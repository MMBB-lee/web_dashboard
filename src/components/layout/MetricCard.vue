<script setup lang="ts">
import { computed } from 'vue'
import { formatPeriod, formatValue } from '../../app/format'
import type { OverviewCard } from '../../app/types'

const props = defineProps<{ card: OverviewCard; demo: boolean; index: number }>()
const label = computed(() => props.card.label ?? ({ scenic_visits: '景区接待人次', rail_departures: '铁路发送人次', airport_throughput: '机场旅客吞吐量' } as Record<string, string>)[props.card.metric] ?? props.card.metric)
</script>

<template>
  <article class="metric-card panel">
    <div class="metric-head"><span class="metric-index">0{{ index + 1 }}</span><span class="metric-label">{{ label }}</span></div>
    <div class="metric-amount"><strong>{{ formatValue(card.value) }}</strong><span>{{ card.value == null ? '该粒度暂无数据' : card.unit }}</span></div>
    <div class="metric-bottom">
      <span>{{ formatPeriod(card.period_start, card.period_end, card.grain) }}</span>
      <span>{{ demo ? '演示数据' : card.source_ids.length ? `来源 ${card.source_ids.length} 项` : '来源待标注' }}</span>
    </div>
  </article>
</template>
