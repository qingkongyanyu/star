/** WebSocket 客户端：/ws/chat 首帧鉴权 + 指数退避重连 + 事件分发。 */

import { useAuthStore } from '../stores/authStore'
import { useChatStore } from '../stores/chatStore'
import { useWsEvents } from '../stores/wsEvents'

let ws: WebSocket | null = null
let retryDelay = 1000
let retryTimer: ReturnType<typeof setTimeout> | null = null
let closed = false

export function connectWs() {
  closed = false
  const token = useAuthStore.getState().token
  if (!token) return

  // token 走首帧鉴权，不放进 URL（避免 token 出现在访问日志/代理日志）
  ws = new WebSocket('/ws/chat')
  ws.onopen = () => {
    retryDelay = 1000
    ws?.send(JSON.stringify({ type: 'auth', token }))
  }
  ws.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data)
      useChatStore.getState().dispatch(evt)
      useWsEvents.getState().emit(evt)
    } catch {
      // 忽略无法解析的消息
    }
  }
  ws.onclose = () => {
    if (closed) return
    retryTimer = setTimeout(() => {
      retryDelay = Math.min(retryDelay * 2, 30000)
      connectWs()
    }, retryDelay)
  }
}

export function closeWs() {
  // 清除待执行的重连定时器，避免登出后用旧 token 继续重连/重复建连
  closed = true
  if (retryTimer !== null) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  ws?.close()
  ws = null
}
