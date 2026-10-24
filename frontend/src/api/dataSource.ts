/** 数据源接入 API。 */

import { http } from './http'
import type { DataSourceItem, DataSourceTestResult, FileUploadResult } from '../types/api'

export async function listDataSources(): Promise<DataSourceItem[]> {
  return (await http.get<DataSourceItem[]>('/data-sources')).data
}

/** 任务下发用数据源选项（脱敏，所有登录用户可用）。 */
export interface DataSourceOption {
  id: number
  name: string
  type: string
  status: string
}

export async function listDataSourceOptions(): Promise<DataSourceOption[]> {
  return (await http.get<DataSourceOption[]>('/data-sources/options')).data
}

export async function createDataSource(payload: {
  name: string
  type: string
  config: Record<string, unknown>
}): Promise<DataSourceItem> {
  return (await http.post<DataSourceItem>('/data-sources', payload)).data
}

export async function testDataSource(id: number): Promise<DataSourceTestResult> {
  return (await http.post<DataSourceTestResult>(`/data-sources/${id}/test`)).data
}

export async function deleteDataSource(id: number): Promise<void> {
  await http.delete(`/data-sources/${id}`)
}

export async function renameDataSource(id: number, name: string): Promise<DataSourceItem> {
  return (await http.put<DataSourceItem>(`/data-sources/${id}`, { name })).data
}

export async function getDataSourceTables(id: number): Promise<string[]> {
  return (await http.get<string[]>(`/data-sources/${id}/tables`)).data
}

export async function getDataSourceSchema(id: number, table?: string): Promise<Record<string, unknown>> {
  return (await http.get(`/data-sources/${id}/schema`, { params: { table } })).data
}

export async function uploadCsv(name: string, file: File): Promise<FileUploadResult> {
  const form = new FormData()
  form.append('name', name)
  form.append('file', file)
  return (await http.post<FileUploadResult>('/data-sources/upload', form)).data
}
