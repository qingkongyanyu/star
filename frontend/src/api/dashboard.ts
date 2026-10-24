/** 大屏 API。 */

import { http } from './http'
import type { QualityOut, SummaryOut, TrendsOut } from '../types/api'

export async function getSummary(): Promise<SummaryOut> {
  const resp = await http.get<SummaryOut>('/dashboard/summary')
  return resp.data
}

export async function getTrends(days = 14): Promise<TrendsOut> {
  const resp = await http.get<TrendsOut>('/dashboard/trends', { params: { days } })
  return resp.data
}

export async function getQuality(): Promise<QualityOut> {
  const resp = await http.get<QualityOut>('/dashboard/quality')
  return resp.data
}
