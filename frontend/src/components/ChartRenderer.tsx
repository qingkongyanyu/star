/** 通用图表渲染器：优先使用服务端下发的 ECharts option（含多图型），
 *  缺失时按通用数据模型降级构建；KPI 类型渲染为指标卡。
 */

import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'

import { T } from '../config/theme'
import type { ChartSpec } from '../types/api'
import { useECharts } from './useECharts'

const PALETTE = ['#d97706', '#ea580c', '#b45309', '#f59e0b', '#9a3412', '#ca8a04', '#7c2d12', '#a16207', '#dc2626', '#0f766e']

function fmt(value: number): string {
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(1)}万`
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function fallbackOption(chart: ChartSpec): EChartsOption {
  const categories = chart.data?.categories ?? []
  const values = chart.data?.series?.[0]?.values ?? []
  const base: EChartsOption = {
    animationDuration: 700,
    color: PALETTE,
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 24, top: 40, bottom: 56, containLabel: true },
  }
  if (chart.type === 'scatter') {
    const points = chart.points ?? []
    return {
      ...base,
      tooltip: { trigger: 'item' },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(180,130,70,0.12)' } } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(180,130,70,0.12)' } } },
      series: [{ type: 'scatter', data: points, symbolSize: 8, itemStyle: { color: T.primary } }],
    }
  }
  if (chart.type === 'pie' || chart.type === 'donut') {
    return {
      ...base,
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, textStyle: { color: T.textSoft } },
      series: [
        {
          type: 'pie',
          radius: chart.type === 'donut' ? ['40%', '66%'] : '66%',
          data: categories.map((name, index) => ({ name, value: values[index] ?? 0 })),
          label: { formatter: '{b}\n{d}%', color: T.textSoft },
        },
      ],
    }
  }
  if (chart.type === 'bar_horizontal') {
    return {
      ...base,
      xAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(180,130,70,0.12)' } } },
      yAxis: { type: 'category', data: categories, inverse: true, axisLabel: { color: T.textSoft } },
      series: [{ type: 'bar', data: values, barMaxWidth: 26, itemStyle: { color: T.primary, borderRadius: [0, 6, 6, 0] } }],
    }
  }
  if (chart.type === 'line') {
    return {
      ...base,
      xAxis: { type: 'category', data: categories, boundaryGap: false, axisLabel: { color: T.textSoft } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(180,130,70,0.12)' } } },
      series: [
        {
          type: 'line',
          data: values,
          smooth: false,
          symbolSize: 7,
          lineStyle: { width: 2.5, color: T.primary },
          areaStyle: { color: 'rgba(217,119,6,0.12)' },
        },
      ],
    }
  }
  return {
    ...base,
    xAxis: { type: 'category', data: categories, axisLabel: { color: T.textSoft, rotate: categories.length > 6 ? 30 : 0 } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(180,130,70,0.12)' } } },
    series: [{ type: 'bar', data: values, barMaxWidth: 34, itemStyle: { color: T.primary, borderRadius: [6, 6, 0, 0] } }],
  }
}

function KpiCard({ chart }: { chart: ChartSpec }) {
  const values = chart.data?.series?.[0]?.values ?? []
  const total = values.reduce((sum, value) => sum + value, 0)
  const prefix = chart.money ? '¥' : ''
  const unit = chart.unit ?? ''
  return (
    <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', height: 180, padding: '4px 8px' }}>
      <div style={{ fontSize: 12, color: T.textFaint }}>{chart.title}</div>
      <div style={{ marginTop: 10, fontSize: 30, fontWeight: 800, color: T.primaryDeep }}>
        {prefix}
        {fmt(total)}
        <span style={{ fontSize: 13, fontWeight: 500, color: T.textSoft, marginLeft: 4 }}>{unit}</span>
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: T.textSoft }}>{chart.insight || '总量指标（KPI）'}</div>
    </div>
  )
}

export default function ChartRenderer({ chart, height = 340 }: { chart: ChartSpec; height?: number }) {
  const option = useMemo<EChartsOption>(() => {
    if (chart.type === 'kpi') return {}
    let base: Record<string, unknown>
    if (chart.option && Object.keys(chart.option).length > 0) {
      const { title: _title, ...rest } = chart.option as Record<string, unknown>
      base = { ...rest, backgroundColor: 'transparent' }
    } else {
      base = fallbackOption(chart) as Record<string, unknown>
    }
    // 数值轴/tooltip 统一做千分位与「万」缩写（服务端 option 不能带函数，前端补格式化）
    for (const axisKey of ['xAxis', 'yAxis'] as const) {
      const axis = base[axisKey] as Record<string, unknown> | undefined
      if (axis && axis.type === 'value') {
        axis.axisLabel = { ...(axis.axisLabel as Record<string, unknown> | undefined), formatter: (value: number) => fmt(value) }
      }
    }
    const tooltip = base.tooltip as Record<string, unknown> | undefined
    if (tooltip) {
      tooltip.valueFormatter = (value: number) => fmt(value)
    }
    return base as EChartsOption
  }, [chart])
  const ref = useECharts(option)
  if (chart.type === 'kpi') return <KpiCard chart={chart} />
  return <div ref={ref} style={{ width: '100%', height }} />
}
