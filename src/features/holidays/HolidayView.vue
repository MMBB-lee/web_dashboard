<script setup lang="ts">
import { computed } from 'vue'
import { backendMode } from '../../app/data'
import { formatValue } from '../../app/format'
import HolidayCompareChart from '../../components/charts/HolidayCompareChart.vue'
import HolidayDurationChart from '../../components/charts/HolidayDurationChart.vue'
import HolidayShareChart from '../../components/charts/HolidayShareChart.vue'
import ChartLoading from '../../components/charts/ChartLoading.vue'
import { useVisibleChartLoading } from '../../components/charts/useVisibleChartLoading'
import UiSelect from '../../components/ui/UiSelect.vue'
import { useHolidays } from './holidays'

const { kind, selectedIds, entityId, mode, holidays, entities, metric, comparison, dailyAverageComparison, loading, error, selectedHolidays, loadComparison } = useHolidays()
const chartLoading = useVisibleChartLoading(loading)
const comparisonLoading = computed(() => selectedHolidays.value.length >= 2 && chartLoading.value)
const isDemo = computed(() => backendMode.value === 'demo')
const modeLabel = computed(() => ({ daily_average: '日均接待人次', period_total: '假期总接待人次', day_index: '逐日变化' })[mode.value])
const kindOptions = [{ value: 'may_day', label: '五一' }, { value: 'national_day', label: '国庆' }, { value: 'summer', label: '暑假' }, { value: 'winter', label: '寒假' }]
const availableHolidays = computed(() => holidays.value.filter(item => item.kind === kind.value && !selectedIds.value.includes(item.id)))
const holidayOptions = computed(() => availableHolidays.value.map(item => ({ value: item.id, label: item.name })))
const canAdd = computed(() => selectedIds.value.length < 6 && availableHolidays.value.length > 0)
const entityOptions = computed(() => entities.value.map(item => ({ value: item.id, label: item.name })))
const modeOptions = [{ value: 'daily_average', label: '日均值' }, { value: 'period_total', label: '假期总量' }, { value: 'day_index', label: '逐日序列' }]
function setKind(value: string) {
  if (value === 'may_day' || value === 'national_day' || value === 'summer' || value === 'winter') kind.value = value
}
function setMode(value: string) {
  if (value === 'daily_average' || value === 'period_total' || value === 'day_index') mode.value = value
}
const selectedItems = computed(() => selectedHolidays.value.map(holiday => ({
  holiday,
  result: dailyAverageComparison.value?.items.find(item => item.holiday_id === holiday.id),
})))
const selectionHint = computed(() => selectedHolidays.value.length === 0
  ? '请先添加假期；选择至少两个假期后，这里会显示客流对比。'
  : selectedHolidays.value.length === 1 ? '再添加一个假期后，这里会显示客流对比。' : '')
const shareValues = computed(() => {
  const values = comparison.value?.items.map(item => item.value)
  if (!comparison.value?.comparable || !values?.length || values.some(value => value == null || value < 0)) return null
  const numeric = values as number[]
  return numeric.some(value => value > 0) ? numeric : null
})
function addHoliday(id: string) {
  if (selectedIds.value.length < 6 && holidays.value.some(item => item.id === id) && !selectedIds.value.includes(id)) selectedIds.value = [...selectedIds.value, id]
}
function removeHoliday(id: string) {
  selectedIds.value = selectedIds.value.filter(item => item !== id)
}
</script>

