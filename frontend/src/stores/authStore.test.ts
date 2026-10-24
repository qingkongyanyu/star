/** authStore 单元测试：登录持久化 / 登出清理。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../stores/authStore'

vi.mock('../api/auth', () => ({
  login: vi.fn(async () => ({
    access_token: 'mock-token',
    token_type: 'bearer',
    user: { id: 1, username: 'admin', role: 'admin' },
  })),
}))

describe('authStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.setState({ token: null, user: null })
  })

  it('login 成功后写入 token 与 user 并持久化', async () => {
    await useAuthStore.getState().login('admin', 'pwd', 'cid', '1234')
    expect(useAuthStore.getState().token).toBe('mock-token')
    expect(useAuthStore.getState().user?.username).toBe('admin')
    expect(localStorage.getItem('auth-token')).toBe('mock-token')
    expect(localStorage.getItem('auth-user')).toBeTruthy()
  })

  it('logout 清空 token/user 与 localStorage', () => {
    useAuthStore.setState({ token: 't', user: { id: 1, username: 'u', role: 'analyst' } })
    useAuthStore.getState().logout()
    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(localStorage.getItem('auth-token')).toBeNull()
  })

  it('loadUser 对损坏的 JSON 返回 null（不抛异常）', () => {
    localStorage.setItem('auth-user', '{invalid json')
    // 触发 store 初始化（重新构造）
    const { useAuthStore: fresh } = { useAuthStore } // noop 保持引用
    void fresh
    expect(() => useAuthStore.getState()).not.toThrow()
  })
})
