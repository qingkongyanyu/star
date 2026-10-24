/** 记忆检索中心：记忆检索 + 复盘纪要列表（docs/03-前端/02 MemoryCenter）。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { App, Button, Card, Empty, Form, Input, InputNumber, List, Select, Space, Tag, Timeline } from 'antd'

import { listRetrospectives, searchMemory } from '../../api/memory'
import type { AssetOut, MemoryHitOut } from '../../types/api'

const SCOPE_LABEL: Record<string, string> = { private: '私有', collective: '集体', all: '全部' }

export default function MemoryCenter() {
  const { message } = App.useApp()
  const [hits, setHits] = useState<MemoryHitOut[]>([])
  const [retros, setRetros] = useState<AssetOut[]>([])
  const [form] = Form.useForm()

  // 检索结果按日期分组（时间线）
  const grouped = useMemo(() => {
    const map = new Map<string, MemoryHitOut[]>()
    for (const h of hits) {
      const day = h.created_ts.slice(0, 10)
      const list = map.get(day) || []
      list.push(h)
      map.set(day, list)
    }
    return Array.from(map.entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1))
  }, [hits])

  const onSearch = useCallback(
    async (v: { q?: string; scope: string; agent?: string; top_k: number }) => {
      try {
        const resp = await searchMemory({
          q: v.q,
          scope: v.scope as 'private' | 'collective' | 'all',
          agent: v.agent,
          top_k: v.top_k,
        })
        setHits(resp)
      } catch (e) {
        message.error((e as Error).message)
      }
    },
    [message],
  )

  useEffect(() => {
    listRetrospectives()
      .then(setRetros)
      .catch(() => undefined)
    onSearch({ q: '', scope: 'all', agent: undefined, top_k: 10 })
  }, [onSearch])

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Card title="记忆检索">
        <Form form={form} layout="inline" initialValues={{ scope: 'all', top_k: 10 }} onFinish={onSearch}>
          <Form.Item name="q" label="关键词">
            <Input placeholder="检索内容" allowClear />
          </Form.Item>
          <Form.Item name="scope" label="范围">
            <Select style={{ width: 120 }} options={[
              { value: 'all', label: '全部' },
              { value: 'private', label: '私有' },
              { value: 'collective', label: '集体' },
            ]} />
          </Form.Item>
          <Form.Item name="agent" label="Agent">
            <Input placeholder="如 sql_agent" allowClear />
          </Form.Item>
          <Form.Item name="top_k" label="条数">
            <InputNumber min={1} max={50} style={{ width: 80 }} />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            检索
          </Button>
        </Form>
        {/* 时间线：按日期分组（docs/03-前端/02 MemoryCenter） */}
        <div style={{ marginTop: 16 }}>
          {grouped.length === 0 ? (
            <Empty description="无匹配记忆" />
          ) : (
            grouped.map(([day, items]) => (
              <div key={day}>
                <div style={{ color: '#8a7256', margin: '12px 0 8px' }}>{day}</div>
                <Timeline
                  items={items.map((h) => ({
                    color: h.scope === 'private' ? 'blue' : 'green',
                    children: (
                      <div>
                        <Space wrap>
                          <Tag color={h.scope === 'private' ? 'blue' : 'green'}>{SCOPE_LABEL[h.scope]}</Tag>
                          <Tag>{h.agent_id}</Tag>
                          <Tag>{h.experience_type}</Tag>
                          {h.source_task ? <Tag>{h.source_task}</Tag> : null}
                        </Space>
                        <div style={{ marginTop: 4 }}>{h.content}</div>
                      </div>
                    ),
                  }))}
                />
              </div>
            ))
          )}
        </div>
      </Card>
      <Card title="复盘纪要">
        <List
          dataSource={retros}
          locale={{ emptyText: '暂无复盘纪要（任务成功后自动生成）' }}
          renderItem={(r) => (
            <List.Item>
              <List.Item.Meta title={r.title} description={`${r.asset_id} · 产出 ${r.producer_agent} · 版本 ${r.latest_version ?? '-'}`} />
            </List.Item>
          )}
        />
      </Card>
    </Space>
  )
}
