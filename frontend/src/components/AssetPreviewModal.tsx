/** 资产预览弹窗：报告 / SQL / JSON 统一预览（Markdown 渲染或排版好的 JSON）。 */

import { useState } from 'react'
import { Modal } from 'antd'

import { getAssetContent } from '../api/asset'
import MarkdownView from './MarkdownView'
import { T } from '../config/theme'

interface AssetPreviewModalProps {
  assetId: string
  /** 弹窗标题 */
  title: string
  /** 资产版本，缺省 1 */
  version?: number
  open: boolean
  onClose: () => void
  onError?: (message: string) => void
}

export default function AssetPreviewModal({ assetId, title, version = 1, open, onClose, onError }: AssetPreviewModalProps) {
  const [content, setContent] = useState('')
  const [isMarkdown, setIsMarkdown] = useState(false)
  const [loaded, setLoaded] = useState(false)

  // open 变为 true 时拉取内容（每次打开都重新加载）
  const [lastOpen, setLastOpen] = useState(false)
  if (open && !lastOpen) {
    setLastOpen(true)
    setLoaded(false)
    getAssetContent(assetId, version)
      .then((c) => {
        const report = c.report
        if (typeof report === 'string' && report) {
          setContent(report)
          setIsMarkdown(true)
        } else {
          setContent(JSON.stringify(c, null, 2))
          setIsMarkdown(false)
        }
        setLoaded(true)
      })
      .catch((e: unknown) => {
        setContent((e as Error).message || '资产加载失败')
        setIsMarkdown(false)
        setLoaded(true)
        onError?.((e as Error).message || '资产加载失败')
      })
  }
  if (!open && lastOpen) {
    setLastOpen(false)
    setLoaded(false)
    setContent('')
  }

  return (
    <Modal title={title} open={open} onCancel={onClose} footer={null} width={820} style={{ top: 40 }}>
      {!loaded ? (
        <div style={{ color: T.textFaint, textAlign: 'center', padding: 32 }}>加载中…</div>
      ) : isMarkdown ? (
        <MarkdownView content={content} />
      ) : (
        <pre
          style={{
            color: T.text,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            fontSize: 12,
            background: '#fbf5ea',
            padding: 12,
            borderRadius: 8,
            margin: 0,
            maxHeight: '60vh',
            overflowY: 'auto',
          }}
        >
          {content}
        </pre>
      )}
    </Modal>
  )
}
