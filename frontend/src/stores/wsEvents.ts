/** WebSocket 事件信号（供非聊天页订阅即时刷新，如任务看板）。 */

import { create } from 'zustand'

import type { WsEvent } from '../types/api'

export interface WsEventSignal {
  type: string
  data: Record<string, unknown>
  at: number
}

interface WsEventsState {
  lastEvent: WsEventSignal | null
  emit: (evt: WsEvent) => void
}

export const useWsEvents = create<WsEventsState>((set) => ({
  lastEvent: null,
  emit: (evt) => set({ lastEvent: { ...evt, at: Date.now() } }),
}))
