# CI/CD 与监控

> 本文区分「已落地」与「建议/规划」：探针、容器化、静态检查已落地；Prometheus 指标与 OpenTelemetry 为后续建议，仓库当前未内置埋点。

## 一、CI（GitHub Actions 示例）

```yaml
# .github/workflows/ci.yml
name: ci
on: { push: { branches: [main] }, pull_request: {} }
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { python-version: "3.13" }
      - run: uv venv && uv pip install -e ".[dev]"
      - run: .venv/bin/ruff check backend/app backend/tests
      - run: .venv/bin/mypy backend/app
      - run: .venv/bin/pytest -m "not integration"
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
        working-directory: frontend
      - run: npm test && npm run lint && npm run build
        working-directory: frontend
  integration:
    runs-on: ubuntu-latest          # 需要中间件：起 compose 跑集成
    steps:
      - uses: actions/checkout@v4
      - run: docker compose up -d mysql redis kafka minio
      # 迁移 + 集成测试（按需补充 init 步骤）
```

## 二、CD（镜像 + 部署）

- tag 语义化 `vX.Y.Z` → 构建后端/前端镜像 → push 到私有 registry。
- 镜像 tag = `{app}-{git_sha}`，保证可回滚。
- 单机：`docker compose -f compose.cloud.yml up -d --build`。
- K8s（可选）：按 `compose.cloud.yml` 的镜像与环境变量编排；密钥走 Secret，探针用 `/healthz` + `/readyz`。
- 发布前强制通过 `docs/04-开发指南/03-上线Checklist.md`。

## 三、健康检查与可观测性

**已落地**：

| 端点 | 用途 | 行为 |
| --- | --- | --- |
| `GET /healthz` | liveness | 进程存活 + 版本/环境 |
| `GET /readyz` | readiness | 并发探测 DB / Redis / MinIO；DB 失败 503；Redis/MinIO 失败标记 `degraded` |

**建议埋点（当前未内置）**：`task_total`、任务成功率、节点耗时、Agent 异常数、
DQ 阻断/特征工程触发次数、任务池积压。接入 `prometheus-fastapi-instrumentator` 或
OpenTelemetry 后再启用告警规则。

## 四、Trace（建议）

- `request_id` 已贯穿 HTTP 请求与错误响应；如需跨 Agent 链路 Trace，建议接入 OpenTelemetry。
- 关键路径：任务下发 → DAG 推进 → Agent 执行 → 资产发布。

## 五、告警通道（建议）

- Prometheus Alertmanager → 钉钉/企业微信 webhook → 值班群。
- 分级：`crit`（阻断性，如 DB 不可用、成功率暴跌）→ 立即通知；`warn`（趋势性）→ 每日汇总。
- 告警去重：相同告警 30 分钟内合并；恢复自动通知。

## 六、日志规范

- 后端：结构化日志到 stdout（建议 JSON），采集进 ELK/Loki。
- 审计日志：独立表（追加写）+ `checksum` 校验；归档策略按合规要求配置。
- 禁止敏感字段进日志（脱敏后输出）；登录日志不得记录口令/验证码。

## 七、故障演练（季度）

| 场景 | 当前实际行为 | 通过标准 |
| --- | --- | --- |
| 停 MinIO | 资产写本地盘（`backend/data/artifacts/`），**不自动回补** | 服务可继续出报告；恢复后人工归档本地副本 |
| 停 Redis | 验证码/登录限流降级内存；事件总线降级进程内广播 | 单实例登录与任务正常 |
| 停 MySQL | `/readyz` 503；调度无法推进 | 恢复后任务由租约扫描自愈 |
| 杀后端进程 | 租约过期 → 其它 worker 接管；成功节点不重跑 | 任务最终到终态 |
