<script setup lang="ts">
import { computed } from 'vue'
import { formatPeriod, formatValue } from '../../app/format'
import type { OverviewCard } from '../../app/types'
import UiSelect from '../ui/UiSelect.vue'

type Option = { value: string; label: string }
const props = defineProps<{ card: OverviewCard; demo: boolean; index: number; selectedId: string; options: Option[] }>()
const emit = defineEmits<{ select: [id: string] }>()
const title = computed(() => ({ scenic_visits: '接待人次', rail_departures: '发送人次', airport_throughput: '旅客吞吐量' } as Record<string, string>)[props.card.metric] ?? props.card.metric)
const selectorLabel = computed(() => ({ scenic_visits: '景区', rail_departures: '火车站', airport_throughput: '机场' } as Record<string, string>)[props.card.metric] ?? '地点')
const selectorOptions = computed(() => props.options)
</script>

<template>
  <article class="metric-card panel">
    <div class="metric-icon" aria-hidden="true">
      <svg v-if="card.metric === 'scenic_visits'" viewBox="0 0 48 48"><path d="M4 37 17 14l8 13 6-9 13 19H4Z"/><path d="m12 29 5-8 5 8m5 1 4-6 4 6" class="icon-detail"/></svg>
      <svg v-else-if="card.metric === 'rail_departures'" viewBox="0 0 48 48"><rect x="11" y="6" width="26" height="31" rx="6"/><path d="M14 26h20M18 42l4-5m8 0 4 5M17 13h14"/><circle cx="18" cy="31" r="1.5"/><circle cx="30" cy="31" r="1.5"/></svg>
      <svg v-else-if="card.metric === 'airport_throughput'" class="airplane-icon" viewBox="0 0 48 48"><path d="M24 4c-2.2 0-3.6 2.2-3.6 5v11.1L6 27v5l14.4-3.3V38l-5.2 3v3l8.8-2.1 8.8 2.1v-3l-5.2-3v-9.3L42 32v-5l-14.4-6.9V9C27.6 6.2 26.2 4 24 4Z"/></svg>
      <svg v-else viewBox="0 0 48 48"><circle cx="24" cy="24" r="16"/><path d="M24 14v20m-10-10h20"/></svg>
    </div>
    <div class="metric-content">
      <div class="metric-head"><span class="metric-index">0{{ index + 1 }}</span><UiSelect class="metric-entity-select" :model-value="selectedId" :options="selectorOptions" :label="`选择${selectorLabel}`" searchable @update:model-value="emit('select', $event)" /><span class="metric-head-title">· {{ title }}</span></div>
      <div class="metric-amount"><strong>{{ formatValue(card.value) }}</strong><span>{{ card.value == null ? '该粒度暂无数据' : card.unit }}</span></div>
      <div class="metric-bottom">
        <span>{{ formatPeriod(card.period_start, card.period_end, card.grain) }}</span>
        <span>{{ card.metric === 'rail_departures' ? '发送人次口径 · ' : '' }}{{ demo ? '静态样例' : card.source_ids.length ? `来源 ${card.source_ids.length} 项` : '来源待标注' }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.metric-card { display: flex; align-items: center; gap: 14px; }
.metric-icon { width: 64px; height: 64px; flex: none; display: grid; place-items: center; border: 1px solid rgba(0, 212, 255, .42); border-radius: 50%; color: var(--accent); background: radial-gradient(circle, rgba(0, 212, 255, .13), rgba(0, 212, 255, .02) 70%); }
.metric-icon svg { width: 38px; height: 38px; fill: none; stroke: currentColor; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; filter: drop-shadow(0 0 5px rgba(0, 212, 255, .5)); }
.metric-icon .airplane-icon { fill: currentColor; stroke: none; }
.metric-icon .icon-detail { stroke-width: 1.6; }
.metric-content { min-width: 0; flex: 1; }
.metric-entity-select { flex: 0 1 153px; width: 153px; min-width: 0; --select-min-width: 0; }
.metric-entity-select :deep(.ui-select-trigger) { gap: 8px; font-size: 13px; }
.metric-entity-select :deep(.ui-select-trigger > span:first-child) { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.metric-entity-select :deep(.ui-select-menu) { width: 100%; min-width: 100%; max-width: 100%; font-size: 12px; }
.metric-head-title { min-width: 0; overflow: hidden; color: var(--text-main); font-size: 13px; white-space: nowrap; text-overflow: ellipsis; }
@media (max-width: 720px), (min-width: 1101px) and (max-height: 850px) {
  .metric-card { gap: 9px; }
  .metric-icon { width: 46px; height: 46px; }
  .metric-icon svg { width: 28px; height: 28px; }
  .metric-entity-select :deep(.ui-select-trigger) { font-size: 12px; }
}
</style>
