import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ComputedRef } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import type { EChartsType } from 'echarts/core'

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

export function useChart(option: ComputedRef<EChartsOption>) {
  const element = ref<HTMLElement | null>(null)
  let instance: EChartsType | null = null
  let observer: ResizeObserver | null = null

  function draw(next: EChartsOption) {
    instance?.setOption({
      ...next,
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      animationEasing: 'cubicOut',
      animationDurationUpdate: 800,
      animationEasingUpdate: 'cubicOut',
    }, true)
  }

  onMounted(() => {
    if (!element.value) return
    instance = echarts.init(element.value, undefined, { renderer: 'canvas' })
    draw(option.value)
    observer = new ResizeObserver(() => instance?.resize())
    observer.observe(element.value)
  })
  watch(option, draw)
  onBeforeUnmount(() => {
    observer?.disconnect()
    instance?.dispose()
  })
  return element
}