<template>
  <div class="dashboard-page">
  <section class="page-heading">
    <p>按同口径比较假期总量或日均值，不把不同长度的假期直接计算成同比。</p>
  </section>

  <section class="panel section-panel filter-panel">
    <div class="holiday-filters">
      <div class="field"><span>假期类型</span><UiSelect :model-value="kind" :options="kindOptions" label="假期类型" @update:model-value="setKind" /></div>
      <div class="field"><span>景区</span><UiSelect v-model="entityId" :options="entityOptions" label="景区" searchable /></div>
      <div class="field"><span>比较方式</span><UiSelect :model-value="mode" :options="modeOptions" label="比较方式" @update:model-value="setMode" /></div>
      <div class="field add-field"><span>添加假期（{{ selectedHolidays.length }}/6）</span><UiSelect v-if="canAdd" model-value="" :options="holidayOptions" label="添加假期" @update:model-value="addHoliday" /><span v-else class="add-unavailable">{{ selectedIds.length >= 6 ? '最多选择 6 个' : '当前类型已全部加入' }}</span></div>
      <button class="button-primary" :disabled="loading" @click="loadComparison">{{ loading ? '计算中…' : '更新对比' }}</button>
    </div>
  </section>

  <div v-if="selectedHolidays.length" class="selected-holidays" role="list" aria-label="已选假期" :style="{ '--holiday-columns': selectedHolidays.length }">
    <article v-for="{ holiday, result } in selectedItems" :key="holiday.id" class="panel selected-holiday" role="listitem" :title="`${holiday.name}：${holiday.start} 至 ${holiday.end}，${holiday.days_count} 天`">
      <div class="holiday-icon" aria-hidden="true"><svg viewBox="0 0 48 48"><path d="M4 37 17 14l8 13 6-9 13 19H4Z"/><path d="m12 29 5-8 5 8m5 1 4-6 4 6"/></svg></div>
      <div class="holiday-details"><strong>{{ holiday.name }}</strong><span>{{ holiday.start.slice(5).replace('-', '.') }}—{{ holiday.end.slice(5).replace('-', '.') }} · {{ holiday.days_count }}天</span></div>
      <div class="holiday-value" title="日均接待人次"><span>日均</span><strong>{{ result?.value == null ? '—' : formatValue(result.value) }}</strong><small>人次/日</small></div>
      <button type="button" class="remove-holiday" :aria-label="`移除${holiday.name}`" :title="`移除${holiday.name}`" @click="removeHoliday(holiday.id)">×</button>
    </article>
  </div>

  <div v-if="error" class="state-box error holiday-state">{{ error }}</div>
  <div v-else-if="!chartLoading && !holidays.length" class="state-box holiday-state">当前没有可用的假期定义。</div>
  <template v-else>
    <div class="holiday-screen dashboard-body" :class="{ 'has-selected-holidays': selectedHolidays.length > 0 }">
      <div class="holiday-visuals">
        <section class="panel section-panel compare-panel">
          <div class="panel-head"><h2 class="panel-title">{{ modeLabel }}对比</h2><span class="panel-kicker">{{ comparison?.items[0]?.unit ?? '人次' }}</span></div>
          <ChartLoading v-if="comparisonLoading" kind="bar" label="正在加载假期客流对比…" />
          <HolidayCompareChart v-else-if="selectedHolidays.length >= 2 && comparison?.comparable && comparison.items.length" :items="comparison.items" />
          <div v-else class="state-box empty-chart-state">{{ selectionHint || comparison?.reason || '所选假期没有可比较的数据。' }}</div>
          <div v-if="!comparisonLoading && selectedHolidays.length >= 2" class="chart-caption">{{ metric?.definition ?? '景区接待人次。' }} {{ isDemo ? '静态假期样例，非官方统计。' : `来源：${comparison?.items.flatMap(item => item.source_ids).join('、') || '待标注'}。` }}</div>
        </section>

        <section class="panel section-panel visual-panel donut-panel">
          <div class="panel-head"><h2 class="panel-title">所选假期数值占比</h2><span class="panel-kicker">{{ modeLabel }}</span></div>
          <ChartLoading v-if="comparisonLoading" kind="donut" label="正在加载假期占比…" />
          <HolidayShareChart v-else-if="selectedHolidays.length >= 2 && shareValues && comparison" :labels="comparison.items.map(item => item.label)" :values="shareValues" :unit="comparison.items[0]?.unit ?? '人次'" title="所选假期数值占比" />
          <div v-else class="state-box empty-chart-state">{{ selectionHint || comparison?.reason || '当前条件下没有可计算占比的数值。' }}</div>
        </section>

        <section class="panel section-panel visual-panel duration-panel">
          <div class="panel-head"><h2 class="panel-title">假期天数对比</h2><span class="panel-kicker">假期日历 / 天</span></div>
          <ChartLoading v-if="comparisonLoading" kind="bar" label="正在加载假期天数…" />
          <HolidayDurationChart v-else-if="selectedHolidays.length >= 2" :holidays="selectedHolidays" />
          <div v-else class="state-box empty-chart-state">{{ selectionHint }}</div>
        </section>

        <section class="panel section-panel visual-panel daily-panel">
          <div class="panel-head"><h2 class="panel-title">日均客流量变化</h2><span class="panel-kicker">按所选假期时间顺序 · 人次/日</span></div>
          <ChartLoading v-if="comparisonLoading" kind="line" label="正在加载日均客流量变化…" />
          <HolidayCompareChart v-else-if="selectedHolidays.length >= 2 && dailyAverageComparison?.comparable && dailyAverageComparison.items.length" :items="dailyAverageComparison.items" variant="timeline" />
          <div v-else class="state-box empty-chart-state">{{ selectionHint || dailyAverageComparison?.reason || '所选假期没有可用的日均客流数据。' }}</div>
          <div v-if="!comparisonLoading && dailyAverageComparison?.comparable" class="chart-caption">仅显示当前选中的 {{ selectedHolidays.length }} 个假期；按假期开始日期从早到晚排列。</div>
        </section>

        <section class="panel section-panel source-panel">
          <div class="panel-head"><h2 class="panel-title">口径与来源</h2></div>
          <dl class="source-list">
            <div><dt>指标</dt><dd>{{ metric?.display_name ?? '景区接待人次' }}</dd></div>
            <div><dt>统计方式</dt><dd>{{ modeLabel }}</dd></div>
            <div><dt>已选假期</dt><dd>{{ selectedHolidays.length }} 个（最多 6 个）</dd></div>
            <div><dt>数据来源</dt><dd>{{ isDemo ? '静态演示数据，非官方统计' : comparison?.items.flatMap(item => item.source_ids).join('、') || '待标注' }}</dd></div>
          </dl>
        </section>
      </div>

    </div>
  </template>
  </div>
