/** 认证状态（Zustand）：token 持久化 localStorage。 */

import { create } from 'zustand'
import { login as apiLogin } from '../api/auth'
import type { UserInfo } from '../types/api'

const TOKEN_KEY = 'auth-token'
const USER_KEY = 'auth-user'

interface AuthState {
  token: string | null
  user: UserInfo | null
  login: (username: string, password: string, captchaId: string, captchaCode: string) => Promise<void>
  logout: () => void
}

function loadUser(): UserInfo | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as UserInfo) : null
  } catch {
    return null
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: loadUser(),
  login: async (username, password, captchaId, captchaCode) => {
    const resp = await apiLogin(username, password, captchaId, captchaCode)
    localStorage.setItem(TOKEN_KEY, resp.access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(resp.user))
    set({ token: resp.access_token, user: resp.user })
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    set({ token: null, user: null })
  },
}))
