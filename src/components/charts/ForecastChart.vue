<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import { formatAxisValue, formatValue, pointLabel } from '../../app/format'
import type { NumericPoint } from '../../app/types'
import { useChart } from './useChart'

const props = defineProps<{ actual: NumericPoint[]; forecast: NumericPoint[]; unit?: string }>()
const option = computed<EChartsOption>(() => {
  const actual = props.actual.slice(-7)
  const forecast = props.forecast
  const labels = [...actual, ...forecast].map(pointLabel)
  const start = Math.max(0, actual.length - 1)
  const prediction = [...Array(start).fill(null), actual.at(-1)?.value ?? null, ...forecast.map((point) => point.value)]
  const bounds = (key: 'lower_bound' | 'upper_bound') => [
    ...Array(actual.length).fill(null), ...forecast.map((point) => point[key] ?? null),
  ]
  return {
    animationDuration: 1000,
    color: ['#00d4ff', '#ffb84d', '#688cae'],
    grid: { top: 35, right: 36, bottom: 27, left: 14, containLabel: true },
    legend: { top: 0, right: 0, textStyle: { color: '#a2bfda', fontSize: 12 }, itemWidth: 16, itemHeight: 6, data: ['历史实际', '预测值', '预测区间'] },
    tooltip: {
      trigger: 'axis', backgroundColor: 'rgba(7, 23, 49, .96)', borderColor: 'rgba(0, 212, 255, .45)', borderRadius: 0,
      textStyle: { color: '#e6f7ff', fontSize: 13 }, valueFormatter: (value) => `${formatValue(Number(value))} ${props.unit ?? '人次'}`,
    },
    xAxis: { type: 'category', boundaryGap: false, data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: 'rgba(117, 167, 210, .3)' } }, axisLabel: { color: '#a2bfda', fontSize: 12, hideOverlap: true } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#a2bfda', fontSize: 12, formatter: formatAxisValue }, splitLine: { lineStyle: { color: 'rgba(117, 167, 210, .11)' } } },
    series: [
      { name: '历史实际', type: 'line', smooth: .26, symbol: 'circle', symbolSize: 5, showSymbol: false, lineStyle: { width: 2.5, color: '#00d4ff' }, itemStyle: { color: '#00d4ff' }, data: [...actual.map((point) => point.value), ...Array(forecast.length).fill(null)] },
      { name: '预测值', type: 'line', smooth: .26, symbol: 'emptyCircle', symbolSize: 6, lineStyle: { width: 2.5, type: 'dashed', color: '#ffb84d' }, itemStyle: { color: '#ffb84d' }, data: prediction },
      { name: '预测区间', type: 'line', symbol: 'none', lineStyle: { width: 1, type: 'dotted', color: '#688cae' }, data: bounds('lower_bound') },
      { name: '区间上界', type: 'line', symbol: 'none', lineStyle: { width: 1, type: 'dotted', color: '#688cae' }, data: bounds('upper_bound') },
    ],
  }
})
const chartEl = useChart(option)
</script>

<template><div ref="chartEl" class="chart-canvas" role="img" aria-label="历史实际值、预测值与预测区间图" /></template>
