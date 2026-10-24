"""数据源连接器：SQL（mysql/postgres/olap）/ CSV / API / Kafka 的测试与结构探查。

职责边界：只做「连接测试 + 表/字段结构读取 + 采样」，不落业务数据、
不改动数据源（与各 Agent 的边界约束一致）。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy import table as sa_table

from app.core.constants import MASK_RULES, SENSITIVE_FIELD_PATTERNS

logger = logging.getLogger(__name__)

_SQL_SCHEMES = {
    "mysql": "mysql+pymysql",
    "postgres": "postgresql+psycopg",
}


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    row_count: int | None = None


# ============ SQL（mysql / postgres / olap）============

def build_sqlalchemy_url(cfg: dict[str, Any]) -> str:
    """由数据源配置拼 SQLAlchemy URL；olap 可直接给 engine_url。"""
    engine_url = (cfg.get("engine_url") or "").strip()
    if engine_url:
        return engine_url
    scheme = _SQL_SCHEMES.get((cfg.get("vendor") or "mysql").lower().split(":")[0], "mysql+pymysql")
    host = cfg.get("host") or "127.0.0.1"
    default_port = 5432 if scheme.startswith("postgresql") else 3306
    try:
        port = int(cfg.get("port") or default_port)
    except (TypeError, ValueError):
        port = default_port
    db = cfg.get("database") or ""
    user = cfg.get("username") or ""
    pwd = cfg.get("password") or ""
    return f"{scheme}://{user}:{quote_plus(pwd)}@{host}:{port}/{db}"


def _engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "pool_recycle": 300}
    if url.startswith("mysql"):
        kwargs["connect_args"] = {"connect_timeout": 8, "read_timeout": 15}
    return create_engine(url, **kwargs)


def sql_test(url: str) -> tuple[bool, str]:
    """连接测试：SELECT 1。"""
    eng = _engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "连接成功"
    except Exception as exc:  # noqa: BLE001  连接失败给出可读原因
        return False, f"连接失败: {exc}"
    finally:
        eng.dispose()


def sql_list_tables(url: str, limit: int = 50) -> list[str]:
    """表清单（上限 limit）；失败返回空列表（与 sql_test 等一致的兜底语义）。"""
    eng = _engine(url)
    try:
        return list(inspect(eng).get_table_names())[:limit]
    except Exception:
        logger.warning("获取表清单失败（sql_list_tables）", exc_info=True)
        return []
    finally:
        eng.dispose()


def _safe_identifier(name: str, fallback: str = "t") -> str:
    """标识符白名单清洗：仅允许常见标识符字符，杜绝注入（表名来自外部参数）。"""
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "", name)
    return cleaned or fallback


def sql_sample_rows(url: str, table: str, limit: int = 50) -> dict[str, Any]:
    """采样单表前 N 行（供指标目录/字段角色识别；失败返回空结构，不阻断规划）。"""
    table = _safe_identifier(table)
    eng = _engine(url)
    try:
        with eng.connect() as conn:
            columns = [str(c["name"]) for c in inspect(eng).get_columns(table)]
            if not columns:
                return {"columns": [], "rows": []}

            def _quote(name: str) -> str:
                return f'"{name}"' if eng.dialect.name == "postgres" else f"`{name}`"

            column_sql = ", ".join(_quote(name) for name in columns)
            result = conn.execute(
                text(f"SELECT {column_sql} FROM {_quote(table)} LIMIT :limit"),
                {"limit": int(limit)},
            )
            rows = [[_json_safe(v) for v in row] for row in result]
        return {"columns": columns, "rows": rows}
    except Exception:
        logger.warning("采样表 %s 失败（sql_sample_rows）", table, exc_info=True)
        return {"columns": [], "rows": []}
    finally:
        eng.dispose()


def sql_schema(url: str, table: str, with_row_count: bool = True) -> TableInfo:
    """单表结构 + 行数估计（COUNT 慢表可省略）。

    表名经白名单清洗后交给 SQLAlchemy table() 构造，跨 MySQL/Postgres 方言都正确引用。
    """
    table = _safe_identifier(table)
    eng = _engine(url)
    try:
        cols = [
            ColumnInfo(name=c["name"], type=str(c["type"]), nullable=bool(c.get("nullable", True)))
            for c in inspect(eng).get_columns(table)
        ]
        row_count: int | None = None
        if with_row_count:
            try:
                with eng.connect() as conn:
                    row_count = int(
                        conn.execute(select(func.count()).select_from(sa_table(table))).scalar_one()
                    )
            except Exception:  # noqa: BLE001  行数失败不影响结构
                row_count = None
        return TableInfo(name=table, columns=cols, row_count=row_count)
    finally:
        eng.dispose()


def _json_safe(value: Any) -> Any:
    """把 DB 返回的非 JSON 原生类型转成可序列化类型（Decimal/日期等）。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    return value


