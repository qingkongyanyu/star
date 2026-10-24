# API 接口契约

> 全部接口 REST + JSON，前缀 `/api/v1`，鉴权用 `Authorization: Bearer <JWT>`。
> OpenAPI 由 FastAPI 自动生成于 `/docs`；`ENV=prod` 时按商业版安全基线关闭文档端点。
> 本文档定义**契约意图**，字段以实际 Pydantic schema 为准。
> 系统探针不在 `/api/v1` 下：`/healthz`（存活）、`/readyz`（DB/Redis/MinIO 就绪）。

## 一、通用约定

- 时间：ISO8601，UTC。
- 分页：`?page=1&page_size=20`，响应 `{ "items": [], "total": n, "page": 1 }`。
- 错误：HTTP 状态码 + 业务错误码：

```json
{ "code": "E_UNAUTHORIZED", "message": "登录已过期，请重新登录", "request_id": "req-xxx" }
```

| HTTP | 业务码 | 含义 |
| --- | --- | --- |
| 401 | `E_UNAUTHORIZED` | 未登录/令牌过期 |
| 403 | `E_FORBIDDEN` | 已登录但无权限（角色/ACL 不通过） |
| 404 | `E_NOT_FOUND` | 资源不存在 |
| 422 | `E_VALIDATION` | 参数校验失败 |
| 409 | `E_CONFLICT` | 状态冲突（如节点已被认领） |
| 429 | `E_RATELIMIT` | 限流 |
| 500 | `E_INTERNAL` | 内部错误 |

## 二、认证 `auth`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/auth/captcha` | 获取一次性登录验证码（SVG + captcha_id，TTL 300s） | 公开 |
| POST | `/auth/login` | 账号密码 + 验证码登录，返回 access_token + 用户信息 | 公开 |
| POST | `/auth/logout` | 注销（无状态 JWT，前端丢弃 token） | 登录 |
| GET | `/auth/me` | 当前用户 + 角色 + 权限点 | 登录 |

```json
// GET /auth/captcha
{ "captcha_id": "9f...", "svg": "<svg .../>", "expires_in": 300 }

// POST /auth/login
{ "username": "zhangsan", "password": "******",
  "captcha_id": "9f...", "captcha_code": "a2Cd" }
// 响应
{ "access_token": "eyJ...", "token_type": "bearer", "user": {
    "id": 2, "username": "zhangsan", "role": "analyst", "display_name": "张三" } }
```

失败语义：
- 验证码/口令错误统一返回 401（不区分账号是否存在）；
- 账号或来源 IP 连续失败达到阈值返回 `429 E_RATELIMIT`，message 含剩余秒数；
- 登录成功后失败计数清零。

## 三、任务 `tasks`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| POST | `/tasks` | 下发任务（群聊消息 → 任务池） | 分析师+ |
| GET | `/tasks` | 任务列表（状态筛选） | 登录 |
| GET | `/tasks/{task_id}` | 任务详情 + DAG | 登录 |
| GET | `/tasks/{task_id}/dag` | DAG 图数据（节点/边/状态） | 登录 |
| GET | `/nodes/{node_id}` | 节点详情（含日志） | 登录 |
| POST | `/tasks/{task_id}/override` | 人工干预（block/allow/rerun/abort） | 管理员 |

```json
// GET /tasks/{id}/dag
{ "task_id": "t88", "status": "RUNNING",
  "nodes": [
    { "node_id": "n1", "agent_type": "sql_extract", "status": "SUCCESS",
      "label": "数据抽取", "output_asset_id": "a1" }
  ],
  "edges": [ { "from": "n0", "to": "n1", "type": "data_dep" } ] }
```

## 四、资产 `assets`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/assets` | 资产列表（类型/关键字/分页） | 登录 |
| GET | `/assets/{asset_id}` | 资产详情 | 登录 |
| GET | `/assets/{asset_id}/versions` | 版本列表 | 登录 |
| GET | `/assets/{asset_id}/versions/{v}` | 下载/预览（对象内容） | 登录 |
| GET | `/assets/{asset_id}/lineage` | 血缘链 | 登录 |

