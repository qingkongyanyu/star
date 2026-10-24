/** DAG 图可视化：拓扑深度列布局的 SVG 渲染（无 G6 依赖）。 */

import { useMemo } from 'react'

import type { DAGOut } from '../types/api'

const STATUS_COLOR: Record<string, string> = {
  PENDING: '#b09a7a',
  READY: '#1d4ed8',
  CLAIMED: '#0d9488',
  RUNNING: '#d97706',
  SUCCESS: '#15803d',
  FAILED: '#dc2626',
  BLOCKED: '#dc2626',
}
const NODE_W = 150
const NODE_H = 44
const COL_GAP = 70
const ROW_GAP = 28

export default function DagGraph({ dag }: { dag: DAGOut }) {
  const { edges, pos, width, height } = useMemo(() => {
    const edgesFrom: Record<string, string[]> = {}
    const inDeg: Record<string, number> = {}
    for (const n of dag.nodes) {
      inDeg[n.node_id] = 0
    }
    for (const e of dag.edges) {
      ;(edgesFrom[e.from] ??= []).push(e.to)
      inDeg[e.to] = (inDeg[e.to] ?? 0) + 1
    }

    // 拓扑深度 = 到根的最长路径
    const depth: Record<string, number> = {}
    for (const n of dag.nodes) depth[n.node_id] = 0
    const stack = dag.nodes.filter((n) => !inDeg[n.node_id]).map((n) => n.node_id)
    while (stack.length) {
      const id = stack.pop()!
      for (const to of edgesFrom[id] ?? []) {
        depth[to] = Math.max(depth[to] ?? 0, (depth[id] ?? 0) + 1)
        inDeg[to] = (inDeg[to] ?? 0) - 1
        if (inDeg[to] === 0) stack.push(to)
      }
    }

    // 列 = 深度，行 = 列内序号
    const cols: Record<number, string[]> = {}
    for (const n of dag.nodes) {
      const d = depth[n.node_id] ?? 0
      cols[d] = cols[d] || []
      cols[d].push(n.node_id)
    }
    const positions: Record<string, { x: number; y: number }> = {}
    let maxCols = 1
    let maxRows = 1
    for (const [dStr, ids] of Object.entries(cols)) {
      const d = Number(dStr)
      maxCols = Math.max(maxCols, d + 1)
      maxRows = Math.max(maxRows, ids.length)
      ids.forEach((id, i) => {
        positions[id] = { x: d * (NODE_W + COL_GAP), y: i * (NODE_H + ROW_GAP) }
      })
    }
    return {
      edges: dag.edges,
      pos: positions,
      width: maxCols * (NODE_W + COL_GAP),
      height: maxRows * (NODE_H + ROW_GAP),
    }
  }, [dag])

  const nodeById = useMemo(() => new Map(dag.nodes.map((n) => [n.node_id, n])), [dag])

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg width={width} height={height} style={{ minWidth: width }}>
        {edges.map((e, i) => {
          const s = pos[e.from]
          const t = pos[e.to]
          if (!s || !t) return null
          const x1 = s.x + NODE_W
          const y1 = s.y + NODE_H / 2
          const x2 = t.x
          const y2 = t.y + NODE_H / 2
          const mid = (x1 + x2) / 2
          return (
            <path
              key={`${e.from}-${e.to}-${i}`}
              d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke={e.type === 'quality_block' ? '#dc2626' : '#b3936a'}
              strokeWidth={1.5}
            />
          )
        })}
        {dag.nodes.map((n) => {
          const p = pos[n.node_id]
          if (!p) return null
          const color = STATUS_COLOR[n.status] || '#b09a7a'
          return (
            <g key={n.node_id}>
              <rect
                x={p.x}
                y={p.y}
                width={NODE_W}
                height={NODE_H}
                rx={6}
                fill="#fffdf8"
                stroke={color}
                strokeWidth={1.5}
              />
              <text
                x={p.x + NODE_W / 2}
                y={p.y + NODE_H / 2 - 1}
                textAnchor="middle"
                fill="#3d2c1d"
                fontSize={13}
              >
                {n.label}
              </text>
              <text
                x={p.x + NODE_W / 2}
                y={p.y + NODE_H / 2 + 13}
                textAnchor="middle"
                fill={color}
                fontSize={11}
              >
                {n.status}
                {nodeById.get(n.node_id)?.output_asset_id ? ` · ${nodeById.get(n.node_id)?.output_asset_id}` : ''}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
