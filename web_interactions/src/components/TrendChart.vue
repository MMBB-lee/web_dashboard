<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { graphic, init, use, type EChartsType } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, MarkAreaComponent, AriaComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Point } from '../types/api'
use([LineChart, GridComponent, TooltipComponent, MarkAreaComponent, AriaComponent, CanvasRenderer])
type ChartPoint = Pick<Point, 'value' | 'unit' | 'period_start' | 'period_end' | 'lower_bound' | 'upper_bound'> & { grain?: string; offset_seconds?: number }
const props = defineProps<{ points: ChartPoint[]; forecast?: ChartPoint[]; label?: string; short?: boolean; relative?: boolean; historyLabel?: string; unit?: string }>()
const host = ref<HTMLDivElement>()
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
const all = computed(() => [...props.points, ...(props.forecast ?? [])])
const description = computed(() => `${props.label || '客流'}趋势图，包含 ${props.points.length} 个${props.historyLabel || '历史'}数据点和 ${props.forecast?.length || 0} 个预测数据点；详细数值见下方数据表`)
const divisor = computed(() => !props.unit && all.value.some(p => (p.value ?? 0) >= 10000) ? 10000 : 1)
function draw() {
  if (!chart) return
  const historyLength = props.points.length
  const value = (p: ChartPoint, field: 'value' | 'lower_bound' | 'upper_bound') => p[field] == null ? null : p[field]! / divisor.value
  const labels = all.value.map(p => props.relative ? p.period_end : props.short ? p.period_end.slice(11, 19) : p.grain === 'year' ? `${p.period_start.slice(0, 4)}年` : p.grain === 'month' ? p.period_start.slice(0, 7) : p.period_start.slice(0, 10))
  const datum = (p: ChartPoint, amount: number | null) => props.relative ? [p.offset_seconds, amount] : props.short ? [Date.parse(p.period_end), amount] : amount
  chart.setOption({
    animation: typeof window !== 'undefined' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    animationDuration: 900, animationEasing: 'cubicOut',
    aria: { enabled: true, label: { description: description.value } }, backgroundColor: 'transparent',
    grid: { top: 35, left: 14, right: 36, bottom: 27, containLabel: true },
    tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: 'rgba(7, 23, 49, .96)', borderColor: 'rgba(0, 212, 255, .45)', borderRadius: 0, textStyle: { color: '#e6f7ff', fontSize: 13 }, formatter: (params: unknown) => {
      const items = params as { dataIndex: number }[]
      const index = items[0]?.dataIndex ?? 0, p = all.value[index]
      if (!p) return ''
      return `${labels[index]} · ${index >= historyLength ? '预测' : props.historyLabel || '历史'}\n${p.value == null ? '缺失' : (p.value / divisor.value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${divisor.value === 10000 ? '万' : ''}${props.unit || p.unit}${p.lower_bound != null && p.upper_bound != null ? `\n区间 ${(p.lower_bound / divisor.value).toFixed(1)} – ${(p.upper_bound / divisor.value).toFixed(1)}` : ''}`
    } },
    xAxis: { type: props.relative ? 'value' : props.short ? 'time' : 'category', boundaryGap: false, ...(!props.short && !props.relative ? { data: labels } : {}), axisLine: { lineStyle: { color: 'rgba(117, 167, 210, .3)' } }, axisTick: { show: false }, axisLabel: { color: '#a2bfda', fontSize: 12, hideOverlap: true, margin: 14, ...(props.relative ? { formatter: (seconds: number) => `${Math.floor(seconds / 60)}分${Math.floor(seconds % 60)}秒` } : props.short ? { formatter: (time: number) => new Date(time + 8 * 3600000).toISOString().slice(11, 16) } : {}) } },
    yAxis: { type: 'value', name: (divisor.value === 10000 ? '万' : '') + (props.unit || all.value[0]?.unit || '人次'), nameTextStyle: { color: '#a2bfda', fontSize: 12 }, axisLabel: { color: '#a2bfda', fontSize: 12 }, splitLine: { lineStyle: { color: 'rgba(117, 167, 210, .11)' } } },
    series: [
      { name: props.label || '历史值', type: 'line', data: all.value.map((p, i) => datum(p, i < historyLength ? value(p, 'value') : null)), smooth: .25, connectNulls: false, symbol: 'circle', symbolSize: props.points.length === 1 ? 10 : 6, showSymbol: props.points.length === 1, lineStyle: { color: '#00d4ff', width: 2.5, shadowColor: 'rgba(0, 212, 255, .36)', shadowBlur: 10 }, itemStyle: { color: '#00d4ff' }, areaStyle: { color: new graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(0, 212, 255, .32)' }, { offset: 1, color: 'rgba(0, 212, 255, .015)' }]) } },
      { name: '区间下界', type: 'line', data: all.value.map((p, i) => datum(p, i >= historyLength ? value(p, 'lower_bound') : null)), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true },
      { name: '预测区间', type: 'line', data: all.value.map((p, i) => datum(p, i >= historyLength && p.lower_bound != null && p.upper_bound != null ? (p.upper_bound - p.lower_bound) / divisor.value : null)), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: '#688cae', opacity: .16 }, silent: true },
      { name: '预测值', type: 'line', data: all.value.map((p, i) => datum(p, i >= historyLength ? value(p, 'value') : null)), smooth: .25, symbolSize: 5, lineStyle: { color: '#ffb84d', width: 2, type: 'dashed' }, itemStyle: { color: '#ffb84d' } },
    ],
  }, true)
}
onMounted(() => { if (host.value) { chart = init(host.value); observer = new ResizeObserver(() => chart?.resize()); observer.observe(host.value); draw() } })
watch(() => [props.points, props.forecast, props.short, props.relative, props.historyLabel, props.unit], draw, { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>
<template>
  <div ref="host" class="trend-chart" role="img" :aria-label="description" />
  <details class="chart-data"><summary>查看图表数据</summary><div class="table-scroll"><table><thead><tr><th>统计时间</th><th>类型</th><th>数值</th><th>单位</th><th>区间</th></tr></thead><tbody><tr v-for="(p, i) in all" :key="`${p.period_start}-${i}`"><td>{{ short ? p.period_end : p.period_start }}</td><td>{{ i < points.length ? historyLabel || '历史' : '预测' }}</td><td>{{ p.value ?? '缺失' }}</td><td>{{ unit || p.unit }}</td><td>{{ p.lower_bound != null && p.upper_bound != null ? `${p.lower_bound} – ${p.upper_bound}` : '—' }}</td></tr></tbody></table></div></details>
</template>