# ============ 敏感字段脱敏（SQLAgent 取数后应用，规格书 mask_result）============

def _mask_value(value: Any, kind: str) -> Any:
    """按类型对单值脱敏：手机号/身份证/邮箱。非字符串原样返回。"""
    if not isinstance(value, str) or not value:
        return value
    pattern = MASK_RULES.get(kind)
    if not pattern:
        return value
    return re.sub(pattern, lambda m: _mask_match(m.group(0), kind), value)


def _mask_match(text: str, kind: str) -> str:
    """对命中片段做脱敏：保留首尾、中间打星。"""
    if kind == "phone":
        return text[:3] + "****" + text[-4:] if len(text) >= 7 else "****"
    if kind == "id_card":
        return text[:4] + "***********" + text[-2:] if len(text) >= 8 else "********"
    if kind == "email":
        local, _, domain = text.partition("@")
        return (local[:2] + "***" + "@" + domain) if domain else "***"
    return "***"


def mask_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """对数据集按字段名自动脱敏（手机号/身份证/邮箱），返回新 dataset（不改入参）。"""
    columns = [str(c) for c in dataset.get("columns") or []]
    rows = dataset.get("rows") or []
    if not columns or not rows:
        return dataset
    # 判定哪些列需要脱敏：字段名命中敏感字段模式
    col_kinds: list[str | None] = []
    for col in columns:
        kind: str | None = None
        low = col.lower()
        for k, pat in SENSITIVE_FIELD_PATTERNS.items():
            if re.search(pat, low):
                kind = k
                break
        col_kinds.append(kind)
    if not any(col_kinds):
        return dataset
    masked_rows: list[list[Any]] = []
    for r in rows:
        row = list(r)
        for idx, kind in enumerate(col_kinds):
            if kind is not None and idx < len(row):
                row[idx] = _mask_value(row[idx], kind)
        masked_rows.append(row)
    out = dict(dataset)
    out["rows"] = masked_rows
    out["masked"] = True
    return out


def sql_execute(url: str, sql: str, limit: int | None = None) -> dict[str, Any] | None:
    """执行只读 SELECT，返回 {columns, rows, row_count, truncated}；非只读单条 SELECT 或失败返回 None。

    聚合取数默认上限 settings.max_query_rows（可配置）；多取 1 行探测截断——返回 truncated=True
    且只保留前 limit 行，下游据此如实标注（绝不让"前 N 行当全量"静默失真）。
    """
    from app.core.config import settings

    limit = limit if limit is not None else settings.max_query_rows
    sql_clean = (sql or "").strip().rstrip(";").strip()
    if not sql_clean:
        return None
    # 剥离行首注释（-- / # / /* ... */），再判定是否为单条 SELECT
    stripped = sql_clean
    for _ in range(16):  # 防循环（多层嵌套注释理论上有限）
        stripped = re.sub(r"^\s*(--[^\n]*\n|#[^\n]*\n|/\*.*?\*/)", "", stripped, flags=re.DOTALL)
        if not stripped.startswith(("--", "#", "/*")):
            break
    head = stripped.lstrip().lower()
    if not head.startswith("select"):
        return None
    # 拒绝多语句：SELECT 之后出现分号即非单条
    if ";" in stripped:
        return None
    eng = _engine(url)
    try:
        with eng.connect() as conn:
            result = conn.execute(text(sql_clean))
            columns = list(result.keys())
            rows = [[_json_safe(v) for v in r] for r in result.fetchmany(limit + 1)]
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
    except Exception:  # 取数失败不影响 SQL 产出
        logger.warning("SQL 执行失败（sql_execute）", exc_info=True)
        return None
    finally:
        eng.dispose()


