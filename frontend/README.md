# 星枢 StarPivot · 前端

React 19 + TypeScript + Vite 8 + Ant Design 6 + ECharts 6。

## 开发

```bash
npm install
npm run dev        # http://localhost:5173，/api 与 /ws 代理到 127.0.0.1:8070
```

## 质量门槛

```bash
npm test           # vitest
npm run lint       # oxlint
npm run build      # tsc -b && vite build
```

## 文档

- 目录与组件：`docs/03-前端/02-目录结构与组件.md`
- 路由与页面：`docs/03-前端/03-路由与页面.md`
- 状态与 API：`docs/03-前端/04-状态管理与API对接.md`
- 登录与安全：`docs/03-前端/05-权限与安全.md`
