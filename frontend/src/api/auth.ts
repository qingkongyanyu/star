/** 认证 API。 */

import { http } from './http'
import type { LoginResponse } from '../types/api'

export interface CaptchaResult {
  captcha_id: string
  svg: string
  expires_in: number
}

/** 获取登录验证码（SVG 图形）。 */
export async function getCaptcha(): Promise<CaptchaResult> {
  const resp = await http.get<CaptchaResult>('/auth/captcha')
  return resp.data
}

export async function login(
  username: string,
  password: string,
  captchaId: string,
  captchaCode: string,
): Promise<LoginResponse> {
  const resp = await http.post<LoginResponse>('/auth/login', {
    username,
    password,
    captcha_id: captchaId,
    captcha_code: captchaCode,
  })
  return resp.data
}