# ============ CSV / 文件型 ============

# 上传/解析大小与行数上限（防超大文件 OOM；超出诚实报错或截断标注）
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_CSV_ROWS = 200_000  # 单文件解析行数上限（超出拒绝，避免内存爆炸）


def _decode_text(data: bytes) -> str:
    """字节 → 文本，自动识别编码（UTF-8/UTF-16/GBK/GB18030 回退）。

    中文 Windows/Excel 常见 GBK 编码，直接 utf-8 解码会乱码；此处按 BOM →
    UTF-8 严格 → GB18030 兜底 的顺序解码，保证中文 CSV 不乱码。
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        return data.decode("utf-8-sig", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # GB18030 是 GBK 超集，覆盖 GBK/GB2312；仍失败则 latin-1 兜底（不丢字节）
        try:
            return data.decode("gb18030")
        except UnicodeDecodeError:
            return data.decode("latin-1")


def parse_csv(data: bytes) -> dict[str, Any]:
    """解析 CSV：推断列类型 + 采样行 + 行数。UTF-8/UTF-16/GBK/GB18030 编码容错。"""
    text_content = _decode_text(data)
    reader = csv.reader(io.StringIO(text_content))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError("CSV 内容为空")
    header = [h.strip() for h in rows[0]]
    body = rows[1:]

    def _infer(values: list[str]) -> str:
        seen_int = seen_float = seen_str = False
        for v in values[:20]:
            v = v.strip()
            if not v:
                continue
            try:
                int(v)
                seen_int = True
                continue
            except ValueError:
                pass
            try:
                float(v)
                seen_float = True
                continue
            except ValueError:
                pass
            seen_str = True
        if seen_str:
            return "string"
        if seen_float:
            return "float"
        if seen_int:
            return "int"
        return "string"

    columns = [
        {"name": h, "type": _infer([r[i] if i < len(r) else "" for r in body]) if body else "string"}
        for i, h in enumerate(header)
    ]
    return {"columns": columns, "sample_rows": body[:10], "row_count": len(body)}


# ---- 上传文件读取（file 数据源真实取数用）----

_LOCAL_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "data-source"


def _read_uploaded_local(object_key: str) -> bytes | None:
    """本地降级读取上传文件：先按绝对路径，再按 data/data-source 相对候选。"""
    direct = Path(object_key)
    if object_key and direct.exists() and direct.is_file():
        return direct.read_bytes()
    norm = object_key.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    candidates = [
        _LOCAL_UPLOAD_DIR / norm,
        _LOCAL_UPLOAD_DIR / "uploads" / name,
        _LOCAL_UPLOAD_DIR / name,
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.read_bytes()
        except OSError:
            continue
    return None


async def load_uploaded_csv(object_key: str) -> dict[str, Any] | None:
    """读取上传的 CSV 全量内容（本地降级或 MinIO），返回 {columns, rows, row_count}。

    行值为原始字符串；聚合方按需转数值。读不到返回 None（调用方诚实标注，绝不编造数据）。
    """
    import asyncio

    raw = await asyncio.to_thread(_read_uploaded_local, object_key) if object_key else None
    if raw is None and object_key and "/" in object_key:
        try:  # MinIO 路径：bucket/key（上传时 bucket=data-source）
            from app.infra.minio import get_object

            bucket, key = object_key.split("/", 1)
            raw = await get_object(bucket, key)
        except Exception:  # noqa: BLE001  MinIO 不可用/对象不存在 → 回退 None（诚实标注）
            raw = None
    if raw is None:
        return None
    text = _decode_text(raw)
    reader = csv.reader(io.StringIO(text))
    parsed_rows = [r for r in reader if any(cell.strip() for cell in r)]
    if len(parsed_rows) < 1:
        return None
    header = [h.strip() for h in parsed_rows[0]]
    width = len(header)
    body = [r[:width] + [""] * (width - len(r)) for r in parsed_rows[1:]]
    # 行数上限防护：超大 CSV 只保留前 MAX_CSV_ROWS 行，并标注 truncated
    truncated = len(body) > MAX_CSV_ROWS
    if truncated:
        body = body[:MAX_CSV_ROWS]
    return {"columns": header, "rows": body, "row_count": len(body), "truncated": truncated}


# ============ API ============

def _json_type(value: Any) -> str:
    if value is None:
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


async def api_test(cfg: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
    """API 测试：发一次请求；若返回 JSON 推断字段结构。"""
    url = (cfg.get("url") or "").strip()
    if not url:
        return False, "缺少 URL", None
    method = (cfg.get("method") or "GET").upper()
    headers = dict(cfg.get("headers") or {})
    auth_type = cfg.get("auth_type")
    if auth_type == "bearer" and cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    elif auth_type == "basic":
        headers.setdefault("Authorization", f"Basic {cfg.get('api_key') or ''}")
    timeout = httpx.Timeout(10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers)
        if resp.status_code >= 400:
            return False, f"请求失败: HTTP {resp.status_code}", None
        try:
            data = resp.json()
        except ValueError:
            return True, f"HTTP {resp.status_code}（非 JSON，未推断结构）", None
        # 与 api_fetch_records 保持一致：先解包常见记录信封（data/rows/records…），再取首条推断字段，
        # 否则 {"data":[...]} 这类响应会被误判成一列 "data"，指标口径随之退化为 COUNT(data)。
        if isinstance(data, dict):
            for key in ("data", "rows", "records", "items", "list", "result"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            columns = [{"name": k, "type": _json_type(v)} for k, v in data.items()]
            return True, f"HTTP {resp.status_code}，字段 {len(columns)} 个", {"columns": columns}
        return True, f"HTTP {resp.status_code}", None
    except Exception as exc:  # noqa: BLE001
        return False, f"请求异常: {exc}", None


def _record_cell(value: Any) -> Any:
    """记录值 → 可聚合单元格：None/嵌套转空/字符串化，数字保留。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def api_fetch_records(cfg: dict[str, Any], limit: int = 1000) -> dict[str, Any] | None:
    """API 真实取数：GET 返回 JSON（dict 包 list 或 list[dict] 或单 dict），摊平成 {columns, rows}。

    读不到/非结构化返回 None（调用方诚实 no_data，绝不用样例拼数）。
    """
    url = (cfg.get("url") or "").strip()
    if not url:
        return None
    method = (cfg.get("method") or "GET").upper()
    headers = dict(cfg.get("headers") or {})
    auth_type = cfg.get("auth_type")
    if auth_type == "bearer" and cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    elif auth_type == "basic":
        headers.setdefault("Authorization", f"Basic {cfg.get('api_key') or ''}")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
            resp = await client.request(method, url, headers=headers)
        if resp.status_code >= 400:
            return None
        data = resp.json()
    except Exception:  # noqa: BLE001  网络/解析异常统一返回 None
        return None
    if isinstance(data, dict):
        for key in ("data", "rows", "records", "items", "list", "result"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if isinstance(data, dict):
        data = [data]  # 单对象时间序列
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    records = data[:limit]
    cols = [str(k) for k in records[0]]
    rows = [[_record_cell(r.get(c)) for c in cols] for r in records]
    return {"columns": cols, "rows": rows, "row_count": len(rows)}


def _kafka_connect(cfg: dict[str, Any]) -> Any:
    from kafka import KafkaConsumer

    bootstrap = cfg.get("bootstrap_servers") or "127.0.0.1:9092"
    topic = cfg.get("topic")
    if not topic:
        return None
    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        auto_offset_reset="earliest",  # 每任务从头读当前保留消息，行为确定、可核对
        enable_auto_commit=False,       # 不推进消费位点（分析型只读，不抢业务位点）
        consumer_timeout_ms=int(cfg.get("timeout_ms") or 6000),
    )


