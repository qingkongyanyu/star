/** 聊天消息流：12 个 Agent 以各自身份在群里汇报任务进度。
 *
 * 事件（task.created / artifact.published / dq.blocked / task.completed）经 WS
 * 到达后，翻译成对应 Agent 的发言（agent = 能力标签，Chat 页据此渲染头像与名字）。
 */

import { create } from 'zustand'

export interface ChatMessage {
  id: number
  role: 'user' | 'system'
  content: string
  task_id?: string
  /** 发言 Agent 的能力标签（system 消息），如 sql_extract */
  agent?: string
  ts: number
}

const MAX_MESSAGES = 200

/** 各 Agent 的发言模板（title 为产出资产标题）。
 *
 * 诚实地用实际标题说话：不把「无数据源/失败」的产出谎称成「已对真实数据源执行取数」。
 */
const AGENT_MESSAGES: Record<string, (title: string) => string> = {
  planner: (_t) => '已收到需求，完成 DAG 拆解与调度，开始派发执行',
  metric_definition: (t) => `指标口径产出：${t}`,
  metadata_profiling: (t) => `数据探查产出：${t}`,
  sql_extract: (t) => `取数产出：${t}`,
  quality_check: (t) => `数据质检产出：${t}`,
  feature_engineer: (t) => `特征工程产出：${t}`,
  visualization: (t) => `可视化产出：${t}`,
  insight_analysis: (t) => `洞察分析产出：${t}`,
  report_writing: (t) => `分析报告产出：《${t}》`,
  review: (t) => `终审评审：《${t}》`,
  memory_management: (t) => `复盘记忆产出：${t}`,
  trace: (t) => `溯源产出：${t}`,
}

function eventToAgentMessage(type: string, data: Record<string, unknown>): { content: string; agent?: string } {
  const taskId = typeof data.task_id === 'string' ? `任务 ${data.task_id}` : ''
  switch (type) {
    case 'task.created':
      return {
        agent: 'orchestrator',
        content: `📦 ${taskId} · 收到需求并完成 DAG 拆解与调度（${String(data.node_count ?? '')} 个节点）`,
      }
    case 'planner.ready':
      return {
        agent: 'orchestrator',
        content: `🗺️ ${taskId} · Planner 拆解完成（${data.plan_source === 'hybrid' ? '规则识别 + LLM 补槽' : data.plan_source === 'llm' ? 'LLM 按需求拆解' : '确定性模板兜底'}）：${String(data.plan_summary ?? '')}（${String(data.node_count ?? '')} 个节点）`,
      }
    case 'task.planning':
      return { agent: 'orchestrator', content: `🧭 ${taskId} · Planner 正在召回记忆与指标口径并规划拆解` }
    case 'node.started': {
      const capability = typeof data.agent_type === 'string' ? data.agent_type : ''
      return { agent: capability, content: `▶️ ${capability} 节点开始执行（${taskId}）` }
    }
    case 'dag.updated':
      return { agent: 'orchestrator', content: `🔄 ${taskId} · DAG 已更新（阻断/修订分支）` }
    case 'agent.message': {
      // 定向协作消息（用户→Agent 质询 / Agent-Agent 反馈）：尽量归到目标 Agent
      const target = typeof data.to_role === 'string' && data.to_role !== 'user' ? data.to_role : undefined
      const from = typeof data.from_role === 'string' ? data.from_role : 'user'
      return {
        agent: target,
        content: `✉️ ${from} → ${String(target ?? '平台')}：${String(data.content ?? '')}`,
      }
    }
    case 'artifact.published': {
      const capability = typeof data.agent_type === 'string' ? data.agent_type : ''
      const title = typeof data.title === 'string' ? data.title : ''
      const tpl = AGENT_MESSAGES[capability]
      return { agent: capability, content: tpl ? tpl(title) : `产出资产 ${String(data.asset_id ?? '')}` }
    }
    case 'dq.blocked':
      return { agent: 'quality_check', content: `🚫 ${taskId} 数据质量阻断，触发特征工程清洗` }
    case 'dq.fe':
      return { agent: 'quality_check', content: `🧹 ${taskId} 数据存在质量告警，已前置特征清洗再出图/报告` }
    case 'dq.no_data':
      return {
        agent: 'quality_check',
        content: `🚫 ${taskId} 上游无数据集，无法分析（请先接入数据源后重新下发）`,
      }
    case 'review.rejected':
      return {
        agent: 'review',
        content: `❌ ${taskId} 终审否决了分析报告（${String(data.reason ?? '')}），任务未通过`,
      }
    case 'node.failed':
      return {
        agent: 'orchestrator',
        content: `⚠️ ${taskId} 节点执行失败：${String(data.agent_type ?? '')}，详情见任务工作台`,
      }
    case 'task.completed': {
      const status = String(data.status ?? '-')
      const reason = typeof data.reason === 'string' ? data.reason : ''
      const content =
        status === 'SUCCESS'
          ? `✅ ${taskId} 执行完成 · 点击下方「查看任务工作台」查看报告与图表`
          : `❌ ${taskId} 执行${status}${reason ? `：${reason}` : ''} · 详情见任务工作台`
      return { agent: 'orchestrator', content }
    }
    default:
      return { content: `${type}: ${JSON.stringify(data)}` }
  }
}

interface ChatState {
  messages: ChatMessage[]
  appendUser: (content: string) => void
  appendSystem: (content: string, taskId?: string, agent?: string) => void
  dispatch: (evt: { type: string; data: Record<string, unknown> }) => void
}

let seq = 0

function push(list: ChatMessage[], msg: ChatMessage): ChatMessage[] {
  return [...list, msg].slice(-MAX_MESSAGES)
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  appendUser: (content) =>
    set((s) => ({ messages: push(s.messages, { id: ++seq, role: 'user', content, ts: Date.now() }) })),
  appendSystem: (content, taskId, agent) =>
    set((s) => ({ messages: push(s.messages, { id: ++seq, role: 'system', content, task_id: taskId, agent, ts: Date.now() }) })),
  dispatch: (evt) => {
    const taskId = typeof evt.data.task_id === 'string' ? evt.data.task_id : undefined
    const { content, agent } = eventToAgentMessage(evt.type, evt.data)
    set((s) => ({
      messages: push(s.messages, {
        id: ++seq,
        role: 'system',
        content,
        task_id: taskId,
        agent,
        ts: Date.now(),
      }),
    }))
  },
}))
