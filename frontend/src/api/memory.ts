/** 记忆 API。 */

import { http } from './http'
import type { AssetOut, MemoryHitOut } from '../types/api'

export interface MemorySearchParams {
  q?: string
  scope?: 'private' | 'collective' | 'all'
  agent?: string
  top_k?: number
}

export async function searchMemory(params: MemorySearchParams = {}): Promise<MemoryHitOut[]> {
  const resp = await http.get<MemoryHitOut[]>('/memory/search', {
    params: { q: params.q, scope: params.scope, agent: params.agent, top_k: params.top_k },
  })
  return resp.data
}

export async function listRetrospectives(): Promise<AssetOut[]> {
  const resp = await http.get<AssetOut[]>('/memory/retrospectives')
  return resp.data
}
