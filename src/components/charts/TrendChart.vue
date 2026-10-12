<script setup lang="ts">
import { computed } from 'vue'
import { graphic } from 'echarts/core'
import type { EChartsOption } from 'echarts'
import { formatAxisValue, formatValue, pointLabel } from '../../app/format'
import type { NumericPoint } from '../../app/types'
import { useChart } from './useChart'

const props = defineProps<{ points: NumericPoint[]; name?: string; unit?: string; variant?: 'visits' | 'period-change' }>()
const periodChanges = computed(() => props.points.map((point, index) => {
  const previous = props.points[index - 1]
  if (!previous || previous.value == null || previous.value === 0 || point.value == null) return null
  // 只比较相邻的完整周期，避免把缺失期误算为环比。
  if (Date.parse(point.period_start) - Date.parse(previous.period_end) !== 86_400_000) return null
  return Number((((point.value - previous.value) / previous.value) * 100).toFixed(1))
}))
const option = computed<EChartsOption>(() => props.variant === 'period-change' ? ({
  animationDuration: 800,
  grid: { top: 22, right: 16, bottom: 26, left: 14, containLabel: true },
  tooltip: {
    trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: 'rgba(7, 23, 49, .96)',
    borderColor: 'rgba(0, 212, 255, .45)', borderRadius: 0,
    textStyle: { color: '#e6f7ff', fontSize: 13 },
    valueFormatter: value => value == null ? '缺少相邻周期数据' : `${formatValue(Number(value), 1)}%`,
  },
  xAxis: {
    type: 'category', data: props.points.map(pointLabel),
    axisTick: { show: false }, axisLine: { lineStyle: { color: 'rgba(117, 167, 210, .3)' } },
    axisLabel: { color: '#a2bfda', fontSize: 11, hideOverlap: true },
  },
  yAxis: {
    type: 'value', axisLabel: { color: '#a2bfda', fontSize: 11, formatter: (value: number) => `${value}%` },
    splitLine: { lineStyle: { color: 'rgba(117, 167, 210, .11)' } },
  },
  series: [{
    name: '环比变化', type: 'bar', barMaxWidth: 25,
    data: periodChanges.value.map(value => ({
      value,
      itemStyle: { color: value != null && value < 0 ? '#ffb84d' : '#00d4ff' },
    })),
  }],
}) : ({
  animationDuration: 1000,
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
    axisLabel: { color: '#a2bfda', fontSize: 12, formatter: formatAxisValue },
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

<template><div ref="chartEl" class="chart-canvas" role="img" :aria-label="variant === 'period-change' ? '客流相邻周期环比变化图' : `${name ?? '历史客流'}趋势图`" /></template>
