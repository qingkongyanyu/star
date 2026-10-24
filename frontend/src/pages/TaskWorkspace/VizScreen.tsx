/** 任务数据可视化大屏：渲染该任务最终分析产物（多数据集 / 多图型 / 关键洞察 / 图文表报告）。
 *
 * 数据来自任务 DAG：
 * - visualization：datasets[] + charts[]（服务端已按数据特征/意图选型并给出 ECharts option）
 * - insight_analysis：findings[]（带证据与图表引用）
 * - report_writing：report（引用 chart://，由 MarkdownView 渲染为可交互图表）
 * 每个任务独立加载，切任务即刷新，不复用上一个任务的数据。
 */

import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { App, Collapse, Empty, Tag } from 'antd'

import { getAssetContent } from '../../api/asset'
import { getTaskDag } from '../../api/task'
import ChartRenderer from '../../components/ChartRenderer'
import MarkdownView from '../../components/MarkdownView'
import { CHART_TYPE_LABEL } from '../../config/labels'
import { T } from '../../config/theme'
import type { ChartSpec, DatasetSpec, InsightFinding, InsightOut } from '../../types/api'

const PANEL: CSSProperties = {
  background: T.screenPanel,
  border: T.screenBorder,
  borderRadius: 14,
  backdropFilter: 'blur(10px)',
  WebkitBackdropFilter: 'blur(10px)',
  boxShadow: T.screenShadow,
  padding: '18px 20px',
}

const WIDE_TYPES = new Set(['line', 'bar', 'waterfall', 'treemap', 'heatmap'])

