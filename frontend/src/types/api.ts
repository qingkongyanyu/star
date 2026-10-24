/** 后端 API 响应类型（与 backend/app/schemas 对齐）。 */

export interface UserInfo {
  id: number
  username: string
  role: 'admin' | 'analyst' | 'readonly'
  display_name?: string | null
}

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  user: UserInfo
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
}

export interface TaskOut {
  task_id: string
  task_type: string
  status: string
  priority: number
  requester: string
  create_time: string
}

export interface DAGNode {
  node_id: string
  agent_type: string
  status: string
  label: string
  output_asset_id?: string | null
  instruction?: string | null
}

export interface DAGEdge {
  from: string
  to: string
  type: string
}

export interface DAGOut {
  task_id: string
  status: string
  nodes: DAGNode[]
  edges: DAGEdge[]
}

export interface AssetOut {
  asset_id: string
  type: string
  title: string
  producer_agent: string
  status: string
  latest_version: number | null
  create_time: string
}

export interface AgentHealth {
  capability: string
  name: string
  status: string
  health_percent: number
}

export interface SummaryOut {
  total_tasks: number
  success_rate: number
  dq_block_rate: number
  fe_trigger_rate: number
  agent_health: AgentHealth[]
  assets_by_type: Record<string, number>
}

export interface TrendsOut {
  days: number
  items: { date: string; created: number }[]
}

export interface QualityOut {
  score: number
  dimensions: { name: string; value: number }[]
}

// ---- admin ----

export interface UserAdminOut {
  id: number
  username: string
  role: string
  display_name?: string | null
  is_active: boolean
  created_at: string
}

export interface ACLRuleOut {
  id: number
  role_or_user: string
  table: string
  action: string
  allow: boolean
}

export interface KBIndicatorOut {
  id: number
  indicator_key: string
  metric_name: string
  category?: string | null
  formula?: string | null
  description?: string | null
  tags?: string[] | null
  created_at: string
}

export interface AuditEntryOut {
  id: number
  ts: string
  actor: string
  action: string
  node_id?: string | null
  asset_id?: string | null
  detail?: Record<string, unknown> | null
  checksum: string
}

// ---- WebSocket ----

export interface WsEvent {
  type: string
  data: Record<string, unknown>
}

// ---- 数据源接入 ----

export type DataSourceType = 'mysql' | 'postgres' | 'olap' | 'file' | 'stream' | 'api'

export interface DataSourceItem {
  id: number
  name: string
  type: DataSourceType
  config: Record<string, unknown>
  status: string
  last_tested_at?: string | null
  created_by?: string | null
  created_at: string
  updated_at: string
}

export interface DataSourceTestResult {
  ok: boolean
  message: string
  tables: string[]
  columns: { name: string; type: string }[]
  schema_preview: Record<string, unknown>
}

/** CSV 上传结果：返回已注册的数据源（含 id），客户端可直接选中。 */
export interface FileUploadResult {
  data_source: DataSourceItem
  columns: { name: string; type: string }[]
  sample_rows: string[][]
  row_count: number
}

// ---- v3 分析产物（洞察 / 多数据集 / 多图型） ----

/** 统一数据集（取数 Agent 产出，DQ/FE/可视化/洞察/报告共用） */
export interface DatasetSpec {
  id: string
  task_id?: string | null
  kind?: string
  metric_id?: string | null
  metric_name?: string
  metric_formula?: string
  unit?: string
  agg?: string
  metric_kind?: string
  dim?: string | null
  dim_type?: string
  dim_label?: string
  granularity?: string | null
  columns: string[]
  rows: (string | number | null)[][]
  row_count: number
  source_row_count?: number
  table?: string
  sql?: string
  truncated?: boolean
  error?: string | null
  stats?: Record<string, number>
  points?: [number, number][] | null
  notes?: string[]
  filters?: Record<string, unknown>[]
}

/** 图表（服务端给出 ECharts option + 通用数据模型 + 选型理由） */
export interface ChartSpec {
  id: string
  type: string
  title: string
  subtitle?: string
  dataset_id?: string | null
  encoding?: Record<string, unknown>
  data?: { categories?: string[]; series?: { name: string; values: number[] }[] }
  option?: Record<string, unknown>
  points?: [number, number][]
  columns?: string[]
  rationale?: string
  insight?: string
  unit?: string
  money?: boolean
}

/** 洞察发现（带证据与图表引用） */
export interface InsightFinding {
  id: string
  dataset_id?: string | null
  kind: string
  title: string
  statement: string
  evidence: string[]
  severity: 'info' | 'warning' | 'critical' | string
  chart_ids: string[]
  metric?: string | null
  unit?: string
  value?: number | null
}

/** 指标口径（多指标） */
export interface MetricSpecOut {
  id?: string
  name: string
  formula: string
  agg?: string
  column?: string | null
  unit?: string
  kind?: string
  feasible?: boolean
  reason?: string
  derived?: boolean
  description?: string
  kb_formula_ignored?: boolean
  kb_formula_note?: string
}

export interface InsightOut {
  summary: string
  narrative?: string
  headline: string[]
  findings: InsightFinding[]
  dataset_count: number
  chart_count: number
  title?: string
}

/** 报告资产（v3 额外字段） */
export interface ReportOut {
  report: string
  report_export_md?: string
  title?: string
  figure_index?: { number: number; chart_id: string; caption: string; chart_type?: string; dataset_id?: string }[]
  table_index?: { number: number; title: string; dataset_id?: string | null }[]
  sections?: { number: number; title: string; kind?: string; dataset_id?: string }[]
}

// ---- memory ----

export interface MemoryHitOut {
  id: number
  agent_id: string
  scope: string
  source_task?: string | null
  task_type?: string | null
  content: string
  experience_type: string
  tags?: string[] | null
  created_ts: string
  ttl_ts?: string | null
  access_count: number
}

