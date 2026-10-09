<script setup lang="ts">
import { computed } from 'vue'
import { backendMode } from '../../app/data'
import { formatValue } from '../../app/format'
import HolidayCompareChart from '../../components/charts/HolidayCompareChart.vue'
import { useHolidays } from './holidays'

const { kind, firstId, secondId, entityId, mode, holidays, entities, metric, comparison, loading, error, firstHoliday, secondHoliday, entity, loadComparison } = useHolidays()
const isDemo = computed(() => backendMode.value === 'demo')
const modeLabel = computed(() => ({ daily_average: '日均接待人次', period_total: '假期总接待人次', day_index: '逐日变化' })[mode.value])
const difference = computed(() => {
  const [a, b] = comparison.value?.items ?? []
  if (!comparison.value?.comparable || a?.value == null || b?.value == null || a.value === 0) return null
  return ((b.value - a.value) / a.value) * 100
})
</script>

<template>
  <div class="dashboard-page">
  <section class="page-heading">
    <div><span class="eyebrow">HOLIDAY COMPARISON / 专题</span><h1>假期客流对比</h1><p>按同口径比较假期总量或日均值，不把不同长度的假期直接计算成同比。</p></div>
  </section>

  <section class="panel section-panel filter-panel">
    <div class="panel-head"><h2 class="panel-title">比较条件</h2><span class="panel-kicker">FILTER / 口径选择</span></div>
    <div class="holiday-filters">
      <label class="field">假期类型<select v-model="kind"><option value="may_day">五一</option><option value="national_day">国庆</option><option value="summer">暑假</option><option value="winter">寒假</option></select></label>
      <label class="field">比较假期 A<select v-model="firstId"><option v-for="holiday in holidays" :key="holiday.id" :value="holiday.id">{{ holiday.name }}</option></select></label>
      <label class="field">比较假期 B<select v-model="secondId"><option v-for="holiday in holidays" :key="holiday.id" :value="holiday.id">{{ holiday.name }}</option></select></label>
      <label class="field">景区<select v-model="entityId"><option v-for="item in entities" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label class="field">比较方式<select v-model="mode"><option value="daily_average">日均值</option><option value="period_total">假期总量</option><option value="day_index">逐日序列</option></select></label>
      <button class="button-primary" :disabled="loading" @click="loadComparison">{{ loading ? '计算中…' : '更新对比' }}</button>
    </div>
  </section>

  <div v-if="error" class="state-box error holiday-state">{{ error }}</div>
  <div v-else-if="loading && !comparison" class="state-box holiday-state">正在加载假期定义与对比数据…</div>
  <div v-else-if="!holidays.length" class="state-box holiday-state">当前假期类型尚无可用定义，请选择其他类型。</div>
  <template v-else>
    <div class="holiday-screen dashboard-body">
    <div class="holiday-info-grid">
      <article class="panel holiday-info"><span class="holiday-index">PERIOD A</span><strong>{{ firstHoliday?.name ?? '未选择' }}</strong><p>{{ firstHoliday?.start ?? '—' }} — {{ firstHoliday?.end ?? '—' }}</p><small>{{ firstHoliday?.days_count ?? '—' }} 天 · {{ isDemo ? '静态假期样例' : `定义来源 ${firstHoliday?.definition_source_id ?? '待标注'}` }}</small></article>
      <article class="panel holiday-info"><span class="holiday-index">PERIOD B</span><strong>{{ secondHoliday?.name ?? '未选择' }}</strong><p>{{ secondHoliday?.start ?? '—' }} — {{ secondHoliday?.end ?? '—' }}</p><small>{{ secondHoliday?.days_count ?? '—' }} 天 · {{ isDemo ? '静态假期样例' : `定义来源 ${secondHoliday?.definition_source_id ?? '待标注'}` }}</small></article>
      <article class="panel holiday-info change-card"><span class="holiday-index">COMPARABILITY</span><strong :class="comparison?.comparable ? 'text-green' : 'text-warn'">{{ comparison?.comparable ? '口径可比' : '暂不可比' }}</strong><p>{{ difference == null ? '—' : `${difference >= 0 ? '+' : ''}${difference.toFixed(1)}%` }}</p><small>{{ modeLabel }} · {{ entity?.name ?? '景区' }}</small></article>
    </div>

    <section class="panel section-panel compare-panel">
      <div class="panel-head"><h2 class="panel-title">{{ modeLabel }}对比</h2><span class="panel-kicker">{{ metric?.display_name ?? '景区接待人次' }}</span></div>
      <div v-if="comparison?.comparable && comparison.items.length" class="compare-layout">
        <HolidayCompareChart :items="comparison.items" />
        <div class="compare-values"><div v-for="(item, index) in comparison.items" :key="item.holiday_id" class="compare-value"><span>0{{ index + 1 }} / {{ item.label }}</span><strong>{{ formatValue(item.value) }} <small>{{ item.unit }}</small></strong><em>统计期 {{ item.period_start ?? '—' }} — {{ item.period_end ?? '—' }} · 缺失 {{ item.missing_days }} 天</em><em>{{ isDemo ? '静态样例，非官方统计' : `来源：${item.source_ids.join('、') || '待标注'} · 更新：${item.ingested_at || '待标注'}` }}</em></div></div>
      </div>
      <div v-else class="state-box">{{ comparison?.reason ?? '所选假期没有可比较的数据。' }}</div>
      <div class="chart-caption">{{ comparison?.reason ?? '展示值以接口返回的实际可用数据为准。' }} {{ isDemo ? '当前数值仅供界面展示。' : '' }}</div>
    </section>

    <section class="panel section-panel explain-panel"><h2 class="panel-title">口径说明</h2><p>{{ metric?.definition ?? '按指定景区与假期统计的接待人次。' }}</p><p>假期天数不同，优先比较日均值；缺少完整逐日数据时不生成逐日曲线。{{ metric?.comparability_note }}</p></section>
    </div>
    <div v-if="loading" class="load-hint">正在更新数据…</div>
  </template>
  </div>
