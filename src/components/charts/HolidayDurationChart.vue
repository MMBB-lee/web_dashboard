<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import type { Holiday } from '../../app/types'
import { useChart } from './useChart'

const props = defineProps<{ holidays: Holiday[] }>()
const colors = ['#00d4ff', '#00d6a4', '#a58aff', '#ffbb52', '#ff759f', '#589fff']

const option = computed<EChartsOption>(() => ({
  animationDuration: 1000,
  color: ['#00d4ff', '#00d6a4'],
  grid: { top: 18, right: 45, bottom: 20, left: 8, containLabel: true },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    valueFormatter: value => `${value} 天`,
    backgroundColor: 'rgba(7, 23, 49, .96)',
    borderColor: 'rgba(0, 212, 255, .45)',
    borderRadius: 0,
    textStyle: { color: '#e6f7ff', fontSize: 12 },
  },
  xAxis: { type: 'value', min: 0, minInterval: 1, axisLabel: { color: '#a2bfda', fontSize: 10, formatter: '{value}天' }, splitLine: { lineStyle: { color: 'rgba(117, 167, 210, .11)' } } },
  yAxis: { type: 'category', inverse: true, data: props.holidays.map(item => item.name), axisLabel: { color: '#a2bfda', fontSize: 11, interval: 0 }, axisTick: { show: false }, axisLine: { lineStyle: { color: 'rgba(117, 167, 210, .25)' } } },
  series: [{
    type: 'bar',
    barMaxWidth: 25,
    data: props.holidays.map((item, index) => ({ value: item.days_count, itemStyle: { color: colors[index % colors.length] } })),
    label: { show: true, position: 'right', color: '#c0d8f0', fontSize: 11, formatter: '{c} 天' },
  }],
}))
const chartEl = useChart(option)
</script>

<template><div ref="chartEl" class="chart-canvas" role="img" :aria-label="`假期天数：${holidays.map(item => `${item.name}${item.days_count}天`).join('，')}`" /></template>
