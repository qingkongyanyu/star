/** 任务 API。 */

import { http } from './http'
import type { AssetOut, DAGOut, MemoryHitOut, Page, TaskOut } from '../types/api'

export async function createTask(message: string, dataSourceId?: number): Promise<DAGOut> {
  const resp = await http.post<DAGOut>('/tasks', {
    message,
    task_type: 'analysis',
    priority: 5,
    data_source_id: dataSourceId ?? null,
  })
  return resp.data
}

export async function getTaskDag(taskId: string): Promise<DAGOut> {
  const resp = await http.get<DAGOut>(`/tasks/${taskId}/dag`)
  return resp.data
}

export async function listTasks(page = 1, pageSize = 10): Promise<Page<TaskOut>> {
  const resp = await http.get<Page<TaskOut>>('/tasks', { params: { page, page_size: pageSize } })
  return resp.data
}

export async function getTaskAssets(taskId: string): Promise<AssetOut[]> {
  return (await http.get<AssetOut[]>(`/tasks/${taskId}/assets`)).data
}

export async function getTaskMemory(taskId: string): Promise<MemoryHitOut[]> {
  return (await http.get<MemoryHitOut[]>(`/tasks/${taskId}/memory`)).data
}
