<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import { useChart } from './useChart'

const props = defineProps<{
  labels: string[]
  values: number[]
  title: string
  unit: string
}>()

const option = computed<EChartsOption>(() => ({
  animationDuration: 1000,
  color: ['#00d4ff', '#00d6a4', '#a58aff', '#ffbb52', '#ff759f', '#589fff'],
  tooltip: {
    trigger: 'item',
    formatter: `{b}<br/>{c} ${props.unit} · {d}%`,
    backgroundColor: 'rgba(7, 23, 49, .96)',
    borderColor: 'rgba(0, 212, 255, .45)',
    borderRadius: 0,
    textStyle: { color: '#e6f7ff', fontSize: 12 },
  },
  series: [{
    type: 'pie',
    radius: ['46%', '72%'],
    center: ['50%', '50%'],
    avoidLabelOverlap: true,
    label: { show: false },
    labelLine: { show: false },
    itemStyle: { borderColor: '#0c2548', borderWidth: 2 },
    emphasis: { scaleSize: 6 },
    data: props.labels.map((name, index) => ({ name, value: props.values[index] ?? 0 })),
  }],
}))
const chartEl = useChart(option)
</script>

<template><div ref="chartEl" class="chart-canvas" role="img" :aria-label="`${title}：${labels.map((name, index) => `${name}${Math.round((values[index] ?? 0) / values.reduce((sum, value) => sum + value, 0) * 1000) / 10}%`).join('，')}`" /></template>
