# 星枢 StarPivot · 企业级 12-Agent 智能数据分析平台（商业版）

输入一句分析需求，12 个 Agent 按 DAG 编排完成「意图规划 → 指标定义 → 数据探查 → SQL 取数 → 数据质检 → 可视化 → 洞察分析 → 报告编撰 → 终审」，产出**基于真实数据的图文表并茂分析报告 + 可交互图表 + 洞察发现**。

> 当前版本：**v3.2 商业版**（无内置演示账号、登录防爆破、生产安全基线、异步链路增强）。

## ✨ 核心能力

| 能力 | 说明 |
|---|---|
| **12-Agent 编排** | Planner 调度 + 11 个执行 Agent（指标/探查/SQL/质检/特征/可视化/洞察/报告/终审/记忆/溯源），按确定性 DAG 协作 |
| **数据源接入** | 6 类数据源：MySQL / PostgreSQL / OLAP / CSV 文件 / Kafka 实时流 / 外部 API。全部支持真实取数 → 出报告；管理、测试、结构预览、CSV 上传 |
| **真实数据分析** | SQLAgent 真实执行 SQL / 读 CSV / 请求 API / 消费 Kafka 取数，DataQuality 用真实数据质检，全链路数据透传 |
| **意图驱动分析** | 天枢先识别趋势/对比/排名/构成/分布/异常/相关/归因等意图槽位，再拆成多个取数任务（指标×维度×时间粒度×过滤×TopN），不同需求产出不同分析 |
| **图文表并茂报告** | 报告按意图动态组织章节：关键结论 → 各分析主题（图 + 表 + 解读）→ 数据质量与口径 → 风险建议 → 附录；导出 `.md` 自动内嵌 PNG 图表 |
| **数据质量与口径** | 每个数据集独立质检 + 跨集一致性校验；指标口径带公式/可用性/知识库修正说明；数字 100% 来自真实数据 |
| **任务工作台** | 每个任务独立工作区：数据可视化大屏 / DAG 看板 / 资产仓库 / 记忆检索，支持任务切换与往期回看 |
| **Agent 与指标知识库** | 12 个 Agent 的职责/能力/提示词可查看；管理员维护指标口径，Agent 定义指标时优先采用 |
| **表级 ACL** | 取数前强制校验：角色或用户 × 表 × 操作，拒绝优先 |
| **数据大屏** | 平台级 KPI 总览（任务量/成功率/质量雷达/Agent 健康墙） |

> 12 个 Agent 拟人化命名：天枢_调度 · 素问_指标定义 · 观澜_数据探查 · 灵枢_SQL抽取 · 明鉴_数据质检 · 炼金_特征工程 · 丹青_可视化 · 天机_洞察分析 · 文澜_报告编撰 · 洛璃_终审 · 忆安_记忆管理 · 闻道_溯源

## 🏗 架构

```
前端 React + antd + ECharts (5173)
后端 FastAPI + SQLAlchemy (8070)
中间件 Docker Compose：MySQL / Redis / Kafka / MinIO
数据流：需求 → DAG 拆解 → 节点执行 → 资产发布(MinIO + MySQL 索引) → 报告/图表
```

- **双库隔离**：`task_repo`（任务/节点/记忆/数据源配置）、`artifact_repo`（资产索引），均在 MySQL
- **业务数据源**：独立于平台库，通过「数据源接入」连接（MySQL/PostgreSQL/OLAP/CSV/Kafka/API 六类）
- **异步执行**：API/调度/Agent 全 async；同步数据库驱动经线程池桥接；验证码与登录限流使用异步 Redis；节点输入资产并发拉取
- **可观测性**：`/healthz` 存活探针、`/readyz` 就绪探针（并发探测 DB / Redis / MinIO）

## 🚀 快速开始（本地开发）

