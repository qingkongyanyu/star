/** 路由 + 登录/角色守卫（docs/03-前端/03/05）。
 *
 * 页面组件按路由懒加载（React.lazy），配合 Suspense fallback 减少首屏体积。
 *
 * 本文件是「路由表 + 守卫组件」的聚合模块（非纯组件模块），不参与 Fast Refresh：
 * 因此按 oxlint 约定在文件级关闭 only-export-components 规则。
 */
// oxlint-disable react/only-export-components

import { lazy, Suspense } from 'react'
import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { Spin } from 'antd'

import { useAuthStore } from '../stores/authStore'
import MainLayout from '../layouts/MainLayout'

const Login = lazy(() => import('../pages/Login'))
const Chat = lazy(() => import('../pages/Chat'))
const TaskBoard = lazy(() => import('../pages/TaskBoard'))
const AssetCenter = lazy(() => import('../pages/AssetCenter'))
const BigScreen = lazy(() => import('../pages/BigScreen'))
const SystemAdmin = lazy(() => import('../pages/SystemAdmin'))
const MemoryCenter = lazy(() => import('../pages/MemoryCenter'))
const DataSource = lazy(() => import('../pages/DataSource'))
const AgentKB = lazy(() => import('../pages/AgentKB'))
const TaskWorkspace = lazy(() => import('../pages/TaskWorkspace'))

/** 懒加载统一 fallback（居中加载动画） */
function PageFallback() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 320 }}>
      <Spin size="large" />
    </div>
  )
}

/** 包装懒加载组件，挂 Suspense */
function lazyWrap(node: ReactNode) {
  return <Suspense fallback={<PageFallback />}>{node}</Suspense>
}

function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

function RequireRole({ role, children }: { role: string; children: ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  if (!token) {
    return <Navigate to="/login" replace />
  }
  if (user?.role !== role) {
    return <Navigate to="/task-board" replace />
  }
  return <>{children}</>
}

export const router = createBrowserRouter([
  { path: '/login', element: lazyWrap(<Login />) },
  {
    path: '/',
    element: (
      <RequireAuth>
        <MainLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: lazyWrap(<Chat />) },
      { path: 'task-board', element: lazyWrap(<TaskBoard />) },
      { path: 'task', element: lazyWrap(<TaskWorkspace />) },
      { path: 'task/:taskId', element: lazyWrap(<TaskWorkspace />) },
      { path: 'assets', element: lazyWrap(<AssetCenter />) },
      { path: 'bigscreen', element: lazyWrap(<BigScreen />) },
      {
        path: 'admin',
        element: (
          <RequireRole role="admin">
            {lazyWrap(<SystemAdmin />)}
          </RequireRole>
        ),
      },
      {
        path: 'data-sources',
        element: (
          <RequireRole role="admin">
            {lazyWrap(<DataSource />)}
          </RequireRole>
        ),
      },
      {
        path: 'agent-kb',
        element: (
          <RequireRole role="admin">
            {lazyWrap(<AgentKB />)}
          </RequireRole>
        ),
      },
      {
        path: 'memory',
        element: (
          <RequireRole role="admin">
            {lazyWrap(<MemoryCenter />)}
          </RequireRole>
        ),
      },
    ],
  },
])
