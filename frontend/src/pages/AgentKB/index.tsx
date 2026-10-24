/** Agent 知识库 / 提示词库：12 个 Agent 的职责、能力、产出与系统提示词。 */

import { useEffect, useMemo, useState } from 'react'
import { App, Card, Collapse, Empty, Input, Space, Tag, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'

import { getAgentKB } from '../../api/agents'
import type { AgentKBItem } from '../../api/agents'
import { AGENTS } from '../../config/agents'
import { T } from '../../config/theme'

const CAP_COLOR: Record<string, string> = {
  orchestrator: 'gold',
  metric_definition: 'blue',
  metadata_profiling: 'cyan',
  sql_extract: 'geekblue',
  quality_check: 'red',
  feature_engineer: 'purple',
  visualization: 'magenta',
  report_writing: 'orange',
  review: 'green',
  memory_management: 'volcano',
  trace: 'lime',
}

export default function AgentKB() {
  const { message } = App.useApp()
  const [items, setItems] = useState<AgentKBItem[]>([])
  const [keyword, setKeyword] = useState('')

  useEffect(() => {
    getAgentKB()
      .then(setItems)
      .catch((e) => message.error((e as Error).message))
  }, [message])

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    if (!kw) return items
    return items.filter(
      (a) => a.name.toLowerCase().includes(kw) || a.capability.toLowerCase().includes(kw) || a.role.toLowerCase().includes(kw),
    )
  }, [items, keyword])

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <div>
            <Typography.Title level={4} style={{ margin: 0, color: T.text }}>
              Agent 知识库 · 提示词库
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              共 {items.length} 个 Agent · 点击条目展开查看系统提示词
            </Typography.Text>
          </div>
          <Input
            allowClear
            prefix={<SearchOutlined style={{ color: T.textFaint }} />}
            placeholder="搜索名称 / 能力 / 职责"
            style={{ width: 280 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </div>
      </Card>

      {filtered.length === 0 ? (
        <Card>
          <Empty description="无匹配的 Agent" />
        </Card>
      ) : (
        <Collapse
          items={filtered.map((a) => {
            const meta = AGENTS.find((x) => x.capability === a.capability)
            return {
              key: a.id,
              label: (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <img
                    src={meta?.avatar}
                    alt={a.name}
                    style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', border: '1px solid rgba(180,130,70,0.35)' }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ color: T.text, fontWeight: 600, fontSize: 14 }}>{a.name}</div>
                    <div style={{ color: T.textFaint, fontSize: 11 }}>{a.role}</div>
                  </div>
                  <Tag color={CAP_COLOR[a.capability] ?? 'default'}>{a.capability}</Tag>
                  <Tag color="blue">产出：{a.asset_type || '—'}</Tag>
                </div>
              ),
              children: (
                <div>
                  <div style={{ marginBottom: 6, color: T.textFaint, fontSize: 12 }}>系统提示词（System Prompt）</div>
                  <pre
                    style={{
                      margin: 0,
                      padding: 14,
                      borderRadius: 8,
                      background: '#fbf5ea',
                      border: '1px solid rgba(180,130,70,0.2)',
                      color: T.text,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all',
                      fontSize: 12.5,
                      lineHeight: 1.7,
                      maxHeight: 420,
                      overflowY: 'auto',
                    }}
                  >
                    {a.system_prompt}
                  </pre>
                </div>
              ),
            }
          })}
        />
      )}
    </Space>
  )
}
