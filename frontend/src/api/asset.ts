/** 资产 API。 */

import { http } from './http'
import type { AssetOut, Page } from '../types/api'

export interface AssetListParams {
  type?: string
  keyword?: string
  page?: number
  page_size?: number
}

export async function listAssets(params: AssetListParams = {}): Promise<Page<AssetOut>> {
  const resp = await http.get<Page<AssetOut>>('/assets', { params })
  return resp.data
}

export interface AssetDownloadOut {
  url: string
  expires_in: number
  object_key: string
}

/** 获取资产下载（预签名 URL）。version 缺省取 1（当前后端 v1 每次发布即新资产、版本恒为 1）。 */
export async function getAssetDownloadUrl(assetId: string, version = 1): Promise<AssetDownloadOut> {
  return (await http.get<AssetDownloadOut>(`/assets/${assetId}/versions/${version}`)).data
}

/** 获取资产内容（解析 JSON）。用于报告预览。 */
export async function getAssetContent(assetId: string, version = 1): Promise<Record<string, unknown>> {
  const { url } = await getAssetDownloadUrl(assetId, version)
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`资产下载失败: HTTP ${resp.status}`)
  return (await resp.json()) as Record<string, unknown>
}

/** 下载为干净文件：报告 → .md、SQL → .sql、其余 → 排版好的 .json。 */
export async function downloadAssetFile(assetId: string, label = '', version = 1): Promise<void> {
  const { url } = await getAssetDownloadUrl(assetId, version)
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`下载失败: HTTP ${resp.status}`)
  const content = (await resp.json()) as Record<string, unknown>

  let text: string
  let ext: string
  const base = String((content.title as string) || label || assetId)
  const report = content.report
  const reportExport = content.report_export_md
  const sql = content.sql
  if (typeof report === 'string' && report) {
    // 导出优先用内嵌 PNG 的版本（离线可看图）；旧的纯文本报告保持兼容
    text = typeof reportExport === 'string' && reportExport.includes('data:image/png;base64') ? reportExport : report
    ext = 'md'
  } else if (typeof sql === 'string' && sql) {
    text = sql
    ext = 'sql'
  } else {
    text = JSON.stringify(content, null, 2)
    ext = 'json'
  }

  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objUrl
  a.download = `${label ? `${label}_` : ''}${base}.${ext}`.replace(/[\\/:*?"<>|\s]+/g, '_')
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objUrl)
}
