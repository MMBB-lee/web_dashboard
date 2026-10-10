<script setup lang="ts">
import { computed } from 'vue'
import { graphic } from 'echarts/core'
import type { EChartsOption } from 'echarts'
import { formatValue } from '../../app/format'
import type { HolidayCompareItem } from '../../app/types'
import { useChart } from './useChart'

const props = defineProps<{ items: HolidayCompareItem[] }>()
const option = computed<EChartsOption>(() => ({
  animationDuration: 1000,
  grid: { top: 17, right: 72, bottom: 26, left: 20, containLabel: true },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: 'rgba(7, 23, 49, .96)', borderColor: 'rgba(0, 212, 255, .45)', borderRadius: 0, textStyle: { color: '#e6f7ff', fontSize: 13 }, valueFormatter: (value) => `${formatValue(Number(value))} ${props.items[0]?.unit ?? ''}` },
  xAxis: { type: 'value', axisLabel: { color: '#a2bfda', fontSize: 12, formatter: (value: number) => value >= 10000 ? `${Math.round(value / 10000)}万` : String(value) }, splitLine: { lineStyle: { color: 'rgba(117, 167, 210, .11)' } } },
  yAxis: { type: 'category', inverse: true, data: props.items.map((item) => item.label), axisLabel: { color: '#c0d8f0', fontSize: 13 }, axisTick: { show: false }, axisLine: { show: false } },
  series: [{
    type: 'bar', barWidth: 22, data: props.items.map((item) => item.value),
    label: { show: true, position: 'right', color: '#c0d8f0', fontSize: 13, formatter: (params) => formatValue(Number(params.value)) },
    itemStyle: { color: new graphic.LinearGradient(1, 0, 0, 0, [
      { offset: 0, color: '#00d4ff' }, { offset: 1, color: 'rgba(0, 212, 255, .19)' },
    ]) },
  }],
}))
const chartEl = useChart(option)
</script>

<template><div ref="chartEl" class="chart-canvas" role="img" aria-label="假期客流比较条形图" /></template>
