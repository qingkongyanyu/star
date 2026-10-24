/** 数据大屏：KPI 总览 + 任务趋势 + 质量雷达 + Agent 健康 + 资产分布。秋季暖色调。 */

import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { App, Empty } from 'antd'
import type { EChartsOption } from 'echarts'

import { getQuality, getSummary, getTrends } from '../../api/dashboard'
import { useECharts } from '../../components/useECharts'
import { AGENTS } from '../../config/agents'
import { ASSETS } from '../../config/assets'
import { T } from '../../config/theme'
import type { AgentHealth, QualityOut, SummaryOut, TrendsOut } from '../../types/api'

/* ---------- 风格常量（暖色浅底） ---------- */
const PANEL: CSSProperties = {
  background: T.screenPanel,
  border: T.screenBorder,
  borderRadius: 14,
  backdropFilter: 'blur(10px)',
  WebkitBackdropFilter: 'blur(10px)',
  boxShadow: T.screenShadow,
  padding: '18px 20px',
}
const KPI_ACCENT: Record<string, { color: string; glow: string }> = {
  total: { color: T.gold, glow: 'rgba(217,119,6,0.16)' },
  success: { color: T.green, glow: 'rgba(21,128,61,0.14)' },
  dq: { color: T.red, glow: 'rgba(220,38,38,0.13)' },
  fe: { color: T.blue, glow: 'rgba(29,78,216,0.14)' },
}

function PanelTitle({ text, dot }: { text: string; dot?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
      <span style={{ width: 8, height: 8, borderRadius: 4, background: dot ?? T.primary, boxShadow: `0 0 8px ${dot ?? T.primary}` }} />
      <span style={{ color: T.text, fontSize: 14, fontWeight: 600, letterSpacing: 0.5 }}>{text}</span>
    </div>
  )
}

/* ---------- Agent 健康状态 ---------- */
function healthMeta(h: AgentHealth | undefined, hasTasks: boolean) {
  if (!h || !hasTasks || h.status === 'online') return { label: '待命中', color: '#9a8a70', pct: null as number | null }
  if (h.status === 'healthy') return { label: '正常', color: T.green, pct: h.health_percent }
  if (h.status === 'warning') return { label: '异常', color: T.gold, pct: h.health_percent }
  return { label: '故障', color: T.red, pct: h.health_percent }
}

