/** 全局展示常量：任务状态、资产类型、可下载文档节点的统一标签与配色。
 *
 * 供任务看板 / 资产仓库 / 任务工作台等页面共享，避免各处重复定义漂移。
 */

/** 任务 / 节点状态 → antd Tag 颜色 */
export const STATUS_COLOR: Record<string, string> = {
  SUCCESS: 'success',
  FAILED: 'error',
  ABORTED: 'error',
  BLOCKED: 'error',
  PENDING: 'default',
  READY: 'default',
  CLAIMED: 'processing',
  RUNNING: 'processing',
}

/** 资产类型 → 中文标签 */
export const TYPE_LABEL: Record<string, string> = {
  metric_spec: '指标口径',
  profile: '探查报告',
  sql: 'SQL / 数据集',
  dataset: '数据集',
  dq_report: '质检报告',
  feature_set: '特征集',
  chart: '图表',
  insight: '洞察分析',
  report: '分析报告',
  review: '评审意见',
  retrospective: '复盘',
  trace: '溯源',
}

/** 可下载 / 可预览的文档类节点（终态后提供下载） */
export const DOC_LABEL: Record<string, string> = {
  report_writing: '分析报告',
  review: '评审意见',
  visualization: '图表',
  insight_analysis: '洞察分析',
  quality_check: '质检报告',
  metric_definition: '指标口径',
  metadata_profiling: '探查报告',
  sql_extract: 'SQL',
}

/** 任务终态 */
export const TERMINAL_STATUS = ['SUCCESS', 'FAILED', 'ABORTED'] as const

/** 图表类型中文名（工作台图例） */
export const CHART_TYPE_LABEL: Record<string, string> = {
  line: '折线图',
  bar: '柱状图',
  bar_horizontal: '条形图',
  bar_stacked: '堆叠柱',
  bar_grouped: '分组柱',
  pie: '饼图',
  donut: '环形图',
  rose: '玫瑰图',
  scatter: '散点图',
  heatmap: '热力图',
  radar: '雷达图',
  funnel: '漏斗图',
  treemap: '树图',
  boxplot: '箱线图',
  waterfall: '瀑布图',
  kpi: 'KPI',
}
