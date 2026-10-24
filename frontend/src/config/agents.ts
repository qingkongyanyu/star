/** 12 个 Agent 的展示配置。
 *
 * 顺序与后端 AGENT_CAPABILITY（app/core/constants.py）保持一致；
 * 头像统一放在 frontend/public/assets/agents/。
 */

export interface AgentMeta {
  /** 后端能力标签（大屏 agent_health 返回的 capability） */
  capability: string
  /** 短码（聊天成员列表用） */
  code: string
  /** 中文展示名 */
  name: string
  /** 头像路径 */
  avatar: string
}

export const AGENTS: AgentMeta[] = [
  { capability: 'orchestrator', code: 'Pl', name: '天枢_调度', avatar: '/assets/agents/agent-01.jpeg' },
  { capability: 'metric_definition', code: 'Md', name: '素问_指标定义', avatar: '/assets/agents/agent-02.jpeg' },
  { capability: 'metadata_profiling', code: 'Dp', name: '观澜_数据探查', avatar: '/assets/agents/agent-03.jpeg' },
  { capability: 'sql_extract', code: 'Sq', name: '灵枢_SQL抽取', avatar: '/assets/agents/agent-04.jpeg' },
  { capability: 'quality_check', code: 'Dq', name: '明鉴_数据质检', avatar: '/assets/agents/agent-05.jpeg' },
  { capability: 'feature_engineer', code: 'Fe', name: '炼金_特征工程', avatar: '/assets/agents/agent-06.jpeg' },
  { capability: 'visualization', code: 'Vs', name: '丹青_可视化', avatar: '/assets/agents/agent-07.jpeg' },
  { capability: 'insight_analysis', code: 'Ia', name: '天机_洞察分析', avatar: '/assets/agents/agent-12.jpeg' },
  { capability: 'report_writing', code: 'Rs', name: '文澜_报告编撰', avatar: '/assets/agents/agent-08.jpeg' },
  { capability: 'review', code: 'Ar', name: '洛璃_终审', avatar: '/assets/agents/agent-09.jpeg' },
  { capability: 'memory_management', code: 'Mk', name: '忆安_记忆管理', avatar: '/assets/agents/agent-10.jpeg' },
  { capability: 'trace', code: 'Tn', name: '闻道_溯源', avatar: '/assets/agents/agent-11.jpeg' },
]

/** 按能力标签取 Agent 元信息（缺省回退 Planner）。 */
export function agentByCapability(capability: string): AgentMeta {
  return AGENTS.find((a) => a.capability === capability) ?? AGENTS[0]!
}
