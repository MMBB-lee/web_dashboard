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

const { kind, selectedIds, entityId, mode, holidays, entities, metric, comparison, loading, error, selectedHolidays, loadComparison } = useHolidays()
const chartLoading = useVisibleChartLoading(loading)
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
  result: comparison.value?.items.find(item => item.holiday_id === holiday.id),
})))
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
    <div><span class="eyebrow">HOLIDAY COMPARISON / 专题</span><h1>假期客流对比</h1><p>按同口径比较假期总量或日均值，不把不同长度的假期直接计算成同比。</p></div>
  </section>

  <section class="panel section-panel filter-panel">
    <div class="panel-head"><h2 class="panel-title">比较条件</h2><span class="panel-kicker">FILTER / 已选 {{ selectedHolidays.length }} 个假期</span></div>
    <div class="holiday-filters">
      <div class="field"><span>假期类型</span><UiSelect :model-value="kind" :options="kindOptions" label="假期类型" @update:model-value="setKind" /></div>
      <div class="field add-field"><span>添加假期</span><UiSelect v-if="canAdd" model-value="" :options="holidayOptions" label="添加假期" @update:model-value="addHoliday" /><span v-else class="add-unavailable">{{ selectedIds.length >= 6 ? '最多选择 6 个' : '当前类型已全部加入' }}</span></div>
      <div class="field"><span>景区</span><UiSelect v-model="entityId" :options="entityOptions" label="景区" searchable /></div>
      <div class="field"><span>比较方式</span><UiSelect :model-value="mode" :options="modeOptions" label="比较方式" @update:model-value="setMode" /></div>
      <button class="button-primary" :disabled="loading" @click="loadComparison">{{ loading ? '计算中…' : '更新对比' }}</button>
    </div>
    <div class="selected-holidays" role="list" aria-label="已选假期" :style="{ '--holiday-count': Math.max(selectedHolidays.length, 1) }">
      <div v-for="{ holiday, result } in selectedItems" :key="holiday.id" class="selected-holiday" role="listitem" :title="`${holiday.name}：${holiday.start} 至 ${holiday.end}，${holiday.days_count} 天`">
        <div class="selected-holiday-main"><strong>{{ holiday.name }}</strong><button type="button" class="remove-holiday" :aria-label="`移除${holiday.name}`" :title="`移除${holiday.name}`" @click="removeHoliday(holiday.id)">×</button></div>
        <span>{{ holiday.start }} — {{ holiday.end }} · {{ holiday.days_count }} 天</span>
        <em v-if="comparison?.comparable && result?.value != null">{{ formatValue(result.value) }} {{ result.unit }}</em>
      </div>
      <div v-if="!selectedHolidays.length" class="empty-selection" role="listitem">尚未选择假期，请从“添加假期”中选择。</div>
    </div>
  </section>

  <div v-if="error" class="state-box error holiday-state">{{ error }}</div>
  <div v-else-if="!chartLoading && !holidays.length" class="state-box holiday-state">当前没有可用的假期定义。</div>
  <template v-else>
    <div class="holiday-screen dashboard-body">
      <div class="holiday-visuals">
        <section class="panel section-panel compare-panel">
          <div class="panel-head"><h2 class="panel-title">{{ modeLabel }}对比</h2><span class="panel-kicker">{{ metric?.display_name ?? '景区接待人次' }} / 同口径</span></div>
          <ChartLoading v-if="chartLoading" kind="bar" label="正在加载假期客流对比…" />
          <HolidayCompareChart v-else-if="comparison?.comparable && comparison.items.length" :items="comparison.items" />
          <div v-else class="state-box empty-chart-state">{{ !selectedHolidays.length ? '请先添加假期；选择至少两个假期后，这里会显示客流对比。' : selectedHolidays.length === 1 ? '再添加一个假期后，这里会显示客流对比。' : comparison?.reason ?? '所选假期没有可比较的数据。' }}</div>
          <div v-if="!chartLoading && selectedHolidays.length >= 2" class="chart-caption">{{ metric?.definition ?? '景区接待人次。' }} {{ isDemo ? '静态假期样例，非官方统计。' : `来源：${comparison?.items.flatMap(item => item.source_ids).join('、') || '待标注'}。` }} {{ comparison?.reason ?? (isDemo ? '当前数值仅供界面展示。' : '展示值以接口返回数据为准。') }}</div>
        </section>

        <section class="panel section-panel visual-panel donut-panel">
          <div class="panel-head"><h2 class="panel-title">所选假期数值占比</h2><span class="panel-kicker">{{ modeLabel }} / 同口径</span></div>
          <ChartLoading v-if="chartLoading" kind="donut" label="正在加载假期占比…" />
          <HolidayShareChart v-else-if="shareValues && comparison" :labels="comparison.items.map(item => item.label)" :values="shareValues" :unit="comparison.items[0]?.unit ?? '人次'" title="所选假期数值占比" />
          <div v-else class="state-box empty-chart-state">{{ !selectedHolidays.length ? '选择至少两个假期后，这里会显示各假期数值占比。' : selectedHolidays.length === 1 ? '再添加一个假期后，这里会显示数值占比。' : comparison?.reason ?? '当前条件下没有可计算占比的数值。' }}</div>
        </section>

        <section class="panel section-panel visual-panel duration-panel">
          <div class="panel-head"><h2 class="panel-title">假期天数对比</h2><span class="panel-kicker">假期日历 / 天</span></div>
          <ChartLoading v-if="chartLoading" kind="bar" label="正在加载假期天数…" />
          <HolidayDurationChart v-else-if="selectedHolidays.length" :holidays="selectedHolidays" />
          <div v-else class="state-box empty-chart-state">添加假期后，这里会显示假期天数。</div>
          <div v-if="!chartLoading && selectedHolidays.length" class="chart-caption">起止日期来自假期定义。天数不同，优先比较日均值。</div>
        </section>
      </div>

    </div>
  </template>
  </div>
