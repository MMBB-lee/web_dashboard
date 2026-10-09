import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ComputedRef } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import type { EChartsType } from 'echarts/core'

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

export function useChart(option: ComputedRef<EChartsOption>) {
  const element = ref<HTMLElement | null>(null)
  let instance: EChartsType | null = null
  let observer: ResizeObserver | null = null

  onMounted(() => {
    if (!element.value) return
    instance = echarts.init(element.value, undefined, { renderer: 'canvas' })
    instance.setOption(option.value, true)
    observer = new ResizeObserver(() => instance?.resize())
    observer.observe(element.value)
  })
  watch(option, (next) => instance?.setOption(next, true))
  onBeforeUnmount(() => {
    observer?.disconnect()
    instance?.dispose()
  })
  return element
}
