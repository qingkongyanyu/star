/** Markdown 渲染（分析报告预览）。
 *
 *  v3：`![图 N ...](chart://<chartId>)` 会解析为可交互 ECharts 图表（图表由报告内嵌），
 *  兼容旧的 `chart://asset@ver/type/index`（显示为图表徽章）；表格自动横向滚动。
 */

import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { ChartSpec } from '../types/api'
import ChartRenderer from './ChartRenderer'

interface Props {
  content: string
  charts?: Record<string, ChartSpec>
}

export default function MarkdownView({ content, charts }: Props) {
  const components = useMemo<Components>(() => {
    const map = charts ?? {}
    return {
      img({ src, alt }) {
        if (src && src.startsWith('chart://')) {
          const ref = src.slice('chart://'.length).trim()
          const chartId = ref.split(/[/@]/)[0] ?? ''
          const chart = map[chartId]
          if (chart) {
            return (
              <figure style={{ margin: '16px 0 18px' }}>
                <div
                  style={{
                    border: '1px solid rgba(180,130,70,0.18)',
                    borderRadius: 14,
                    padding: '10px 8px 4px',
                    background: 'rgba(255,253,248,0.72)',
                  }}
                >
                  <ChartRenderer chart={chart} height={320} />
                </div>
                <figcaption style={{ marginTop: 8, textAlign: 'center', fontSize: 12, color: '#8a7358' }}>
                  {alt || chart.title}
                </figcaption>
              </figure>
            )
          }
          return (
            <span
              style={{
                display: 'inline-block',
                margin: '6px 6px 6px 0',
                padding: '3px 12px',
                borderRadius: 999,
                background: 'rgba(217, 119, 6, 0.12)',
                border: '1px solid rgba(217, 119, 6, 0.32)',
                color: '#b45309',
                fontSize: 12,
              }}
            >
              📈 图表：{alt || '已生成'}
            </span>
          )
        }
        return (
          <img
            src={src}
            alt={alt ?? ''}
            style={{ maxWidth: '100%', borderRadius: 10, border: '1px solid rgba(180,130,70,0.16)', margin: '12px 0' }}
          />
        )
      },
      a({ href, children }) {
        return (
          <a href={href} target="_blank" rel="noreferrer" style={{ color: '#b45309' }}>
            {children}
          </a>
        )
      },
      table({ children }) {
        return (
          <div style={{ overflowX: 'auto', margin: '12px 0' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12.5 }}>{children}</table>
          </div>
        )
      },
    }
  }, [charts])

  return (
    <div className="md-view">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        urlTransform={(url) => url}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
