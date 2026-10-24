/** Agent 知识库 API。 */

import { http } from './http'

export interface AgentKBItem {
  id: string
  name: string
  capability: string
  role: string
  system_prompt: string
  asset_type: string
  implemented: boolean
}

export async function getAgentKB(): Promise<AgentKBItem[]> {
  return (await http.get<AgentKBItem[]>('/agents/kb')).data
}
