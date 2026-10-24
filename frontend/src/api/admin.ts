/** 系统管理 API。 */

import { http } from './http'
import type { ACLRuleOut, AuditEntryOut, KBIndicatorOut, Page, UserAdminOut } from '../types/api'

export async function listUsers(page = 1, pageSize = 20): Promise<Page<UserAdminOut>> {
  const resp = await http.get<Page<UserAdminOut>>('/admin/users', { params: { page, page_size: pageSize } })
  return resp.data
}

export async function createUser(payload: {
  username: string
  password: string
  role: string
  display_name?: string
}): Promise<UserAdminOut> {
  const resp = await http.post<UserAdminOut>('/admin/users', payload)
  return resp.data
}

export async function updateUser(
  userId: number,
  payload: { role?: string; is_active?: boolean },
): Promise<UserAdminOut> {
  const resp = await http.put<UserAdminOut>(`/admin/users/${userId}`, payload)
  return resp.data
}

export async function getAcl(): Promise<ACLRuleOut[]> {
  const resp = await http.get<ACLRuleOut[]>('/admin/acl')
  return resp.data
}

export async function replaceAcl(rules: Omit<ACLRuleOut, 'id'>[]): Promise<ACLRuleOut[]> {
  const resp = await http.put<ACLRuleOut[]>('/admin/acl', { rules })
  return resp.data
}

export interface ACLPayload {
  role_or_user: string
  table: string
  action: string
  allow: boolean
}

export async function createAcl(payload: ACLPayload): Promise<ACLRuleOut> {
  return (await http.post<ACLRuleOut>('/admin/acl', payload)).data
}

export async function updateAcl(id: number, payload: ACLPayload): Promise<ACLRuleOut> {
  return (await http.put<ACLRuleOut>(`/admin/acl/${id}`, payload)).data
}

export async function deleteAcl(id: number): Promise<void> {
  await http.delete(`/admin/acl/${id}`)
}

export async function listKb(keyword?: string, page = 1, pageSize = 20): Promise<Page<KBIndicatorOut>> {
  const resp = await http.get<Page<KBIndicatorOut>>('/admin/kb', {
    params: { keyword, page, page_size: pageSize },
  })
  return resp.data
}

export async function createKb(payload: {
  indicator_key: string
  metric_name: string
  category?: string
  formula?: string
  description?: string
  tags?: string[]
}): Promise<KBIndicatorOut> {
  const resp = await http.post<KBIndicatorOut>('/admin/kb', payload)
  return resp.data
}

export async function updateKb(
  id: number,
  payload: {
    metric_name?: string
    category?: string
    formula?: string
    description?: string
    tags?: string[]
  },
): Promise<KBIndicatorOut> {
  const resp = await http.put<KBIndicatorOut>(`/admin/kb/${id}`, payload)
  return resp.data
}

export async function deleteKb(id: number): Promise<void> {
  await http.delete(`/admin/kb/${id}`)
}

export interface AuditQuery {
  actor?: string
  action?: string
  asset_id?: string
  page?: number
  page_size?: number
}

export async function listAudit(params: AuditQuery = {}): Promise<Page<AuditEntryOut>> {
  const resp = await http.get<Page<AuditEntryOut>>('/admin/audit', { params })
  return resp.data
}
