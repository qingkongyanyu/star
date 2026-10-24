# 状态管理与 API 对接

> 全局状态用 Zustand；请求统一走 `api/` 层；WebSocket 事件分发到 store 或页面订阅。数据流单向：`api → store/组件`。

## 一、Zustand Stores

```ts
// stores/authStore.ts
interface AuthState {
  user: { id: number; username: string; role: 'admin'|'analyst'|'readonly' } | null;
  token: string | null;
  login: (username, password, captchaId, captchaCode) => Promise<void>;
  logout: () => void;
}
```

| Store | 状态 | 变更来源 |
| --- | --- | --- |
| `authStore` | token / user | 登录/登出；token 与用户信息持久化 localStorage |
| `chatStore` | 群聊消息流（最多 200 条） | 发送需求 + WS 事件翻译成 Agent 发言 |
| `wsEvents` | `lastEvent`（最近一次 WS 事件信号） | 非聊天页订阅后刷新任务/DAG/资产 |

> 任务详情、DAG、资产列表等为页面级状态，由页面按需拉取并通过 `wsEvents.lastEvent` 触发刷新，不额外做全局 store。

## 二、API 层封装

```ts
// api/http.ts —— axios 实例
const http = axios.create({ baseURL: '/api/v1' });
http.interceptors.request.use(cfg => {
  const token = useAuthStore.getState().token;
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});
http.interceptors.response.use(
  r => r.data,
  err => {
    if (err.response?.status === 401) useAuthStore.getState().logout();
    throw err.response?.data ?? err;          // { code, message, request_id }
  },
);

// api/task.ts
export const fetchDag = (taskId: string) =>
  http.get<DagDTO>(`/tasks/${taskId}/dag`);
export const overrideTask = (taskId: string, body: OverrideDTO) =>
  http.post(`/tasks/${taskId}/override`, body);
```

**类型对齐**：`types/api.ts` 与后端 `schemas/*.py` 字段对应（后端 snake_case，前端按需映射）。
登录接口需要 `captcha_id` + `captcha_code`；429 时把 `message` 原样展示给用户。

## 三、WebSocket 封装

```ts
// api/ws.ts
const ws = new WebSocket('/ws/chat');           // token 不进 URL
ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }));
ws.onmessage = (e) => {
  const evt = JSON.parse(e.data);
  useChatStore.getState().dispatch(evt);        // 群聊发言
  useWsEvents.getState().emit(evt);             // 通知其它页面刷新
};
ws.onclose = () => setTimeout(connectWs, retryDelay);  // 指数退避，上限 30s
```

事件类型见 `docs/02-后端/05-消息事件契约.md`；登出时 `closeWs()` 会清掉重连定时器。

## 四、关键数据流示例：DQ 阻断 → 前端联动

```
后端 dq.blocked 事件
   → ws → chatStore.dispatch       · 群聊出现质检 Agent 发言
   → wsEvents.emit                 · 任务看板/工作台订阅后刷新 DAG 与状态
   → DagGraph 将该节点/边渲染为阻断态（真实数据来自 /tasks/{id}/dag）
```

## 五、乐观更新与失败回滚

- 人工干预、重命名等操作可乐观更新（立即改 UI，失败回滚 + toast）。
- 任务状态、资产血缘等**关键**状态一律以服务端查询/WS 事件为准，前端不自行推导状态机。

## 六、性能约定

- 大屏轮询接口 `60s`；列表分页 `page_size=20`；资产大文件下载走预签名 URL。
- `ChartRenderer` 在组件卸载时 `dispose()`，防止 ECharts 泄漏。
- DAG 使用自研 SVG 列布局，避免引入重型图库。
