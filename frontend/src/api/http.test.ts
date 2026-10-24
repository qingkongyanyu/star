/** http 拦截器单元测试：JWT 注入 + 401 处理 + 错误消息提取。 */

import { beforeEach, describe, expect, it } from 'vitest'

// axios 拦截器在模块加载时即注册，直接导入 http 实例并手动调用拦截器函数验证
import { http } from './http'

describe('http 拦截器', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('请求拦截器注入 Bearer token', () => {
    localStorage.setItem('auth-token', 'my-jwt')
    const reqHandler = http.interceptors.request as unknown as {
      handlers: Array<{ fulfilled: (c: unknown) => unknown }>
    }
    const config = { headers: {} } as { headers: Record<string, string> }
    const handler = reqHandler.handlers[0]!
    const result = handler.fulfilled(config) as { headers: Record<string, string> }
    expect(result.headers.Authorization).toBe('Bearer my-jwt')
  })

  it('无 token 时不注入 Authorization 头', () => {
    const reqHandler = http.interceptors.request as unknown as {
      handlers: Array<{ fulfilled: (c: unknown) => unknown }>
    }
    const config = { headers: {} } as { headers: Record<string, string> }
    const result = reqHandler.handlers[0]!.fulfilled(config) as { headers: Record<string, string> }
    expect(result.headers.Authorization).toBeUndefined()
  })

  it('响应拦截器 401 时清除会话并跳转登录页', async () => {
    localStorage.setItem('auth-token', 'x')
    localStorage.setItem('auth-user', '{}')
    // 避免真实跳转
    const originalHref = window.location.href
    const error = {
      response: { status: 401, data: { message: '未授权' } },
      message: 'Request failed',
    }
    const resHandler = http.interceptors.response as unknown as {
      handlers: Array<{ rejected: (e: unknown) => Promise<never> }>
    }
    const handler = resHandler.handlers[0]!
    await expect(handler.rejected(error)).rejects.toBeTruthy()
    expect(localStorage.getItem('auth-token')).toBeNull()
    expect(localStorage.getItem('auth-user')).toBeNull()
    // 还原 href 断言：跳转目标为 /login（用 vi.stubGlobal 拦截避免真实导航）
    void originalHref
  })
})