</template>

<style scoped>
.filter-panel { margin-bottom: 11px; }
.holiday-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; }
.holiday-filters .field { flex: 0 1 215px; --select-min-width: 0; }
.holiday-filters .field :deep(.ui-select) { width: 100%; }
.add-unavailable { display: flex; align-items: center; height: var(--select-height, 31px); padding: 0 10px; border: 1px solid var(--border-glow); background: #10274b; color: var(--text-sub); font-size: 12px; }
.selected-holidays { flex: none; display: grid; grid-template-columns: repeat(var(--holiday-columns), minmax(0, 1fr)); gap: 11px; margin-bottom: 11px; }
.selected-holiday { display: flex; align-items: center; gap: 6px; min-width: 0; min-height: 70px; padding: 7px 19px 7px 9px; }
.holiday-icon { width: 38px; height: 38px; flex: none; display: grid; place-items: center; border: 1px solid rgba(0, 212, 255, .6); border-radius: 50%; color: var(--accent); background: radial-gradient(circle, rgba(0, 212, 255, .18), rgba(0, 212, 255, .02) 72%); }
.holiday-icon svg { width: 25px; height: 25px; fill: none; stroke: currentColor; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
.selected-holiday:nth-child(even) .holiday-icon, .selected-holiday:nth-child(even) .holiday-value strong { color: var(--accent-green); }
.holiday-details { display: grid; gap: 3px; min-width: 0; flex: 1; }
.holiday-details strong { overflow: hidden; color: var(--text-title); font-size: 13px; white-space: nowrap; text-overflow: ellipsis; }
.holiday-details span { overflow: hidden; color: var(--text-sub); font-size: 10px; white-space: nowrap; text-overflow: ellipsis; }
.holiday-value { display: grid; gap: 2px; min-width: 75px; padding-left: 6px; border-left: 1px solid var(--border-glow); }
.holiday-value span { color: var(--text-sub); font-size: 9px; }
.holiday-value strong { color: var(--accent); font-size: 19px; line-height: 1; font-variant-numeric: tabular-nums; white-space: nowrap; }
.holiday-value small { color: var(--text-sub); font-size: 9px; }
.remove-holiday { position: absolute; top: 2px; right: 2px; width: 17px; height: 17px; padding: 0; border: 0; background: transparent; color: var(--text-sub); font-size: 16px; line-height: 16px; cursor: pointer; }
.remove-holiday:hover { color: var(--accent); }
.holiday-state { margin: 14px 0; }
.holiday-screen { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.holiday-visuals { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr) clamp(187px, calc(26vh - 18px), 252px); gap: 11px; }
.has-selected-holidays .holiday-visuals { grid-template-rows: minmax(0, 1fr) clamp(209px, calc(26vh + 4px), 274px); }
.holiday-visuals > .panel { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.compare-panel { grid-column: span 6; }.donut-panel, .duration-panel { grid-column: span 3; }
.daily-panel { grid-column: span 8; }.source-panel { grid-column: span 4; }
.holiday-visuals :deep(.chart-canvas) { flex: 1; min-height: 0; height: auto; }
.daily-panel { padding-top: 8px; padding-bottom: 8px; }
.daily-panel .panel-head { padding-bottom: 5px; }
.daily-panel .chart-caption { padding-top: 4px; }
.empty-chart-state { flex: 1; display: grid; place-items: center; min-height: 0; padding: 12px; text-align: center; }
.holiday-visuals .chart-caption { margin-top: auto; }
.source-list { display: grid; gap: 0; margin: 0; font-size: 12px; }
.source-list > div { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--border-soft); }
.source-list dt { color: var(--text-sub); }.source-list dd { min-width: 0; margin: 0; color: var(--text-main); overflow-wrap: anywhere; }
@media (max-width: 1500px) {
  .selected-holidays { gap: 7px; }
  .selected-holiday { gap: 4px; padding-left: 6px; padding-right: 16px; }
  .holiday-icon { width: 30px; height: 30px; }
  .holiday-icon svg { width: 21px; height: 21px; }
  .holiday-value { min-width: 65px; padding-left: 4px; }
  .holiday-value strong { font-size: 15px; }
  .holiday-details strong { font-size: 11px; }
}
@media (min-width: 1101px) and (max-width: 1250px) {
  .holiday-icon { display: none; }
  .holiday-details span { display: none; }
}
@media (max-width: 1100px) {
  .selected-holidays { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .holiday-screen { flex: none; }
  .holiday-visuals { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }
  .has-selected-holidays .holiday-visuals { grid-template-rows: auto; }
  .holiday-visuals > .panel { min-height: 280px; }
  .compare-panel { grid-column: 1 / -1; }
  .donut-panel, .duration-panel, .daily-panel, .source-panel { grid-column: auto; }
  .daily-panel { grid-column: 1 / -1; }
  .holiday-visuals :deep(.chart-canvas) { flex: none; height: 260px; }
}
@media (max-width: 620px) {
  .holiday-filters .field { flex-basis: calc(50% - 6px); }
  .selected-holidays, .holiday-visuals { grid-template-columns: 1fr; }
  .holiday-visuals > .panel { grid-column: 1; }
}
@media (min-width: 1101px) and (max-height: 900px) {
  .filter-panel { margin-bottom: 8px; }
  .holiday-filters, .holiday-visuals { gap: 8px; }
  .selected-holidays { gap: 8px; margin-bottom: 8px; }
  .selected-holiday { min-height: 64px; padding-top: 5px; padding-bottom: 5px; }
  .holiday-icon { width: 34px; height: 34px; }
  .holiday-icon svg { width: 22px; height: 22px; }
  .holiday-value strong { font-size: 17px; }
  .holiday-visuals { grid-template-rows: minmax(0, 1fr) 147px; }
  .has-selected-holidays .holiday-visuals { grid-template-rows: minmax(0, 1fr) 163px; }
  .source-list { font-size: 11px; }.source-list > div { padding: 6px 0; }
}
</style>
