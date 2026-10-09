<script setup lang="ts">
import { computed } from 'vue'
import { graphic } from 'echarts/core'
import type { EChartsOption } from 'echarts'
import { formatValue, pointLabel } from '../../app/format'
import type { NumericPoint } from '../../app/types'
import { useChart } from './useChart'

const props = defineProps<{ points: NumericPoint[]; name?: string; unit?: string }>()
const option = computed<EChartsOption>(() => ({
  animationDuration: 600,
  grid: { top: 20, right: 32, bottom: 26, left: 14, containLabel: true },
  tooltip: {
    trigger: 'axis', backgroundColor: 'rgba(7, 23, 49, .96)', borderColor: 'rgba(0, 212, 255, .45)', borderRadius: 0,
    textStyle: { color: '#e6f7ff', fontSize: 13 }, valueFormatter: (value) => `${formatValue(Number(value))} ${props.unit ?? '人次'}`,
  },
  xAxis: {
    type: 'category', boundaryGap: false, data: props.points.map(pointLabel),
    axisTick: { show: false }, axisLine: { lineStyle: { color: 'rgba(117, 167, 210, .3)' } },
    axisLabel: { color: '#a2bfda', fontSize: 12, hideOverlap: true },
  },
  yAxis: {
    type: 'value', scale: true,
    axisLabel: { color: '#a2bfda', fontSize: 12, formatter: (value: number) => value >= 10000 ? `${Math.round(value / 10000)}万` : String(value) },
    splitLine: { lineStyle: { color: 'rgba(117, 167, 210, .11)' } },
  },
  series: [{
    name: props.name ?? '实际值', type: 'line', smooth: .32, symbol: 'circle', symbolSize: 5, showSymbol: false,
    lineStyle: { width: 2.5, color: '#00d4ff', shadowBlur: 9, shadowColor: 'rgba(0, 212, 255, .36)' },
    itemStyle: { color: '#00d4ff' },
    areaStyle: { color: new graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: 'rgba(0, 212, 255, .32)' }, { offset: 1, color: 'rgba(0, 212, 255, .015)' },
    ]) },
    data: props.points.map((point) => point.value),
  }],
}))
const chartEl = useChart(option)
</script>

<template><div ref="chartEl" class="chart-canvas" role="img" :aria-label="`${name ?? '历史客流'}趋势图`" /></template>