async def kafka_fetch_records(cfg: dict[str, Any], limit: int = 1000) -> dict[str, Any] | None:
    """Kafka 真实取数：读取 topic 内保留的 JSON 记录（每条消息=一个 dict），摊平成 {columns, rows}。

    限定条数与超时即停（不做无限 tail，配合分析型任务语义）；读不到返回 None。
    """
    import asyncio

    def _poll() -> list[dict[str, Any]] | None:
        consumer = _kafka_connect(cfg)
        if consumer is None:
            return None
        try:
            out: list[dict[str, Any]] = []
            for _msg in consumer:  # consumer_timeout_ms 到期自动停止迭代
                raw = _msg.value
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        raw = raw.decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        raw = ""
                try:
                    obj = json.loads(raw) if isinstance(raw, str) else raw
                except (ValueError, TypeError):
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
                elif isinstance(obj, list):
                    out.extend(o for o in obj if isinstance(o, dict))
                if len(out) >= limit:
                    break
            return out or None
        except Exception:  # noqa: BLE001  Kafka 驱动异常（断连/鉴权等）→ 诚实 None
            return None
        finally:
            try:
                consumer.close()
            except Exception:  # 关闭失败仅调试级提示
                logger.debug("关闭 Kafka consumer 失败", exc_info=True)

    records = await asyncio.to_thread(_poll)
    if not records:
        return None
    records = records[:limit]
    cols = [str(k) for k in records[0]]
    rows = [[_record_cell(r.get(c)) for c in cols] for r in records]
    return {"columns": cols, "rows": rows, "row_count": len(rows)}


