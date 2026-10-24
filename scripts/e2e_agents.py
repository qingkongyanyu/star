"""端到端 12-Agent 验收脚本（真实后端 + Docker 中间件）。

用法（后端已启动在 8070）：

    ./.venv/Scripts/python.exe scripts/e2e_agents.py
    ./.venv/Scripts/python.exe scripts/e2e_agents.py --keep-going
    ./.venv/Scripts/python.exe scripts/e2e_agents.py --base-url http://127.0.0.1:8070

凭据优先级：--username/--password → E2E_USERNAME/E2E_PASSWORD 环境变量 →
backend/.env 的 BOOTSTRAP_ADMIN_*。脚本内不内置任何默认账号口令。

脚本做的事（全部走真实 HTTP + 真实 MySQL/MinIO/数据源，不 mock）：
1. 账号登录（自动读取验证码 SVG 中的字符，仅用于本地验收）；
2. 按场景下发分析任务（MySQL 演示库 / 电商 CSV / 房源 CSV / 脏数据 CSV / API / Kafka）；
3. 轮询任务直到终态，收集每个 DAG 节点的 Agent/状态/产出资产；
4. 拉取产出资产内容做真实性校验（非空 + 关键数字可解析）；
5. 汇总 12 个 Agent 的覆盖情况：planner(编排)、metric_definition、data_profiler、
   sql_agent、data_quality、feature_engineer、visual_agent、insight_analysis、
   report_scribe、analyst_review、memory_keeper(复盘)、trace_navigator。

退出码：0 = 所有场景通过且 12 个 Agent 全部被验证；1 = 有失败。
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _read_backend_env() -> dict[str, str]:
    """读取 backend/.env（仅用于本地验收凭据，不做完整 dotenv 解析）。"""
    values: dict[str, str] = {}
    try:
        for raw in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """解析登录凭据；未提供则明确报错，绝不回退到内置演示账号。"""
    env = _read_backend_env()
    username = (
        args.username
        or os.getenv("E2E_USERNAME")
        or os.getenv("BOOTSTRAP_ADMIN_USERNAME")
        or env.get("BOOTSTRAP_ADMIN_USERNAME")
    )
    password = (
        args.password
        or os.getenv("E2E_PASSWORD")
        or os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
        or env.get("BOOTSTRAP_ADMIN_PASSWORD")
    )
    return username or "", password or ""

# 12 个 Agent（能力标签 → 展示名），覆盖判定用
AGENTS: dict[str, str] = {
    "planner": "天枢_调度",
    "metric_definition": "素问_指标定义",
    "metadata_profiling": "观澜_数据探查",
    "sql_extract": "灵枢_SQL抽取",
    "quality_check": "明鉴_数据质检",
    "feature_engineer": "炼金_特征工程",
    "visualization": "丹青_可视化",
    "insight_analysis": "天机_洞察分析",
    "report_writing": "文澜_报告编撰",
    "review": "洛璃_终审",
    "memory_management": "忆安_记忆管理",
    "trace": "闻道_溯源",
}

# 任务级终态
TERMINAL = {"SUCCESS", "FAILED", "ABORTED"}


@dataclass
class Scenario:
    name: str
    data_source_id: int
    requirement: str
    expect_agents: set[str] = field(default_factory=set)
    allow_failed: bool = False
    min_figures: int = 1
    min_tables: int = 2
    min_datasets: int = 1
    expect_chart_any: set[str] = field(default_factory=set)
    expect_sections: tuple[str, ...] = ()
    forbid_text: tuple[str, ...] = ()
    require_month_axis: bool = False


@dataclass
class TaskReport:
    scenario: Scenario
    task_id: str = ""
    status: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    asset_types: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    retrospective: bool = False
    chart_types: set[str] = field(default_factory=set)
    figure_numbers: list[int] = field(default_factory=list)
    table_numbers: list[int] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    dataset_count: int = 0
    line_categories: list[str] = field(default_factory=list)


class E2E:
    def __init__(self, base_url: str, username: str, password: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=60.0)
        self.username = username
        self.password = password
        self.task_timeout = timeout
        self.token = ""
        self.temp_sources: list[int] = []

    # ---- 基础 ----
    def login(self) -> None:
        cap = self.client.get("/api/v1/auth/captcha").json()
        code = "".join(re.findall(r"<text[^>]*>([^<])</text>", cap["svg"]))
        if len(code) < 4:
            raise RuntimeError(f"无法从验证码 SVG 解析验证码字符：{cap['svg'][:200]}")
        resp = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "captcha_id": cap["captcha_id"],
                "captcha_code": code,
            },
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {self.token}"

    def data_sources(self) -> dict[int, dict[str, Any]]:
        resp = self.client.get("/api/v1/data-sources")
        resp.raise_for_status()
        return {item["id"]: item for item in resp.json()}

    def upload_csv(self, name: str, content: str) -> int:
        """上传 CSV 并返回新建数据源 id（脏数据场景用）。"""
        files = {"file": (f"{uuid.uuid4().hex[:6]}.csv", content.encode("utf-8"), "text/csv")}
        data = {"name": name}
        resp = self.client.post("/api/v1/data-sources/upload", files=files, data=data)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("data_source"):
            ds_id = int(body["data_source"]["id"])
            self.temp_sources.append(ds_id)
            return ds_id
        # 兼容旧契约（返回 DataSourceTestOut）：回查名称
        for ds in self.data_sources().values():
            if ds["name"] == name:
                return int(ds["id"])
        raise RuntimeError(f"上传成功但无法定位数据源 id：{body}")

    def create_api_source(self, name: str, url: str) -> int:
        """注册并验证一个 API 数据源（指向脚本内置的本地 mock 服务）。"""
        resp = self.client.post(
            "/api/v1/data-sources",
            json={"name": name, "type": "api", "config": {"url": url, "method": "GET"}},
        )
        resp.raise_for_status()
        ds_id = int(resp.json()["id"])
        tested = self.client.post(f"/api/v1/data-sources/{ds_id}/test").json()
        if not tested.get("ok"):
            raise RuntimeError(f"API 数据源连接测试失败：{tested.get('message')}")
        self.temp_sources.append(ds_id)
        return ds_id

    def delete_data_source(self, ds_id: int) -> None:
        try:
            self.client.delete(f"/api/v1/data-sources/{ds_id}")
        except Exception:  # noqa: BLE001,S110 - 清理失败不影响验收结论
            pass

    def working_sql_source(self, sources: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
        """挑一个真正连得通的 MySQL 数据源（库内可能有历史遗留的失效配置）。"""
        for ds in sources.values():
            if ds["type"] != "mysql":
                continue
            try:
                tested = self.client.post(f"/api/v1/data-sources/{ds['id']}/test", timeout=30.0).json()
            except Exception:  # noqa: BLE001,S112
                continue
            if tested.get("ok"):
                return ds
        return None

    # ---- 任务 ----
    def submit(self, scenario: Scenario) -> str:
        resp = self.client.post(
            "/api/v1/tasks",
            json={
                "message": scenario.requirement,
                "task_type": "analysis",
                "priority": 5,
                "data_source_id": scenario.data_source_id,
            },
            headers={"Idempotency-Key": f"e2e-{uuid.uuid4().hex}"},
        )
        resp.raise_for_status()
        return str(resp.json()["task_id"])

    def wait(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.task_timeout
        while time.time() < deadline:
            resp = self.client.get(f"/api/v1/tasks/{task_id}")
            resp.raise_for_status()
            detail = resp.json()
            if detail["task"]["status"] in TERMINAL:
                return detail
            time.sleep(2)
        raise TimeoutError(f"任务 {task_id} 超过 {self.task_timeout}s 未终态")

    def asset_content(self, asset_id: str) -> str:
        versions = self.client.get(f"/api/v1/assets/{asset_id}/versions").json()
        if not versions.get("items"):
            raise RuntimeError(f"资产 {asset_id} 无版本")
        version = versions["items"][0]["version"]
        download = self.client.get(f"/api/v1/assets/{asset_id}/versions/{version}").json()
        url = download["url"]
        if url.startswith("data:"):
            return ""
        # MinIO 预签名 URL 为绝对地址，用独立 client 拉取（不带平台 Authorization）
        with httpx.Client(timeout=30.0) as raw:
            resp = raw.get(url)
            resp.raise_for_status()
            return resp.text

    def retrospectives(self, task_id: str, attempts: int = 3) -> list[str]:
        """任务成功后的复盘资产（MemoryKeeper 非 DAG 节点，异步于终态写入，故短暂重试）。"""
        for _ in range(attempts):
            resp = self.client.get("/api/v1/assets", params={"type": "retrospective", "page_size": 100})
            resp.raise_for_status()
            found = [a["asset_id"] for a in resp.json()["items"] if task_id in str(a.get("title", ""))]
            if found:
                return found
            time.sleep(2)
        return []

    def fill_retrospectives(self, reports: list[TaskReport], timeout: float = 40.0) -> None:
        """终态后统一补齐复盘资产：MemoryKeeper 写资产晚于任务终态数秒，逐场景短轮询会误判缺失。"""
        pending = [report for report in reports if report.task_id and not report.retrospective]
        if not pending:
            return
        deadline = time.time() + timeout
        while pending and time.time() < deadline:
            try:
                resp = self.client.get(
                    "/api/v1/assets", params={"type": "retrospective", "page_size": 100}
                )
                resp.raise_for_status()
                items = resp.json().get("items") or []
            except Exception:  # noqa: BLE001 - 查询失败按未就绪处理，继续等待
                items = []
            for report in list(pending):
                if any(report.task_id in str(item.get("title", "")) for item in items):
                    report.retrospective = True
                    pending.remove(report)
            if pending:
                time.sleep(2)

    # ---- 场景执行 ----
    def run(self, scenario: Scenario) -> TaskReport:
        report = TaskReport(scenario=scenario)
        try:
            report.task_id = self.submit(scenario)
        except Exception as exc:  # noqa: BLE001 - 汇总为验收问题
            report.problems.append(f"下发失败：{exc}")
            return report
        try:
            detail = self.wait(report.task_id)
        except Exception as exc:  # noqa: BLE001
            report.problems.append(str(exc))
            return report

        report.status = detail["task"]["status"]
        for node in detail["dag"]["nodes"]:
            report.nodes.append(
                {
                    "agent_type": node["agent_type"],
                    "status": node["status"],
                    "output_asset_id": node.get("output_asset_id"),
                }
            )
        if report.status != "SUCCESS" and not scenario.allow_failed:
            report.problems.append(f"任务终态 {report.status}（期望 SUCCESS）")
        for expected in scenario.expect_agents:
            if not any(n["agent_type"] == expected for n in report.nodes):
                report.problems.append(f"缺少 Agent 节点：{expected}")

        # 资产真实性：逐个读取产出内容（非空）
        for node in report.nodes:
            asset_id = node.get("output_asset_id")
            if not asset_id:
                continue
            try:
                content = self.asset_content(asset_id)
            except Exception as exc:  # noqa: BLE001
                report.problems.append(f"资产 {asset_id}（{node['agent_type']}）不可读：{exc}")
                continue
            if not content.strip():
                report.problems.append(f"资产 {asset_id}（{node['agent_type']}）内容为空")
            if node["agent_type"] == "sql_extract":
                payload = json.loads(content)
                datasets = payload.get("datasets") or ([payload["dataset"]] if payload.get("dataset") else [])
                usable = [item for item in datasets if item.get("rows") or item.get("points")]
                if not usable:
                    report.problems.append("取数产出无 datasets/rows/points（未取到真实数据）")
                report.dataset_count = max(report.dataset_count, len(usable))
                for item in datasets:
                    report.asset_types.append(str(item.get("dim_label") or ""))
            if node["agent_type"] == "visualization":
                payload = json.loads(content)
                charts = payload.get("charts") or []
                report.chart_types.update(str(chart.get("type")) for chart in charts if chart.get("type"))
                for chart in charts:
                    if chart.get("type") == "line":
                        report.line_categories = [
                            str(item) for item in ((chart.get("data") or {}).get("categories") or [])
                        ]
                if scenario.require_month_axis:
                    months = [item for item in report.line_categories if re.fullmatch(r"\d{4}-\d{2}", item)]
                    if len(months) < 6:
                        report.problems.append(
                            f"月度趋势未按月聚合（折线轴：{report.line_categories[:6]}）"
                        )
                if not charts:
                    report.problems.append("可视化产出无 charts")
            if node["agent_type"] == "report_writing":
                payload = json.loads(content)
                text = payload.get("report") or ""
                report.sections = re.findall(r"^##\s+(.+)$", text, re.MULTILINE)
                report.figure_numbers = [int(number) for number in re.findall(r"!\[图\s*(\d+)", text)]
                report.table_numbers = [int(number) for number in re.findall(r"\*\*表\s*(\d+)", text)]
                if "## " not in text or not any("关键结论" in section for section in report.sections):
                    report.problems.append("报告缺少标准章节（关键结论）")
                if len(report.table_numbers) < scenario.min_tables:
                    report.problems.append(
                        f"报告数据表不足：{len(report.table_numbers)} 张（要求 ≥{scenario.min_tables}）"
                    )
                if len(report.figure_numbers) < scenario.min_figures:
                    report.problems.append(
                        f"报告图表引用不足：{len(report.figure_numbers)} 个（要求 ≥{scenario.min_figures}）"
                    )
                for label, numbers in (("图", report.figure_numbers), ("表", report.table_numbers)):
                    if len(numbers) != len(set(numbers)):
                        report.problems.append(f"报告{label}号重复：{numbers}")
                    elif numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
                        report.problems.append(f"报告{label}号不连续：{numbers}")
                for section in scenario.expect_sections:
                    if not any(section in title for title in report.sections):
                        report.problems.append(f"报告章节未体现「{section}」意图：{report.sections}")
                if scenario.expect_chart_any and not (scenario.expect_chart_any & report.chart_types):
                    report.problems.append(
                        f"图表类型未命中 {sorted(scenario.expect_chart_any)}：{sorted(report.chart_types) or '无'}"
                    )
                for forbidden in scenario.forbid_text:
                    if forbidden in text:
                        report.problems.append(f"报告出现不应存在的口径：{forbidden}")
                if not re.search(r"\d", text):
                    report.problems.append("报告不含任何数字（疑似空壳）")
                export_md = payload.get("report_export_md") or ""
                if export_md and "data:image/png;base64" not in export_md:
                    report.problems.append("报告导出未内嵌图表 PNG")
        if report.dataset_count < scenario.min_datasets:
            report.problems.append(
                f"数据集不足：{report.dataset_count} 个（要求 ≥{scenario.min_datasets}）"
            )

        try:
            report.retrospective = bool(self.retrospectives(report.task_id))
        except Exception as exc:  # noqa: BLE001
            report.problems.append(f"复盘资产查询失败：{exc}")
        return report


DIRTY_CSV = """渠道,销售额,订单数
APP,12000.5,120
APP,8000,90
小程序,,80
小程序,1500,12
门店,9800,66
门店,,40
客服,0,0
客服,0,3
"""

# 本地 mock API（验证 api 型数据源真实取数；不依赖外网）
MOCK_RECORDS: dict[str, Any] = {
    "data": [
        {"区域": "华东", "销售额": 42000.0, "订单数": 105},
        {"区域": "华东", "销售额": 31000.0, "订单数": 88},
        {"区域": "华东", "销售额": 18000.0, "订单数": 52},
        {"区域": "华南", "销售额": 36000.0, "订单数": 97},
        {"区域": "华南", "销售额": 22500.0, "订单数": 61},
        {"区域": "华南", "销售额": 12000.0, "订单数": 33},
        {"区域": "华北", "销售额": 27500.0, "订单数": 74},
        {"区域": "华北", "销售额": 15800.0, "订单数": 45},
        {"区域": "华北", "销售额": 9200.0, "订单数": 28},
        {"区域": "西南", "销售额": 16800.0, "订单数": 49},
        {"区域": "西南", "销售额": 7400.0, "订单数": 22},
        {"区域": "西南", "销售额": 3100.0, "订单数": 9},
    ]
}


class _MockApiHandler(http.server.BaseHTTPRequestHandler):
    """返回固定 JSON 记录，供 api 型数据源取数。"""

    def do_GET(self) -> None:
        body = json.dumps(MOCK_RECORDS, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """静默访问日志。"""


def start_mock_api() -> tuple[str, http.server.ThreadingHTTPServer]:
    """启动本地 mock API，返回 (url, server)。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockApiHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/records", server


