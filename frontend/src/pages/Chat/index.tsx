/** 协同群聊：选数据源 → 输入需求 → 发送 → 实时推送进度 → 跳转任务工作台看结果。秋季暖色调。 */

import { useEffect, useRef, useState } from 'react'
import { App, Avatar, Button, Input, Select, Space, Typography } from 'antd'
import { SendOutlined, UserOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { listDataSourceOptions } from '../../api/dataSource'
import type { DataSourceOption } from '../../api/dataSource'
import { createTask } from '../../api/task'
import { AGENTS, agentByCapability } from '../../config/agents'
import { ASSETS } from '../../config/assets'
import { T } from '../../config/theme'
import { useAuthStore } from '../../stores/authStore'
import { useChatStore } from '../../stores/chatStore'

export default function Chat() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const messages = useChatStore((s) => s.messages)
  const appendUser = useChatStore((s) => s.appendUser)
  const user = useAuthStore((s) => s.user)
  const [input, setInput] = useState('')
  const [dataSources, setDataSources] = useState<DataSourceOption[]>([])
  const [dsId, setDsId] = useState<number>()
  const streamRef = useRef<HTMLDivElement>(null)

  const connected = dataSources.filter((d) => d.status === 'connected')

  useEffect(() => {
    listDataSourceOptions()
      .then((list) => {
        setDataSources(list)
        const firstConnected = list.find((d) => d.status === 'connected')
        if (firstConnected) setDsId(firstConnected.id)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight
    }
  }, [messages])

  const send = async () => {
    const text = input.trim()
    if (!text) return
    if (connected.length === 0) {
      message.warning('请先接入数据源（数据源接入 → 连接 MySQL 或上传 CSV），任务必须基于真实数据源')
      return
    }
    if (!dsId) {
      message.warning('请选择一个数据源')
      return
    }
    setInput('')
    appendUser(text)
    try {
      await createTask(text, dsId)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 120px)', gap: 16 }}>
      {/* 成员列表 */}
      <div
        style={{
          width: 220,
          background: 'rgba(255,255,255,0.72)',
          border: T.screenBorder,
          borderRadius: 12,
          padding: 12,
          overflowY: 'auto',
          boxShadow: T.shadow,
        }}
      >
        <div style={{ color: T.textFaint, fontSize: 12, marginBottom: 10, letterSpacing: 1 }}>AGENTS · {AGENTS.length}</div>
        {AGENTS.map((a) => (
          <div key={a.capability} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 4px', borderRadius: 8 }}>
            <img
              src={a.avatar}
              alt={a.name}
              style={{ width: 30, height: 30, borderRadius: '50%', objectFit: 'cover', border: '1px solid rgba(180,130,70,0.3)' }}
            />
            <span style={{ flex: 1, fontSize: 13, color: T.text }}>{a.name}</span>
            <span style={{ width: 8, height: 8, borderRadius: 4, background: T.green, boxShadow: `0 0 6px ${T.green}` }} />
          </div>
        ))}
      </div>

      {/* 消息流 + 输入框 */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          background: 'rgba(255,255,255,0.72)',
          border: T.screenBorder,
          borderRadius: 12,
          boxShadow: T.shadow,
        }}
      >
        {/* 使用引导 */}
        <div style={{ padding: '10px 16px', borderBottom: '1px solid rgba(180,130,70,0.16)', background: 'rgba(217,119,6,0.06)' }}>
          <Typography.Text style={{ color: T.textSoft, fontSize: 12 }}>
            💡 使用：<b>①</b> 下方选择数据源 <b>②</b> 输入分析需求 <b>③</b> 发送 <b>④</b> 到「任务工作台」看结果
          </Typography.Text>
        </div>

        <div ref={streamRef} style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {messages.length === 0 && (
            <div style={{ color: T.textFaint, textAlign: 'center', marginTop: 48 }}>
              <div style={{ fontSize: 15, marginBottom: 6 }}>在下方输入分析需求，将自动下发任务并实时推送执行进度</div>
              <div style={{ fontSize: 12 }}>如：分析订单表 orders 各区域销售额，输出标准报告</div>
            </div>
          )}
          {messages.map((m) => {
            const agentMeta = m.role === 'system' && m.agent ? agentByCapability(m.agent) : null
            const avatarSrc =
              m.role === 'user' ? (user?.role === 'admin' ? ASSETS.adminAvatar : undefined) : agentMeta ? agentMeta.avatar : ASSETS.logo
            const senderName = m.role === 'user' ? '我' : agentMeta ? agentMeta.name : '平台'
            return (
            <div key={m.id} style={{ display: 'flex', flexDirection: m.role === 'user' ? 'row-reverse' : 'row', gap: 8, marginBottom: 14 }}>
              <Avatar
                size={32}
                src={avatarSrc}
                icon={m.role === 'user' && user?.role !== 'admin' ? <UserOutlined /> : undefined}
                style={{ background: m.role === 'user' ? 'rgba(217,119,6,0.25)' : agentMeta ? 'rgba(217,119,6,0.12)' : 'rgba(180,130,70,0.25)', borderRadius: 8 }}
              />
              <div style={{ maxWidth: '75%' }}>
                <div style={{ fontSize: 11, color: m.role === 'user' ? T.textFaint : T.primaryDeep, fontWeight: m.role === 'system' && agentMeta ? 600 : 400, textAlign: m.role === 'user' ? 'right' : 'left', marginBottom: 2 }}>
                  {senderName} · {new Date(m.ts).toLocaleTimeString()}
                </div>
                <div
                  style={{
                    background: m.role === 'user' ? 'linear-gradient(135deg,#d97706,#b45309)' : 'rgba(255,255,255,0.95)',
                    border: m.role === 'user' ? 'none' : '1px solid rgba(180,130,70,0.2)',
                    borderRadius: 12,
                    borderTopRightRadius: m.role === 'user' ? 4 : 12,
                    borderTopLeftRadius: m.role === 'user' ? 12 : 4,
                    padding: '9px 13px',
                    wordBreak: 'break-word',
                    color: m.role === 'user' ? '#fff' : T.text,
                    boxShadow: m.role === 'user' ? '0 4px 12px rgba(217,119,6,0.25)' : '0 2px 8px rgba(120,80,30,0.06)',
                  }}
                >
                  {m.content}
                </div>
                {m.role === 'system' && m.task_id && (
                  <div style={{ marginTop: 4, textAlign: 'left' }}>
                    <Button size="small" type="link" style={{ padding: 0, height: 'auto', fontSize: 12 }} onClick={() => navigate(`/task/${m.task_id}`)}>
                      查看任务工作台 →
                    </Button>
                  </div>
                )}
              </div>
            </div>
            )
          })}
        </div>

        {/* 数据源选择 + 输入 */}
        <div style={{ borderTop: '1px solid rgba(180,130,70,0.16)', padding: 12 }}>
          {connected.length === 0 ? (
            <div style={{ marginBottom: 8, padding: '8px 12px', borderRadius: 8, background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.2)' }}>
              {user?.role === 'admin' ? (
                <>
                  <Typography.Text style={{ color: T.red, fontSize: 12 }}>
                    还没有可用的数据源，分析会没有数据。请先到
                  </Typography.Text>
                  <Button size="small" type="link" style={{ padding: 0, fontSize: 12, color: T.primaryDeep }} onClick={() => navigate('/data-sources')}>
                    「数据源接入」
                  </Button>
                  <Typography.Text style={{ color: T.red, fontSize: 12 }}>连接 MySQL 或上传 CSV</Typography.Text>
                </>
              ) : (
                <Typography.Text style={{ color: T.red, fontSize: 12 }}>
                  还没有可用的数据源，分析会没有数据。请联系管理员接入数据源后再下发。
                </Typography.Text>
              )}
            </div>
          ) : null}
          <Space.Compact style={{ width: '100%' }}>
            <Select
              allowClear
              placeholder="选择数据源"
              style={{ width: 240 }}
              value={dsId}
              onChange={setDsId}
              options={dataSources.map((ds) => ({
                value: ds.id,
                label: `${ds.name}（${ds.type.toUpperCase()}${ds.status === 'connected' ? '' : ' · 未连接'}）`,
                disabled: ds.status !== 'connected',
              }))}
            />
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={send}
              placeholder="输入分析需求，如：分析订单各区域销售额并出报告"
            />
            <Button type="primary" icon={<SendOutlined />} onClick={send}>
              发送
            </Button>
          </Space.Compact>
        </div>
      </div>
    </div>
  )
}