function fmtNum(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(1)}万`
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function PanelTitle({ text, dot, extra }: { text: string; dot: string; extra?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, boxShadow: `0 0 8px ${dot}` }} />
      <span style={{ fontSize: 15, fontWeight: 700, color: T.text }}>{text}</span>
      {extra}
    </div>
  )
}

function severityColor(severity: string): string {
  if (severity === 'critical') return '#dc2626'
  if (severity === 'warning') return '#d97706'
  return '#0f766e'
}

function KpiCard({ dataset }: { dataset: DatasetSpec }) {
  const values = dataset.rows
    .map((row) => (typeof row[1] === 'number' ? (row[1] as number) : null))
    .filter((value): value is number => value !== null)
  const total = values.reduce((sum, value) => sum + value, 0)
  const average = values.length ? total / values.length : 0
  const isAvg = dataset.agg === 'avg' || dataset.agg === 'ratio'
  const money = dataset.metric_kind === 'money'
  return (
    <div style={{ ...PANEL, padding: '14px 16px', minWidth: 0 }}>
      <div style={{ fontSize: 12, color: T.textFaint }}>{dataset.metric_name}</div>
      <div style={{ marginTop: 6, fontSize: 24, fontWeight: 800, color: T.primaryDeep, whiteSpace: 'nowrap' }}>
        {money ? '¥' : ''}
        {fmtNum(isAvg ? average : total)}
        <span style={{ fontSize: 12, fontWeight: 500, color: T.textSoft, marginLeft: 4 }}>{dataset.unit}</span>
      </div>
      <div style={{ marginTop: 4, fontSize: 11.5, color: T.textSoft }}>
        {isAvg ? (dataset.agg === 'ratio' ? '均值' : '组均值') : '合计'} · {dataset.row_count} 个{dataset.dim_label}
      </div>
    </div>
  )
}

function DatasetTable({ dataset }: { dataset: DatasetSpec }) {
  const rows = dataset.rows.slice(0, 30)
  const total = rows.reduce((sum, row) => sum + (typeof row[1] === 'number' ? (row[1] as number) : 0), 0)
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
        <thead>
          <tr>
            {dataset.columns.map((column, index) => (
              <th
                key={column}
                style={{
                  border: '1px solid rgba(180,130,70,0.16)',
                  padding: '7px 10px',
                  textAlign: index === 0 ? 'left' : 'right',
                  color: T.textSoft,
                  background: 'rgba(217,119,6,0.06)',
                  whiteSpace: 'nowrap',
                }}
              >
                {column}
              </th>
            ))}
            {dataset.dim_type === 'category' && dataset.agg !== 'avg' && dataset.agg !== 'ratio' && <th style={{ border: '1px solid rgba(180,130,70,0.16)', padding: '7px 10px', color: T.textSoft }}>占比</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((value, cellIndex) => (
                <td
                  key={cellIndex}
                  style={{
                    border: '1px solid rgba(180,130,70,0.14)',
                    padding: '6px 10px',
                    textAlign: cellIndex === 0 ? 'left' : 'right',
                    color: T.text,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value}
                </td>
              ))}
              {dataset.dim_type === 'category' && dataset.agg !== 'avg' && dataset.agg !== 'ratio' && (
                <td style={{ border: '1px solid rgba(180,130,70,0.14)', padding: '6px 10px', textAlign: 'right', color: T.textSoft }}>
                  {total && typeof row[1] === 'number' ? `${(((row[1] as number) / total) * 100).toFixed(1)}%` : '—'}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {dataset.row_count > 30 && (
        <div style={{ marginTop: 6, fontSize: 11.5, color: T.textFaint }}>仅展示前 30 行（共 {dataset.row_count} 行）</div>
      )}
    </div>
  )
}

export default function VizScreen({ taskId }: { taskId: string }) {
  const { message } = App.useApp()
  const [charts, setCharts] = useState<ChartSpec[]>([])
  const [datasets, setDatasets] = useState<DatasetSpec[]>([])
  const [insight, setInsight] = useState<InsightOut | null>(null)
  const [report, setReport] = useState('')
  const [reportTitle, setReportTitle] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setCharts([])
    setDatasets([])
    setInsight(null)
    setReport('')
    ;(async () => {
      try {
        const d = await getTaskDag(taskId)
        if (cancelled) return
        const viz = d.nodes.find((n) => n.agent_type === 'visualization' && n.output_asset_id)
        const ins = d.nodes.find((n) => n.agent_type === 'insight_analysis' && n.output_asset_id)
        const rep = d.nodes.find((n) => n.agent_type === 'report_writing' && n.output_asset_id)
        if (viz) {
          const content = await getAssetContent(viz.output_asset_id!)
          if (cancelled) return
          setCharts((content.charts ?? []) as ChartSpec[])
          const ds = (content.datasets ?? (content.dataset ? [content.dataset] : [])) as DatasetSpec[]
          setDatasets(ds)
        }
        if (ins) {
          const content = await getAssetContent(ins.output_asset_id!)
          if (cancelled) return
          setInsight(content as unknown as InsightOut)
        }
        if (rep) {
          const content = await getAssetContent(rep.output_asset_id!)
          if (cancelled) return
          if (typeof content.report === 'string') setReport(content.report)
          if (typeof content.title === 'string') setReportTitle(content.title)
        }
      } catch (e) {
        if (!cancelled) message.error((e as Error).message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  const chartMap = useMemo(() => Object.fromEntries(charts.map((chart) => [chart.id, chart])), [charts])
  const kpis = useMemo(() => {
    const seen = new Set<string>()
    return datasets
      .filter((dataset) => dataset.metric_name && (dataset.rows?.length ?? 0) > 0)
      .filter((dataset) => {
        const key = dataset.metric_id || dataset.metric_name || dataset.id
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .slice(0, 4)
  }, [datasets])
  const findings: InsightFinding[] = insight?.findings ?? []
  const hasContent = charts.length > 0 || datasets.length > 0 || report.length > 0

  if (loading) {
    return (
      <div style={{ ...PANEL, display: 'flex', justifyContent: 'center', alignItems: 'center', height: 260, color: T.textSoft }}>
        正在加载分析产物…
      </div>
    )
  }

  if (!hasContent) {
    return (
      <div style={{ ...PANEL, display: 'flex', justifyContent: 'center', alignItems: 'center', height: 260 }}>
        <Empty description={<span style={{ color: T.textSoft }}>该任务还没有可视化产物</span>} />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* KPI 总览 */}
      {kpis.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
          {kpis.map((dataset) => (
            <KpiCard key={dataset.id} dataset={dataset} />
          ))}
          <div style={{ ...PANEL, padding: '14px 16px' }}>
            <div style={{ fontSize: 12, color: T.textFaint }}>关键发现</div>
            <div style={{ marginTop: 6, fontSize: 24, fontWeight: 800, color: T.primaryDeep }}>{findings.length}</div>
            <div style={{ marginTop: 4, fontSize: 11.5, color: T.textSoft }}>图表 {charts.length} 张 · 数据集 {datasets.length} 组</div>
          </div>
        </div>
      )}

      {/* 洞察 */}
      {insight && (
        <div style={PANEL}>
          <PanelTitle text="关键洞察" dot={T.gold} extra={<Tag color="gold">{insight.dataset_count} 组数据</Tag>} />
          {insight.narrative && (
            <div style={{ marginBottom: 12, fontSize: 13, lineHeight: 1.9, color: T.text }}>{insight.narrative}</div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {findings.slice(0, 8).map((finding) => (
              <div
                key={finding.id}
                style={{
                  borderLeft: `3px solid ${severityColor(finding.severity)}`,
                  padding: '6px 12px',
                  background: 'rgba(255,253,248,0.5)',
                  borderRadius: 8,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 700, fontSize: 13, color: T.text }}>{finding.title}</span>
                  {finding.chart_ids.map((chartId) => (
                    <Tag key={chartId} color="orange" style={{ marginInlineEnd: 0 }}>
                      {chartId}
                    </Tag>
                  ))}
                </div>
                <div style={{ marginTop: 4, fontSize: 12.5, lineHeight: 1.8, color: T.textSoft }}>{finding.statement}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 图表网格 */}
      {charts.length > 0 && (
        <div style={PANEL}>
          <PanelTitle text="可视化图表" dot={T.primary} extra={<Tag color="orange">按数据特征选型</Tag>} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 14 }}>
            {charts.map((chart) => (
              <div
                key={chart.id}
                style={{
                  gridColumn: WIDE_TYPES.has(chart.type) || chart.type === 'kpi' ? 'span 2' : 'span 1',
                  border: '1px solid rgba(180,130,70,0.16)',
                  borderRadius: 12,
                  padding: '12px 10px 6px',
                  background: 'rgba(255,253,248,0.72)',
                  minWidth: 0,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, padding: '0 6px' }}>
                  <span style={{ fontSize: 13.5, fontWeight: 700, color: T.text }}>{chart.title}</span>
                  <span style={{ fontSize: 11, color: T.textFaint }}>{CHART_TYPE_LABEL[chart.type] ?? chart.type}</span>
                </div>
                {chart.rationale && (
                  <div style={{ padding: '2px 6px 4px', fontSize: 11, color: T.textFaint }}>{chart.rationale}</div>
                )}
                <ChartRenderer chart={chart} height={chart.type === 'kpi' ? 180 : 320} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 数据表 */}
      {datasets.length > 0 && (
        <div style={PANEL}>
          <PanelTitle text="数据明细" dot={T.green} extra={<Tag color="green">数字可复核</Tag>} />
          <Collapse
            ghost
            items={datasets.map((dataset) => ({
              key: dataset.id,
              label: (
                <span style={{ fontSize: 13, color: T.text }}>
                  {dataset.metric_name} · 按{dataset.dim_label}（{dataset.row_count} 行）
                </span>
              ),
              children: <DatasetTable dataset={dataset} />,
            }))}
          />
        </div>
      )}

      {/* 报告 */}
      {report && (
        <div style={PANEL}>
          <PanelTitle text={reportTitle || '分析报告'} dot={T.gold} extra={<Tag color="gold">图文表并茂</Tag>} />
          <div style={{ maxHeight: 640, overflowY: 'auto', paddingRight: 8 }}>
            <MarkdownView content={report} charts={chartMap} />
          </div>
        </div>
      )}
    </div>
  )
}