def build_scenarios(
    sources: dict[int, dict[str, Any]], e2e: E2E, mock_api_url: str
) -> list[Scenario]:
    """按库中真实存在的数据源组装场景（缺失则跳过对应场景）。"""
    scenarios: list[Scenario] = []

    def file_source(*keywords: str) -> dict[str, Any] | None:
        """按名称/文件名关键字挑文件型数据源（避免误用行数最大的那份数据）。"""
        for ds in sources.values():
            if ds["type"] != "file":
                continue
            cfg = ds.get("config") or {}
            haystack = f"{ds['name']} {cfg.get('file_name') or ''}"
            if any(keyword in haystack for keyword in keywords):
                return ds
        return None

    mysql_ds = e2e.working_sql_source(sources)
    ecommerce = file_source("电商", "订单") or next(
        (ds for ds in sources.values() if ds["type"] == "file"), None
    )
    housing = file_source("房源") or ecommerce

    if mysql_ds is not None:
        scenarios.append(
            Scenario(
                name="MySQL·区域销售额对比",
                data_source_id=mysql_ds["id"],
                requirement="按区域统计订单销售额，对比各区域表现",
                expect_agents={
                    "metric_definition",
                    "metadata_profiling",
                    "sql_extract",
                    "quality_check",
                    "visualization",
                    "report_writing",
                    "review",
                },
                min_figures=2,
                expect_chart_any={"bar", "bar_horizontal"},
            )
        )
    if ecommerce is not None:
        scenarios.append(
            Scenario(
                name="CSV·月度走势（趋势意图）",
                data_source_id=ecommerce["id"],
                requirement="分析 2025 年各月实付金额的走势",
                expect_agents={
                    "metric_definition",
                    "metadata_profiling",
                    "sql_extract",
                    "quality_check",
                    "visualization",
                    "report_writing",
                    "review",
                },
                min_figures=2,
                expect_chart_any={"line"},
                expect_sections=("趋势",),
                require_month_axis=True,
            )
        )
        scenarios.append(
            Scenario(
                name="CSV·大区对比 + 溯源",
                data_source_id=ecommerce["id"],
                requirement="对比各大区的销售额，并溯源根因",
                expect_agents={
                    "metric_definition",
                    "sql_extract",
                    "quality_check",
                    "visualization",
                    "report_writing",
                    "review",
                    "trace",
                },
                min_figures=3,
                min_datasets=2,
                expect_chart_any={"waterfall"},
                expect_sections=("贡献",),
            )
        )
        scenarios.append(
            Scenario(
                name="CSV·类目构成（构成意图）",
                data_source_id=ecommerce["id"],
                requirement="各商品类目的销售额占比，找出贡献最高的类目，并分析地区差异",
                expect_agents={"sql_extract", "quality_check", "visualization", "report_writing", "review"},
                min_figures=3,
                min_datasets=2,
                expect_chart_any={"donut", "pie", "treemap"},
                expect_sections=("构成",),
            )
        )
        scenarios.append(
            Scenario(
                name="CSV·双指标相关（散点 + 回归）",
                data_source_id=ecommerce["id"],
                requirement="销售额和订单量有什么关系",
                expect_agents={"sql_extract", "quality_check", "visualization", "report_writing", "review"},
                min_figures=1,
                expect_chart_any={"scatter"},
                expect_sections=("相关",),
            )
        )
        scenarios.append(
            Scenario(
                name="CSV·分布（直方图）",
                data_source_id=ecommerce["id"],
                requirement="各区域销售额的分布情况",
                expect_agents={"sql_extract", "quality_check", "visualization", "report_writing", "review"},
                min_figures=2,
                expect_chart_any={"bar", "bar_horizontal"},
                expect_sections=("分布",),
            )
        )
        scenarios.append(
            Scenario(
                name="CSV·贡献归因（瀑布图）",
                data_source_id=ecommerce["id"],
                requirement="对比各区域的销售额，并分析贡献与根因",
                expect_agents={"sql_extract", "quality_check", "visualization", "report_writing", "review"},
                min_figures=3,
                min_datasets=2,
                expect_chart_any={"waterfall"},
                expect_sections=("贡献",),
            )
        )
        scenarios.append(
            Scenario(
                name="CSV·用户点名饼图",
                data_source_id=ecommerce["id"],
                requirement="用饼图展示各区域的销售额构成",
                expect_agents={"sql_extract", "quality_check", "visualization", "report_writing", "review"},
                min_figures=2,
                expect_chart_any={"pie", "donut"},
                expect_sections=("构成",),
            )
        )
    if housing is not None:
        scenarios.append(
            Scenario(
                name="CSV·平均单价（AVG 口径）",
                data_source_id=housing["id"],
                requirement="各区域平均单价对比，找出最高与最低",
                expect_agents={
                    "metric_definition",
                    "sql_extract",
                    "quality_check",
                    "visualization",
                    "report_writing",
                    "review",
                },
                min_figures=2,
                expect_chart_any={"bar", "bar_horizontal"},
                forbid_text=("单价合计",),
            )
        )
    api_id = e2e.create_api_source(f"e2e-api-{uuid.uuid4().hex[:6]}", mock_api_url)
    scenarios.append(
        Scenario(
            name="API·JSON 取数（本地 mock）",
            data_source_id=api_id,
            requirement="对比各区域的销售额",
            expect_agents={"sql_extract", "quality_check", "visualization", "report_writing", "review"},
            min_figures=2,
            expect_chart_any={"bar", "bar_horizontal"},
        )
    )

    # 脏数据：空值/零值 → 数据质量告警 → 触发 DQ→FE 清洗分支（覆盖 feature_engineer）
    dirty_id = e2e.upload_csv(f"e2e-脏数据-{uuid.uuid4().hex[:6]}", DIRTY_CSV)
    scenarios.append(
        Scenario(
            name="CSV·脏数据（DQ→特征工程）",
            data_source_id=dirty_id,
            requirement="统计各渠道的销售额",
            expect_agents={
                "sql_extract",
                "quality_check",
                "feature_engineer",
                "visualization",
                "report_writing",
                "review",
            },
            min_figures=2,
        )
    )
    for scenario in scenarios:
        scenario.expect_agents.add("insight_analysis")
    return scenarios