# ============ Kafka / 实时流 ============

def kafka_test(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Kafka 测试：bootstrap 建 Admin 客户端列 topic。"""
    bootstrap = cfg.get("bootstrap_servers") or "127.0.0.1:9092"
    try:
        from kafka import KafkaAdminClient
    except Exception as exc:  # noqa: BLE001
        return False, f"kafka 驱动不可用: {exc}"
    admin: KafkaAdminClient | None = None
    try:
        admin = KafkaAdminClient(
            bootstrap_servers=bootstrap,
            request_timeout_ms=8000,
        )
        topics = set(admin.list_topics())
        topic = cfg.get("topic")
        msg = f"连接成功，共 {len(topics)} 个 topic"
        if topic:
            msg += f"，目标 topic「{topic}」" + ("存在" if topic in topics else "不存在")
        return True, msg
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {exc}"
    finally:
        if admin is not None:
            try:
                admin.close()
            except Exception:
                logger.warning("关闭 Kafka Admin 客户端失败", exc_info=True)


# ============ 汇总分发 ============

async def test_connection(ds_type: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    """按类型分发连接测试。"""
    if ds_type in ("mysql", "postgres", "olap"):
        return sql_test(build_sqlalchemy_url(cfg))
    if ds_type == "api":
        ok, msg, _ = await api_test(cfg)
        return ok, msg
    if ds_type == "stream":
        return kafka_test(cfg)
    if ds_type == "file":
        return bool(cfg.get("object_key")), "文件已就绪" if cfg.get("object_key") else "缺少文件"
    return False, f"不支持的数据源类型: {ds_type}"


def schema_for(ds_type: str, cfg: dict[str, Any], table: str | None = None) -> dict[str, Any]:
    """读取结构：SQL 取真实表/字段；file/api 用缓存的结构。"""
    if ds_type in ("mysql", "postgres", "olap"):
        url = build_sqlalchemy_url(cfg)
        if table:
            info = sql_schema(url, table)
            return {"table": info.name, "columns": [c.__dict__ for c in info.columns], "row_count": info.row_count}
        tables = sql_list_tables(url)
        return {"tables": tables}
    if ds_type == "file":
        return {
            "table": cfg.get("file_name", ""),
            "columns": cfg.get("columns") or [],
            "sample_rows": cfg.get("sample_rows") or [],
            "row_count": cfg.get("row_count"),
        }
    if ds_type == "api":
        return {"table": cfg.get("url", ""), "columns": cfg.get("columns") or []}
    return {}