export default function BigScreen() {
  const { message } = App.useApp()
  const [summary, setSummary] = useState<SummaryOut | null>(null)
  const [trends, setTrends] = useState<TrendsOut | null>(null)
  const [quality, setQuality] = useState<QualityOut | null>(null)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    Promise.all([getSummary(), getTrends(14), getQuality()])
      .then(([s, t, q]) => {
        setSummary(s)
        setTrends(t)
        setQuality(q)
      })
      .catch((e) => message.error((e as Error).message))
  }, [message])

  const totalTasks = summary?.total_tasks ?? 0
  const hasTasks = totalTasks > 0
  const timeText = now.toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')

  /* 折线：近 14 日任务趋势 */
  const lineOption = useMemo<EChartsOption>(
    () => ({
      grid: { left: 44, right: 20, top: 24, bottom: 28 },
      tooltip: { trigger: 'axis', backgroundColor: '#fffdf8', borderColor: 'rgba(180,130,70,0.3)', textStyle: { color: T.text } },
      xAxis: {
        type: 'category',
        data: trends?.items.map((i) => i.date.slice(5)) ?? [],
        axisLine: { lineStyle: { color: 'rgba(180,130,70,0.35)' } },
        axisLabel: { color: T.textSoft },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: 'rgba(180,130,70,0.12)' } },
        axisLabel: { color: T.textSoft },
      },
      series: [
        {
          type: 'line' as const,
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          data: trends?.items.map((i) => i.created) ?? [],
          lineStyle: { width: 3, color: T.accent },
          itemStyle: { color: T.gold },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(217,119,6,0.30)' },
                { offset: 1, color: 'rgba(217,119,6,0.02)' },
              ],
            },
          },
        },
      ],
    }),
    [trends],
  )
  const lineRef = useECharts(lineOption)

  /* 雷达：数据质量 */
  const radarOption = useMemo<EChartsOption>(
    () => ({
      tooltip: { backgroundColor: '#fffdf8', borderColor: 'rgba(180,130,70,0.3)', textStyle: { color: T.text } },
      radar: {
        indicator: quality?.dimensions.map((d) => ({ name: d.name, max: 100 })) ?? [],
        radius: '62%',
        axisName: { color: T.textSoft, fontSize: 12 },
        splitLine: { lineStyle: { color: 'rgba(180,130,70,0.2)' } },
        splitArea: { areaStyle: { color: ['rgba(217,119,6,0.03)', 'rgba(217,119,6,0.08)'] } },
        axisLine: { lineStyle: { color: 'rgba(180,130,70,0.3)' } },
      },
      series: [
        {
          type: 'radar' as const,
          data: [
            {
              value: quality?.dimensions.map((d) => d.value) ?? [],
              name: '质量分',
              areaStyle: { color: 'rgba(21,128,61,0.20)' },
              lineStyle: { color: T.green, width: 2 },
              itemStyle: { color: T.green },
            },
          ],
        },
      ],
    }),
    [quality],
  )
  const radarRef = useECharts(radarOption)

  const renderEmpty = (text: string) => (
    <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ color: T.textFaint }}>{text}</span>} />
    </div>
  )

  return (
    <div style={{ minHeight: 'calc(100vh - 120px)', borderRadius: 16, padding: 8 }}>
      {/* ===== 顶部标题栏 ===== */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <img src={ASSETS.logo} alt="logo" style={{ width: 40, height: 40, borderRadius: 10, objectFit: 'cover', boxShadow: '0 4px 14px rgba(120,80,30,0.25)' }} />
          <div>
            <div style={{ color: T.text, fontSize: 20, fontWeight: 700, letterSpacing: 2 }}>数据大屏</div>
            <div style={{ color: T.textFaint, fontSize: 12 }}>企业级智能数据分析中台 · 实时总览</div>
          </div>
        </div>
        <div style={{ color: T.textSoft, fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>{timeText}</div>
      </div>

      {/* ===== KPI 行 ===== */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 16 }}>
        {(
          [
            { key: 'total', label: '任务总量', value: String(totalTasks), suffix: '', sub: hasTasks ? '累计发布任务' : '暂无任务' },
            { key: 'success', label: '任务成功率', value: hasTasks ? String(summary?.success_rate ?? 0) : '—', suffix: hasTasks ? '%' : '', sub: hasTasks ? '成功 / 全部任务' : '暂无数据' },
            { key: 'dq', label: 'DQ 阻断率', value: hasTasks ? String(summary?.dq_block_rate ?? 0) : '—', suffix: hasTasks ? '%' : '', sub: hasTasks ? '质量阻断占比' : '暂无数据' },
            { key: 'fe', label: 'FE 触发率', value: hasTasks ? String(summary?.fe_trigger_rate ?? 0) : '—', suffix: hasTasks ? '%' : '', sub: hasTasks ? '特征工程触发占比' : '暂无数据' },
          ] as const
        ).map(({ key, label, value, suffix, sub }) => {
          const a = KPI_ACCENT[key] ?? { color: T.gold, glow: 'rgba(217,119,6,0.16)' }
          return (
            <div key={key} style={{ ...PANEL, position: 'relative', overflow: 'hidden' }}>
              <div style={{ position: 'absolute', right: -30, top: -30, width: 110, height: 110, borderRadius: '50%', background: a.glow, filter: 'blur(30px)' }} />
              <div style={{ color: T.textFaint, fontSize: 13, marginBottom: 8 }}>{label}</div>
              <div style={{ fontSize: 32, fontWeight: 800, color: a.color, fontVariantNumeric: 'tabular-nums' }}>
                {value}
                <span style={{ fontSize: 18, marginLeft: 2 }}>{suffix}</span>
              </div>
              <div style={{ color: T.textFaint, fontSize: 12, marginTop: 6 }}>{sub}</div>
            </div>
          )
        })}
      </div>

      {/* ===== 趋势 + 质量雷达 ===== */}
      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 16, marginBottom: 16 }}>
        <div style={PANEL}>
          <PanelTitle text="近 14 日任务趋势" dot={T.accent} />
          {hasTasks ? <div ref={lineRef} style={{ height: 280 }} /> : renderEmpty('发布任务后展示趋势')}
        </div>
        <div style={PANEL}>
          <PanelTitle text="数据质量雷达" dot={T.green} />
          {quality && quality.dimensions.some((d) => d.value > 0) ? <div ref={radarRef} style={{ height: 280 }} /> : renderEmpty('暂无质检报告')}
        </div>
      </div>

      {/* ===== Agent 健康墙 ===== */}
      <div style={{ ...PANEL, marginBottom: 16 }}>
        <PanelTitle text="Agent 健康墙" dot={T.blue} />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
          {AGENTS.map((a) => {
            const h = summary?.agent_health.find((x) => x.capability === a.capability)
            const m = healthMeta(h, hasTasks)
            return (
              <div
                key={a.capability}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  background: 'rgba(255,255,255,0.7)',
                  border: '1px solid rgba(180,130,70,0.18)',
                  borderRadius: 12,
                  padding: '10px 12px',
                  transition: 'transform .15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-2px)')}
                onMouseLeave={(e) => (e.currentTarget.style.transform = 'none')}
              >
                <img
                  src={a.avatar}
                  alt={a.name}
                  style={{ width: 42, height: 42, borderRadius: '50%', objectFit: 'cover', border: `2px solid ${m.color}`, boxShadow: `0 0 10px ${m.color}55` }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: T.text, fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.name}</div>
                  <div style={{ fontSize: 11, color: m.color, marginTop: 2 }}>
                    {m.pct === null ? m.label : `${m.label} · ${m.pct}%`}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* ===== 资产分布 ===== */}
      <div style={PANEL}>
        <PanelTitle text="资产分布" dot={T.purple} />
        {summary && Object.keys(summary.assets_by_type).length > 0 ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
            {Object.entries(summary.assets_by_type).map(([k, v]) => (
              <span
                key={k}
                style={{
                  padding: '6px 14px',
                  borderRadius: 999,
                  background: 'rgba(126,34,206,0.08)',
                  border: '1px solid rgba(126,34,206,0.25)',
                  color: '#6b21a8',
                  fontSize: 13,
                }}
              >
                {k} · <b>{v}</b>
              </span>
            ))}
          </div>
        ) : (
          <div style={{ color: T.textFaint, fontSize: 13 }}>暂无资产产出，发布任务后在此聚合制品分布</div>
        )}
      </div>
    </div>
  )
}