def print_report(reports: list[TaskReport], covered: set[str]) -> None:
    print("\n" + "=" * 96)
    print("任务结果")
    print("=" * 96)
    for report in reports:
        mark = "PASS" if not report.problems else "FAIL"
        print(f"[{mark}] {report.scenario.name} · 任务 {report.task_id or '-'} · {report.status or '-'}")
        print(
            f"       图型：{'/'.join(sorted(report.chart_types)) or '无'}"
            f" · 图 {report.figure_numbers} · 表 {report.table_numbers}"
            f" · 数据集 {report.dataset_count} 个"
        )
        chain = " → ".join(f"{n['agent_type']}({n['status'][:4]})" for n in report.nodes)
        print(f"       链路：{chain or '（无节点）'}")
        if report.retrospective:
            print("       复盘：已产出 retrospective 资产（MemoryKeeper）")
        for problem in report.problems:
            print(f"       ! {problem}")

    print("\n" + "=" * 96)
    print("12-Agent 覆盖矩阵")
    print("=" * 96)
    for capability, name in AGENTS.items():
        status = "OK  " if capability in covered else "MISS"
        print(f"  [{status}] {name:<12} ({capability})")
    print("=" * 96)


def main() -> int:
    # Windows 控制台默认 GBK，脚本输出含 ▶/中文/emoji；统一 UTF-8，避免 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001,S110 - 某些被重定向的流不支持 reconfigure
                pass

    parser = argparse.ArgumentParser(description="12-Agent 端到端验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8070")
    parser.add_argument("--username", help="登录账号（默认读 E2E_USERNAME 或 backend/.env 的 BOOTSTRAP_ADMIN_USERNAME）")
    parser.add_argument("--password", help="登录口令（默认读 E2E_PASSWORD 或 backend/.env 的 BOOTSTRAP_ADMIN_PASSWORD）")
    parser.add_argument("--timeout", type=float, default=240.0, help="单任务最长等待秒数")
    parser.add_argument("--keep-going", action="store_true", help="有失败场景也继续跑完")
    args = parser.parse_args()

    username, password = _resolve_credentials(args)
    if not username or not password:
        parser.error(
            "缺少登录凭据：请传 --username/--password，或设置 E2E_USERNAME/E2E_PASSWORD，"
            "或在 backend/.env 配置 BOOTSTRAP_ADMIN_USERNAME/BOOTSTRAP_ADMIN_PASSWORD"
        )

    e2e = E2E(args.base_url, username, password, args.timeout)
    e2e.login()
    print(f"登录成功：{username} @ {args.base_url}")

    mock_url, mock_server = start_mock_api()
    try:
        sources = e2e.data_sources()
        scenarios = build_scenarios(sources, e2e, mock_url)
        print(f"数据源 {len(sources)} 个，验收场景 {len(scenarios)} 个\n")

        reports: list[TaskReport] = []
        covered: set[str] = set()
        for scenario in scenarios:
            print(f"▶ {scenario.name}（数据源 {scenario.data_source_id}）：{scenario.requirement}")
            report = e2e.run(scenario)
            reports.append(report)
            print(
                f"  → {report.status or 'ERROR'}，节点 {len(report.nodes)} 个，"
                f"数据集 {report.dataset_count} 个，图型 {sorted(report.chart_types) or '无'}，"
                f"问题 {len(report.problems)} 项"
            )
            if report.status == "SUCCESS" or scenario.allow_failed:
                covered.update(n["agent_type"] for n in report.nodes if n["status"] == "SUCCESS")
            if report.retrospective:
                covered.add("memory_management")
            # Planner 不是 DAG 节点：任务成功创建即证明其拆解链路可用
            if report.task_id:
                covered.add("planner")
            if report.problems and not args.keep_going:
                print_report(reports, covered)
                return 1

        # MemoryKeeper 异步写复盘资产：等所有任务终态后统一补齐（避免逐场景短轮询误判）
        e2e.fill_retrospectives(reports)

        # 允许跨场景覆盖（如 FE 只在脏数据场景出现）
        for report in reports:
            if report.status == "SUCCESS":
                covered.update(n["agent_type"] for n in report.nodes if n["status"] == "SUCCESS")
            if report.retrospective:
                covered.add("memory_management")
            if report.task_id:
                covered.add("planner")

        print_report(reports, covered)
        failed = [r for r in reports if r.problems]
        missing = [cap for cap in AGENTS if cap not in covered]

        # 简报 §8.2 的全局验收：≥8 场景、≥5 种图表、存在多数据集任务、章节随意图变化
        all_types = sorted(set().union(*(report.chart_types for report in reports)) if reports else set())
        multi_dataset = [report.scenario.name for report in reports if report.dataset_count >= 2]
        dynamic_sections = {
            section
            for report in reports
            for section in report.scenario.expect_sections
            if any(section in title for title in report.sections)
        }
        global_problems: list[str] = []
        if len(reports) < 8:
            global_problems.append(f"验收场景不足：{len(reports)} 个（要求 ≥8）")
        if len(all_types) < 5:
            global_problems.append(f"图表类型不足：{all_types}（要求 ≥5 种）")
        if not multi_dataset:
            global_problems.append("没有任何场景产出多数据集（要求至少 1 个）")
        if len(dynamic_sections) < 3:
            global_problems.append(f"报告章节未随意图变化：仅命中 {sorted(dynamic_sections)}")

        print("\n" + "=" * 96)
        print("全局验收")
        print("=" * 96)
        print(f"  场景数      : {len(reports)}（要求 ≥8）")
        print(f"  图表类型    : {', '.join(all_types) or '无'}（要求 ≥5 种）")
        print(f"  多数据集场景: {', '.join(multi_dataset) or '无'}")
        print(f"  动态章节    : {', '.join(sorted(dynamic_sections)) or '无'}")
        for problem in global_problems:
            print(f"  ! {problem}")

        if missing:
            print(f"\n未被验证的 Agent：{', '.join(missing)}")
        if failed or missing or global_problems:
            print(
                f"\n验收失败：{len(failed)} 个场景有问题，{len(missing)} 个 Agent 未覆盖，"
                f"{len(global_problems)} 项全局要求未达标"
            )
            return 1
        print("\n验收通过：全部场景 SUCCESS，12 个 Agent 均已在真实链路上验证，")
        print("         图表类型 ≥5、报告图号/表号连续、章节随意图动态变化。")
        return 0
    finally:
        # 清理验收期间临时创建的数据源（mock API / 脏数据 CSV）
        for ds_id in e2e.temp_sources:
            e2e.delete_data_source(ds_id)
        mock_server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