</template>

<style scoped>
.filter-panel { margin-bottom: 11px; }
.holiday-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; }
.holiday-filters .field { --select-min-width: 137px; }
.add-field { min-width: 180px; }
.add-unavailable { display: flex; align-items: center; height: var(--select-height, 31px); padding: 0 10px; border: 1px solid var(--border-glow); background: #10274b; color: var(--text-sub); font-size: 12px; }
.selected-holidays { display: grid; grid-template-columns: repeat(var(--holiday-count), minmax(0, 1fr)); min-height: 62px; gap: 8px; margin-top: 10px; }
.selected-holiday { box-sizing: border-box; min-width: 0; height: 62px; padding: 6px 9px; border: 1px solid var(--border-soft); background: rgba(0, 21, 46, .42); }
.empty-selection { display: flex; align-items: center; padding: 0 10px; border: 1px dashed var(--border-soft); color: var(--text-sub); font-size: 12px; }
.selected-holiday-main { display: flex; align-items: center; justify-content: space-between; gap: 5px; }
.selected-holiday strong { overflow: hidden; color: var(--text-title); font-size: 13px; white-space: nowrap; text-overflow: ellipsis; }
.selected-holiday > span, .selected-holiday em { display: block; overflow: hidden; color: var(--text-sub); font-size: 11px; font-style: normal; white-space: nowrap; text-overflow: ellipsis; }
.selected-holiday em { color: var(--accent); }
.remove-holiday { flex: none; width: 20px; height: 20px; padding: 0; border: 0; background: transparent; color: var(--text-sub); font-size: 19px; line-height: 18px; cursor: pointer; }
.remove-holiday:hover:not(:disabled) { color: var(--accent); }
.holiday-state { margin: 14px 0; }
.holiday-screen { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.holiday-visuals { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(230px, .8fr) minmax(230px, .8fr); grid-template-rows: minmax(0, 1fr); gap: 11px; }
.visual-panel, .compare-panel { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.visual-panel :deep(.chart-canvas), .compare-panel :deep(.chart-canvas) { flex: 1; min-height: 0; height: auto; }
.empty-chart-state { flex: 1; display: grid; place-items: center; min-height: 0; padding: 12px; text-align: center; }
.visual-panel .chart-caption, .compare-panel .chart-caption { margin-top: auto; }
@media (max-width: 1100px) {
  .selected-holidays { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .holiday-screen { flex: none; }
  .holiday-visuals { min-height: 600px; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(300px, 1fr) minmax(270px, .8fr); }
  .compare-panel { grid-column: 1 / -1; }
}
@media (max-width: 620px) {
  .holiday-filters .field { width: calc(50% - 5px); --select-min-width: 0; }
  .selected-holidays, .holiday-visuals { grid-template-columns: 1fr; }
  .holiday-visuals { min-height: 900px; grid-template-rows: minmax(360px, auto) repeat(2, minmax(250px, auto)); }
  .compare-panel { grid-column: auto; }
}
@media (min-width: 1101px) and (max-height: 850px) {
  .filter-panel { margin-bottom: 8px; }
  .holiday-filters, .holiday-visuals { gap: 8px; }
  .selected-holidays { margin-top: 7px; }
}
</style>