### 1. 环境
- Docker Desktop + WSL2（中间件）
- Python 3.13 + [uv](https://docs.astral.sh/uv/)（后端）
- Node.js + npm（前端）

### 2. 初始化配置
```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env：
#   1) 配置 LLM_BASE_URL / LLM_API_KEY（可选，未配置时走确定性兜底）
#   2) 首次启动设置 BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_PASSWORD
#      或留空，启动后执行 CLI 创建管理员：
#      cd backend && python -m app.cli create-admin --username admin
```

> 平台**不再内置任何默认账号**，也不会自动创建演示管理员。口令需 ≥10 位且至少包含
> 大写/小写/数字/符号中的三类。

### 3. 启动
```bash
bash scripts/start_dev.sh

# 前端  http://localhost:5173
# 后端  http://localhost:8070/docs（本地开发环境）
# 探针  http://localhost:8070/healthz · /readyz
```

> `start_dev.sh` 幂等建库建用户并执行 Alembic 迁移，启动时检查管理员账号。
> 改后端代码后建议手动重启：
> `taskkill /F /IM python.exe` 后 `cd backend && nohup ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8070 &`

### 4. 演示一条完整链路
1. **数据源接入** → 选择/新增数据源（docker MySQL `sales_demo` 的 `orders`，或上传 CSV：湛江房源/本地演示 CSV），点「测试连接」
2. **任务看板** → 选数据源 → 输入需求（如「分析订单各区域销售额」）→ 下发
3. **任务工作台** → 数据可视化大屏看真实图表 + 报告；资产仓库下载

### 5. 12-Agent 端到端验收（可复跑）
```bash
# 后端启动后执行；凭据从 --username/--password、E2E_USERNAME/E2E_PASSWORD
# 或 backend/.env 的 BOOTSTRAP_ADMIN_* 读取
./.venv/Scripts/python.exe scripts/e2e_agents.py --keep-going
```
脚本会登录、下发 **11 个场景**任务（MySQL/CSV/API/脏数据 × 趋势/对比/构成/分布/相关/归因/用户点名图表），
轮询到终态、读取每个 Agent 的产出资产做真实性校验，并断言：12-Agent 全覆盖、图表类型 ≥5 种、
报告图号/表号连续、章节随意图动态变化；退出码 0 表示全部通过。

## 🏭 商业版部署
```bash
cp .env.cloud.example .env
# 编辑 .env：强 JWT_SECRET、LLM Key、初始管理员、数据库/MinIO 口令、CORS 域名
docker compose -f compose.cloud.yml up -d --build
```
生产模式（`ENV=prod`）下：
- 启动强校验 `JWT_SECRET`（至少 32 位），拒绝模板值；
- 不暴露 `/docs`、`/redoc`、`/openapi.json`；
- 强制显式初始化管理员，缺失则启动失败；
- 追加 HSTS 与严格 CSP 等安全响应头；
- 使用 `/readyz` 做编排就绪判断。

详见 [`docs/云部署上手指南.md`](docs/云部署上手指南.md) 与 [`docs/04-开发指南/03-上线Checklist.md`](docs/04-开发指南/03-上线Checklist.md)。

## 📁 目录结构

```
backend/
  app/
    agents/       # 12 个 Agent（含洞察分析；提示词 + 确定性逻辑）
    api/v1/       # 接口层
    services/     # 用例（任务/DAG/数据源/报告/记忆/调度）
    infra/        # 中间件封装（db/redis/minio/datasource/captcha/login_throttle…）
    repositories/ # 数据访问
    models/       # ORM
    schemas/      # DTO
    tools/        # Agent 工具注册表
    cli.py        # 管理员初始化 / 口令重置 / 管理员列表
  alembic/        # 迁移（task_repo / artifact_repo）
frontend/src/
  pages/          # 页面（登录/群聊/任务看板/任务工作台/大屏/数据源/资产/知识库…）
  components/     # DagGraph / MarkdownView / useECharts…
  config/         # agents.ts（12 Agent 头像/能力）、assets.ts
  stores/         # zustand（auth/chat/ws）
  api/            # 接口封装
scripts/          # start_dev.sh / check_health.sh / e2e_agents.py
Data/             # 演示 CSV（E2E 与手工演示）
docs/             # 架构 / 后端 / 前端 / 开发 / DevOps 文档
```

## 📚 文档

- [`docs/README.md`](docs/README.md)：完整文档地图
- [`docs/项目架构书.md`](docs/项目架构书.md)：当前落地架构（12-Agent / 意图计划 / 多任务取数 / 图表引擎 / 报告 / 工作台 / ACL / 知识库）
- [`docs/功能与使用指南.md`](docs/功能与使用指南.md) / [`docs/项目详细使用说明书.md`](docs/项目详细使用说明书.md)：功能与运维操作
- [`docs/技术栈详细说明书.md`](docs/技术栈详细说明书.md)：前后端技术栈与依赖版本
- [`docs/变更与上线记录.md`](docs/变更与上线记录.md)：v2 → v3.2 变更与回归清单
- [`docs/审查与修复报告-2026-10-18.md`](docs/审查与修复报告-2026-10-18.md)：历史审查修复记录（当时 11-Agent，现已扩展为 12-Agent）

## ⚠️ 已知限制

- **ACL 默认策略**：无规则默认允许（只读），生产建议按需收紧为默认拒绝
- **知识库检索**：MySQL LIKE 评分（RAG-lite），未接向量检索
- **报告图表**：应用内用 ECharts 交互渲染；导出 `.md` 时自动把 `chart://` 引用替换为内嵌 PNG（依赖 matplotlib/Pillow）
- **未配置 LLM 时**：终审改走确定性数字校验（报告关键数字与数据集重算一致才 approved），不冒充实 LLM 裁决
- **密钥管理**：本地 `.env` 为明文配置；生产请使用密钥管理服务或容器 Secret，并避免把 `.env` 提交到仓库

## 🛡 安全基线

- **无默认账号**：启动不种子演示账号；管理员通过 `BOOTSTRAP_ADMIN_*` 或 `python -m app.cli` 显式创建
- **JWT + 三档角色**（admin / analyst / readonly）+ 表级 ACL + 细粒度 RBAC 权限点
- **口令策略**：初始管理员与新建用户强制强度校验（argon2 只存哈希）
- **登录防爆破**：账号 + 来源 IP 双维度计数，Redis 多实例共享、不可用时内存兜底，超限返回 429
- **验证码**：一次性、TTL 过期；异步 Redis 存储，Redis 故障自动降级内存
- **生产加固**：强 JWT 校验、文档端点关闭、安全响应头、CORS 白名单
- **数据安全**：数据源凭据存于平台库；连接器仅执行只读 SELECT；取数自动脱敏；资产按 DAG 血缘登记并审计
- **WebSocket 鉴权**：首帧鉴权（token 不进 URL，避免泄露到访问日志）

---

*星枢 StarPivot · 12-Agent 智能数据分析平台*
