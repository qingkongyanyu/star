/** ECharts 小封装：init / setOption / resize / 卸载销毁。
 *
 * 用「回调 ref」而非空依赖 useEffect 初始化：容器可能是条件渲染（数据到达后才挂载），
 * 空依赖 effect 在首屏 ref.current 为 null 时提前 return，之后容器出现也不会再初始化。
 * 回调 ref 在节点挂载/卸载时都会触发，天然兼容条件渲染。
 */

import { useCallback, useEffect, useRef } from 'react'
import * as echarts from 'echarts'

export function useECharts(option: echarts.EChartsOption) {
  const chartRef = useRef<echarts.ECharts | null>(null)
  const resizeRef = useRef<(() => void) | null>(null)
  const optionRef = useRef(option)
  optionRef.current = option

  const ref = useCallback((node: HTMLDivElement | null) => {
    if (node) {
      if (chartRef.current) return
      const chart = echarts.init(node)
      chartRef.current = chart
      chart.setOption(optionRef.current, true)
      const onResize = () => chart.resize()
      resizeRef.current = onResize
      window.addEventListener('resize', onResize)
    } else if (chartRef.current) {
      window.removeEventListener('resize', resizeRef.current ?? (() => {}))
      chartRef.current.dispose()
      chartRef.current = null
      resizeRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, true)
  }, [option])

  return ref
}
