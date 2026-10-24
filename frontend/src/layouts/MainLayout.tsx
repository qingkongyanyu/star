/** 主布局：侧边栏（logo + 菜单）+ 顶栏（用户信息）+ 内容区。秋季暖色调。 */

import { useEffect } from 'react'
import {
  AppstoreOutlined,
  BookOutlined,
  CommentOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  LinkOutlined,
  LogoutOutlined,
  ProjectOutlined,
  SafetyOutlined,
  SearchOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Layout, Menu, Space, Tag, Typography } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

import { closeWs, connectWs } from '../api/ws'
import { ASSETS } from '../config/assets'
import { T } from '../config/theme'
import { useAuthStore } from '../stores/authStore'

const { Sider, Header, Content } = Layout

const BASE_MENU = [
  { key: '/chat', icon: <CommentOutlined />, label: '协同群聊' },
  { key: '/task-board', icon: <ProjectOutlined />, label: '任务看板' },
  { key: '/task', icon: <AppstoreOutlined />, label: '任务工作台' },
  { key: '/assets', icon: <DatabaseOutlined />, label: '资产仓库' },
  { key: '/bigscreen', icon: <DashboardOutlined />, label: '数据大屏' },
]
const ADMIN_MENU = [
  { key: '/admin', icon: <SafetyOutlined />, label: '系统管理' },
  { key: '/data-sources', icon: <LinkOutlined />, label: '数据源接入' },
  { key: '/agent-kb', icon: <BookOutlined />, label: 'Agent 知识库' },
  { key: '/memory', icon: <SearchOutlined />, label: '记忆检索' },
]

const ROLE_LABEL: Record<string, { text: string; color: string }> = {
  admin: { text: '管理员', color: 'gold' },
  analyst: { text: '分析师', color: 'blue' },
  readonly: { text: '只读', color: 'default' },
}
const FALLBACK_ROLE = { text: '只读', color: 'default' }

export default function MainLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  // 登录后建立 WebSocket 连接（实时事件 → 聊天流）
  useEffect(() => {
    connectWs()
    return () => closeWs()
  }, [])

  const role = user?.role ?? 'readonly'
  const roleMeta = ROLE_LABEL[role] ?? FALLBACK_ROLE

  // 菜单高亮用首段路径：/task/:taskId 归到 /task 菜单项
  const selectedKey = `/${location.pathname.split('/')[1] ?? ''}`

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={224}
        theme="dark"
        style={{
          background: T.sidebarBg,
          borderRight: '1px solid rgba(255, 220, 170, 0.18)',
        }}
      >
        {/* 品牌区：logo + 标题 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '18px 16px 14px' }}>
          <img
            src={ASSETS.logo}
            alt="logo"
            style={{ width: 38, height: 38, borderRadius: 10, objectFit: 'cover', boxShadow: '0 4px 12px rgba(0,0,0,0.35)' }}
          />
          <div style={{ lineHeight: 1.25 }}>
            <div style={{ color: '#fff', fontWeight: 700, fontSize: 14 }}>星枢 · 智能数据分析平台</div>
            <div style={{ color: T.sidebarSub, fontSize: 10, letterSpacing: 1 }}>STARPIVOT · 12-AGENT</div>
          </div>
        </div>

        <Menu
          className="side-menu"
          theme="dark"
          mode="inline"
          style={{ background: 'transparent', borderInlineEnd: 'none', marginTop: 8 }}
          selectedKeys={[selectedKey]}
          items={user?.role === 'admin' ? [...BASE_MENU, ...ADMIN_MENU] : BASE_MENU}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>

      <Layout>
        <Header
          style={{
            background: 'rgba(255, 253, 248, 0.88)',
            backdropFilter: 'blur(10px)',
            WebkitBackdropFilter: 'blur(10px)',
            borderBottom: '1px solid rgba(180, 130, 70, 0.18)',
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            paddingInline: 24,
          }}
        >
          <Space size={20}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Avatar
                size={34}
                src={role === 'admin' ? ASSETS.adminAvatar : undefined}
                icon={role === 'admin' ? undefined : <UserOutlined />}
                style={{ background: '#f0e3cc', boxShadow: '0 2px 8px rgba(120,80,30,0.2)' }}
              />
              <div style={{ textAlign: 'right', lineHeight: 1.2 }}>
                <Typography.Text style={{ color: T.text, fontSize: 13, fontWeight: 600, display: 'block' }}>
                  {user?.display_name || user?.username}
                </Typography.Text>
                <Tag color={roleMeta.color} style={{ marginRight: 0, fontSize: 10, lineHeight: '16px' }}>
                  {roleMeta.text}
                </Tag>
              </div>
            </div>
            <Button
              icon={<LogoutOutlined />}
              onClick={() => {
                closeWs()
                logout()
                navigate('/login', { replace: true })
              }}
            >
              登出
            </Button>
          </Space>
        </Header>

        <Content style={{ margin: 16, padding: 8 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