</template>

<style scoped>
.filter-panel { margin-bottom: 11px; }.holiday-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; }.holiday-filters .field select { min-width: 137px; }
.holiday-state { margin: 14px 0; }
.holiday-screen { display: grid; grid-template-columns: minmax(215px, .8fr) minmax(420px, 1.8fr) minmax(215px, .8fr); grid-template-rows: auto minmax(0, 1fr); gap: 11px; }
.holiday-info-grid { display: contents; }
.holiday-info-grid > :nth-child(1) { grid-column: 1; grid-row: 1; }
.holiday-info-grid > :nth-child(2) { grid-column: 3; grid-row: 1; }
.holiday-info-grid > :nth-child(3) { grid-column: 1; grid-row: 2; }
.holiday-info { display: grid; align-content: start; gap: 10px; padding: 19px; }.holiday-index { color: var(--accent); font: 12px Consolas, monospace; letter-spacing: .12em; }.holiday-info strong { color: var(--text-title); font-size: 19px; }.holiday-info p { margin: 0; color: var(--text-main); font-size: 13px; }.holiday-info small { color: var(--text-sub); font-size: 12px; }.holiday-info.change-card strong { font-size: 19px; }.holiday-info.change-card p { color: var(--accent); font-size: 28px; font-weight: 700; }
.compare-panel { grid-column: 2; grid-row: 1 / 3; min-height: 0; display: flex; flex-direction: column; }
.compare-layout { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr; grid-template-rows: minmax(0, 1fr) auto; gap: 7px; }
.compare-layout :deep(.chart-canvas) { min-height: 0; height: 100%; }
.compare-panel .chart-caption { margin-top: auto; }
.compare-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.compare-value { display: grid; gap: 6px; padding: 9px 0; border-top: 1px solid var(--border-soft); }.compare-value > span { color: var(--text-sub); font-size: 12px; }.compare-value strong { color: var(--accent); font-size: 21px; font-weight: 650; }.compare-value small { color: var(--text-sub); font-size: 12px; font-weight: 400; }.compare-value em { color: var(--text-sub); font-size: 11px; font-style: normal; line-height: 1.3; }
.explain-panel { grid-column: 3; grid-row: 2; }.explain-panel p { margin: 13px 0 0; color: var(--text-sub); font-size: 13px; line-height: 1.55; }.load-hint { padding-top: 10px; color: var(--text-sub); font-size: 12px; }
@media (max-width: 1100px) { .holiday-screen { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }.holiday-info-grid { display: grid; grid-template-columns: repeat(3, 1fr); grid-column: 1 / -1; grid-row: 1; gap: 11px; }.holiday-info-grid > :nth-child(n) { grid-column: auto; grid-row: auto; }.compare-panel { grid-column: 1 / -1; grid-row: 2; }.compare-layout :deep(.chart-canvas) { height: 270px; }.explain-panel { grid-column: 1 / -1; grid-row: 3; } }
@media (max-width: 620px) { .holiday-info-grid { grid-template-columns: 1fr; }.holiday-filters .field { width: calc(50% - 5px); }.holiday-filters .field select { min-width: 0; width: 100%; }.compare-values { grid-template-columns: 1fr; } }
@media (min-width: 1101px) and (max-height: 850px) {
  .filter-panel { margin-bottom: 8px; }
  .holiday-filters { gap: 8px; }
  .holiday-screen { gap: 8px; }
  .holiday-info { padding: 11px; gap: 6px; }
  .holiday-info strong, .holiday-info.change-card strong { font-size: 17px; }
  .holiday-info.change-card p { font-size: 23px; }
  .compare-values { gap: 8px; }
  .compare-value { gap: 3px; padding: 5px 0; }
  .compare-value strong { font-size: 18px; }
  .explain-panel p { margin-top: 7px; font-size: 12px; line-height: 1.35; }
}
</style>