## 五、Agent `agents`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/agents` | Agent 列表 + 实时状态 | 登录 |
| GET | `/agents/{id}/config` | 某 Agent 配置（模型/阈值） | 管理员 |
| PUT | `/agents/{id}/config` | 更新配置 | 管理员 |
| POST | `/agents/{id}/inquiry` | 质询（对应 TraceNavigator @问责） | 分析师+ |

## 六、记忆 `memory`（经 MemoryKeeper）

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/memory/search` | `?q=&scope=private|collective&agent=&top_k=` | 管理员 / 属主 |
| GET | `/memory/retrospectives` | 复盘纪要列表 | 管理员 |

**权限强约束**：`scope=private` 且 `agent != 当前用户属主` → 403。

## 七、溯源 `trace`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/trace/{asset_id}` | 血缘 + 归因报告 | 分析师+ |
| GET | `/trace/{asset_id}/responsibility` | 责任人清单 | 分析师+ |

## 八、大屏 `dashboard`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/dashboard/summary` | KPI：任务总量/成功率/DQ阻断率/FE触发率/Agent健康 | 登录 |
| GET | `/dashboard/trends` | 近 14 日任务趋势 | 登录 |
| GET | `/dashboard/quality` | 数据质量雷达 | 登录 |

## 九、管理 `admin`

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/admin/users` | 用户列表 | 管理员 |
| POST | `/admin/users` | 新增用户 | 管理员 |
| PUT | `/admin/users/{id}` | 改角色/停用 | 管理员 |
| GET | `/admin/acl` | 表级 ACL 列表 | 管理员 |
| PUT | `/admin/acl` | 更新 ACL | 管理员 |
| GET | `/admin/kb` | 指标知识库条目 | 管理员 |
| POST | `/admin/kb` | 新增指标条目 | 管理员 |
| GET | `/admin/audit` | 审计日志（分页，只读） | 管理员 |

## 十、WebSocket（协同）

- 端点：`/ws/chat?token=<JWT>`（握手校验）
- 上行：`{ "type": "task.submit", "message": "..." }`、`{ "type": "agent.inquiry", "target": "dq", "message": "..." }`
- 下行事件（镜像 `task.events`，见 05-消息事件契约）：

```json
{ "type": "artifact.published", "data": { "node_id": "n1", "asset_id": "a1", "version": 3 } }
```

## 十一、实现约定

- 路由函数只做：取 DTO → 调 service → 转响应 schema；禁止写业务。
- 每个 service 方法抛 `AppError(code, message)`，全局异常处理器统一转 `{code,message}`。
- 审计钩子：任何写操作（任务/资产/ACL/人工干预）在 service 提交后追加 `audit_log`。
- 幂等：`POST /tasks` 支持 `Idempotency-Key` 头，重复提交返回原 task_id。

---

## v1.1 新增端点（2026-08-19）

### 数据源 `/data-sources`（管理部分仅 admin，options 所有登录用户）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /data-sources | 数据源列表（含 config，admin） |
| POST | /data-sources | 新增数据源 |
| GET | /data-sources/options | **脱敏选项**（id/name/type/status，任务表单用，无密码） |
| POST | /data-sources/upload | CSV 上传注册（multipart） |
| PUT | /data-sources/{id} | 重命名 |
| POST | /data-sources/{id}/test | 测试连接（返回表清单/列） |
| GET | /data-sources/{id}/tables | 表清单（SQL 型） |
| GET | /data-sources/{id}/schema?table= | 表结构 |
| DELETE | /data-sources/{id} | 删除 |

### 任务工作台
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /tasks/{task_id}/assets | 该任务按 DAG 顺序的资产 |
| GET | /tasks/{task_id}/memory | 该任务（source_task）的记忆 |

### Agent 知识库
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /agents/kb | 12 个 Agent 职责 + 系统提示词 + 产出类型 |

### 系统管理（admin）v1.1
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /admin/acl | 新增 ACL 规则 |
| PUT | /admin/acl/{rule_id} | 更新 ACL 规则 |
| DELETE | /admin/acl/{rule_id} | 删除 ACL 规则 |
| PUT | /admin/kb/{kb_id} | 更新知识库口径 |
| DELETE | /admin/kb/{kb_id} | 删除知识库口径 |

### 事件增强（WS 聊天流）
- `artifact.published` 新增 `agent_type` + `title`：各 Agent 以自身身份在群聊发言
- `task.created` 新增 `node_count`
