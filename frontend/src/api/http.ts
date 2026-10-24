/** axios 实例：JWT 注入 + 401 处理。 */

import axios from 'axios'

const TOKEN_KEY = 'auth-token'

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    // 401 → 清会话并回登录页
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('auth-user')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    const data = error.response?.data
    error.message = data?.message || error.message || '请求失败'
    return Promise.reject(error)
  },
)
