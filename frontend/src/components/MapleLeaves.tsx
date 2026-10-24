/** 登录页枫叶漂浮层。
 *
 * 设计要点：
 * - 纯 CSS 动画（translate3d + 自转 + 翻面），不依赖 JS 帧循环，低开销；
 * - 三个景深层（远/中/近）分别控制尺寸、透明度、模糊与速度，形成景深；
 * - 叶片形状用内联 SVG（五裂枫叶），颜色取自秋季色板；
 * - `prefers-reduced-motion` 下整层隐藏（背景图本身已有枫叶，不丢主题）。
 */

import type { CSSProperties } from 'react'

type Layer = 'far' | 'mid' | 'near'

interface Leaf {
  left: number
  size: number
  delay: number
  duration: number
  drift: number
  spin: number
  opacity: number
  color: string
  layer: Layer
}

const COLORS = ['#b91c1c', '#c2410c', '#ea580c', '#d97706', '#f59e0b', '#dc2626']

/** 确定性伪随机（避免 StrictMode 双渲染导致叶片跳位）。 */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const LEAVES: Leaf[] = (() => {
  const rng = mulberry32(20261019)
  const layers: Array<{ layer: Layer; count: number; size: [number, number]; opacity: [number, number]; duration: [number, number] }> = [
    { layer: 'far', count: 9, size: [12, 20], opacity: [0.22, 0.4], duration: [22, 34] },
    { layer: 'mid', count: 8, size: [20, 30], opacity: [0.42, 0.62], duration: [15, 22] },
    { layer: 'near', count: 6, size: [28, 42], opacity: [0.62, 0.86], duration: [10, 16] },
  ]
  const out: Leaf[] = []
  for (const spec of layers) {
    for (let i = 0; i < spec.count; i += 1) {
      const [minSize, maxSize] = spec.size
      const [minOpacity, maxOpacity] = spec.opacity
      const [minDuration, maxDuration] = spec.duration
      out.push({
        left: rng() * 100,
        size: minSize + rng() * (maxSize - minSize),
        delay: -rng() * 40,
        duration: minDuration + rng() * (maxDuration - minDuration),
        drift: (rng() - 0.5) * 180,
        spin: 240 + rng() * 480,
        opacity: minOpacity + rng() * (maxOpacity - minOpacity),
        color: COLORS[Math.floor(rng() * COLORS.length)] ?? COLORS[0]!,
        layer: spec.layer,
      })
    }
  }
  return out
})()

function LeafSvg({ color, size, opacity }: { color: string; size: number; opacity: number }) {
  return (
    <svg
      width={size}
      height={size * 0.84}
      viewBox="110 160 290 240"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden
      style={{ display: 'block', opacity, filter: 'drop-shadow(0 2px 3px rgba(60,20,0,0.25))' }}
    >
      {/* 枫叶轮廓取自加拿大国旗（公有领域）的 11 尖枫叶路径，仅保留叶片子路径 */}
      <path
        d="M135.3 247.4l-14 4.8 65.4 57.5c5 14.8-1.7 19.1-6 26.9l71-9-1.8 71.5 14.8-.5-3.3-70.9 71.2 8.4c-4.4-9.3-8.3-14.2-4.3-29l65.4-54.5-11.4-4.1c-9.4-7.3 4-34.8 6-52.2 0 0-38.1 13.1-40.6 6.2l-9.9-18.5-34.6 38c-3.8 1-5.4-.6-6.3-3.8l16-79.7-25.4 14.3c-2.1.9-4.2 0-5.6-2.4l-24.5-49-25.2 50.9c-1.9 1.8-3.8 2-5.4.8l-24.2-13.6 14.5 79.2c-1.1 3-3.9 4-7.1 2.3l-33.3-37.8c-4.3 7-7.3 18.4-13 21-5.7 2.3-25-4.9-37.9-7.7 4.4 15.9 18.2 42.3 9.5 51z"
        fill={color}
        stroke="rgba(90,20,0,0.16)"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export default function MapleLeaves() {
  return (
    <div className="sp-leaves" aria-hidden>
      {LEAVES.map((leaf, index) => (
        <span
          key={index}
          className={`sp-leaf sp-leaf--${leaf.layer}`}
          style={
            {
              left: `${leaf.left}%`,
              animationDelay: `${leaf.delay}s`,
              animationDuration: `${leaf.duration}s`,
              '--drift': `${leaf.drift}px`,
              '--spin': `${leaf.spin}deg`,
            } as CSSProperties
          }
        >
          <span
            className="sp-leaf__sway"
            style={
              {
                animationDelay: `${leaf.delay / 2}s`,
                animationDuration: `${leaf.duration * 0.6}s`,
                '--sway': `${leaf.drift * 0.35}px`,
              } as CSSProperties
            }
          >
            <span className="sp-leaf__flip" style={{ animationDuration: `${leaf.duration * 0.45}s` }}>
              <LeafSvg color={leaf.color} size={leaf.size} opacity={leaf.opacity} />
            </span>
          </span>
        </span>
      ))}
    </div>
  )
}
