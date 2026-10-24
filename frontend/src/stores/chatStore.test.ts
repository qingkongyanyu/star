/** chatStore 单元测试：事件 → Agent 发言的翻译逻辑。 */

import { beforeEach, describe, expect, it } from 'vitest'

import { useChatStore } from '../stores/chatStore'

describe('chatStore', () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [] })
  })

  it('appendUser 追加一条用户消息', () => {
    useChatStore.getState().appendUser('分析订单销售额')
    const msgs = useChatStore.getState().messages
    expect(msgs).toHaveLength(1)
    expect(msgs[0]).toMatchObject({ role: 'user', content: '分析订单销售额' })
  })

  it('dispatch task.created 翻译为 orchestrator 发言', () => {
    useChatStore.getState().dispatch({ type: 'task.created', data: { task_id: 't-1', node_count: 5 } })
    const m = useChatStore.getState().messages[0]
    expect(m?.role).toBe('system')
    expect(m?.agent).toBe('orchestrator')
    expect(m?.content).toContain('t-1')
  })

  it('dispatch artifact.published 按 capability 匹配 Agent 模板', () => {
    useChatStore.getState().dispatch({
      type: 'artifact.published',
      data: { task_id: 't-2', agent_type: 'report_writing', title: '销售分析报告' },
    })
    const m = useChatStore.getState().messages[0]
    expect(m?.agent).toBe('report_writing')
    expect(m?.content).toContain('销售分析报告')
  })

  it('dispatch task.completed 成功态输出完成提示', () => {
    useChatStore.getState().dispatch({ type: 'task.completed', data: { task_id: 't-3', status: 'SUCCESS' } })
    const m = useChatStore.getState().messages[0]
    expect(m?.content).toContain('执行完成')
  })

  it('消息数量超过上限时截断旧消息', () => {
    for (let i = 0; i < 205; i++) {
      useChatStore.getState().appendUser(`msg-${i}`)
    }
    expect(useChatStore.getState().messages.length).toBeLessThanOrEqual(200)
    // 截断后保留最新一条
    expect(useChatStore.getState().messages.at(-1)?.content).toBe('msg-204')
  })
})
