<script setup lang="ts">
import { computed } from 'vue'
import { backendMode } from '../../app/data'
import { formatPeriod, formatRate, formatValue } from '../../app/format'
import TrendChart from '../../components/charts/TrendChart.vue'
import ForecastChart from '../../components/charts/ForecastChart.vue'
import { useScenic } from './scenic'

const { entityId, grain, start, end, horizon, entities, selectedEntity, metric, source, series, forecast, loading, error, reload } = useScenic()
const last = computed(() => series.value?.points.at(-1))
const isDemo = computed(() => backendMode.value === 'demo')
</script>

<template>
  <div class="dashboard-page">
  <section class="page-heading scenic-heading">
    <div><span class="eyebrow">SCENIC ANALYSIS / 景区</span><h1>景区客流观察</h1><p>查看接待人次的历史变化与经过回测的周／月预测。</p></div>
    <div class="toolbar">
      <label class="field">选择景区<select v-model="entityId"><option v-for="entity in entities" :key="entity.id" :value="entity.id">{{ entity.name }}</option></select></label>
      <label class="field">时间粒度<select v-model="grain"><option value="week">按周</option><option value="month">按月</option></select></label>
      <label class="field">开始日期<input v-model="start" type="date" /></label>
      <label class="field">结束日期<input v-model="end" type="date" /></label>
      <button class="button-subtle" :disabled="loading" @click="reload">{{ loading ? '加载中…' : '查询' }}</button>
    </div>
  </section>

  <div v-if="error" class="state-box error">{{ error }}</div>
  <div v-else-if="loading && !series" class="state-box">正在加载景区数据…</div>
  <template v-else>
    <div class="scenic-summary">
      <div class="panel summary-tile"><span>观察对象</span><strong>{{ selectedEntity?.name ?? '未选择' }}</strong><small>指标：{{ metric?.display_name ?? '景区接待人次' }}</small></div>
      <div class="panel summary-tile"><span>最新周期接待人次</span><strong class="accent-number">{{ formatValue(last?.value) }} <em>{{ last?.unit ?? '人次' }}</em></strong><small>{{ last ? formatPeriod(last.period_start, last.period_end, last.grain) : '暂无数据' }}</small></div>
      <div class="panel summary-tile"><span>数据覆盖率</span><strong>{{ formatRate(series ? series.coverage_ratio * 100 : null) }}</strong><small>缺失周期 {{ series?.missing_periods.length ?? 0 }} 个</small></div>
      <div class="panel summary-tile"><span>预测回测误差</span><strong>{{ forecast?.backtest ? formatRate(forecast.backtest.smape) : '—' }}</strong><small>sMAPE · {{ forecast?.model_version ?? '暂无模型' }}</small></div>
    </div>

    <div class="scenic-dashboard dashboard-body">
      <section class="panel section-panel">
        <div class="panel-head"><h2 class="panel-title">历史接待趋势</h2><span class="panel-kicker">ACTUAL / {{ grain === 'week' ? '周' : '月' }}</span></div>
        <TrendChart v-if="series?.points.length" :points="series.points" name="实际接待人次" unit="人次" />
        <div v-else class="state-box">选定区间没有可展示的数据。</div>
        <div class="chart-caption">实线表示已核验实际值；缺失周期不补造。{{ series?.missing_periods.length ? `缺失 ${series.missing_periods.length} 个周期。` : '' }}</div>
      </section>
      <section class="panel section-panel">
        <div class="panel-head"><h2 class="panel-title">历史预测与区间</h2><label class="field horizon-field">预测步数<select v-model.number="horizon"><option v-for="n in grain === 'week' ? 8 : 6" :key="n" :value="n">{{ n }} {{ grain === 'week' ? '周' : '月' }}</option></select></label></div>
        <ForecastChart v-if="forecast?.status === 'ready' && forecast.forecast_points.length" :actual="series?.points ?? []" :forecast="forecast.forecast_points" unit="人次" />
        <div v-else class="state-box">{{ forecast?.reason ?? '数据不足，暂不输出预测曲线。' }}</div>
        <div class="chart-caption">预测值用虚线、上下界用点线表示。回测 MAE {{ formatValue(forecast?.backtest?.mae) }} 人次；样本数 {{ forecast?.backtest?.sample_count ?? '—' }}。</div>
      </section>
    <section class="panel section-panel source-panel">
      <div class="panel-head"><h2 class="panel-title">指标口径与来源</h2><span class="panel-kicker">DATA / PROVENANCE</span></div>
      <div class="source-grid">
        <div><span>指标定义</span><p>{{ metric?.definition ?? '景区在统计周期内的接待人次。' }}</p></div>
        <div><span>统计时间</span><p>{{ last ? formatPeriod(last.period_start, last.period_end, last.grain) : '—' }}</p></div>
        <div><span>来源</span><p v-if="isDemo" class="text-warn">静态样例，非官方统计</p><p v-else-if="source"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.publisher }} · {{ source.title }} ↗</a></p><p v-else>{{ last?.source_ids.join('、') || '尚未提供' }}</p></div>
        <div><span>最近更新时间</span><p>{{ isDemo ? '暂无接口更新' : last?.ingested_at ?? '尚未提供' }}</p></div>
      </div>
      <p class="source-footnote">{{ metric?.comparability_note ?? '不同来源与指标须先核对统计口径。' }}</p>
    </section>
    </div>
    <div v-if="loading" class="load-hint">正在更新数据…</div>
  </template>
  </div>
</template>

<style scoped>
.scenic-heading .toolbar { max-width: 760px; justify-content: flex-end; }
.scenic-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 11px; margin-bottom: 11px; }
.summary-tile { display: grid; align-content: start; gap: 7px; min-height: 102px; padding: 12px 14px; }
.summary-tile > span { color: var(--text-sub); font-size: 13px; }.summary-tile strong { color: var(--text-title); font-size: 21px; font-weight: 650; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.summary-tile strong.accent-number { color: var(--accent); font-size: 25px; }.summary-tile em { color: var(--text-sub); font-size: 13px; font-style: normal; font-weight: 400; }
.summary-tile small { color: var(--text-sub); font-size: 12px; }
.scenic-dashboard { display: grid; grid-template-columns: minmax(0, 1.13fr) minmax(240px, .75fr) minmax(0, 1.13fr); grid-template-rows: minmax(0, 1fr); gap: 11px; }
.scenic-dashboard > .panel { min-height: 0; display: flex; flex-direction: column; }
.scenic-dashboard > .panel .chart-caption { margin-top: auto; }
.scenic-dashboard > section:nth-child(1) { grid-column: 1; grid-row: 1; }
.scenic-dashboard > section:nth-child(2) { grid-column: 3; grid-row: 1; }
.scenic-dashboard > .source-panel { grid-column: 2; grid-row: 1; }
.scenic-dashboard :deep(.chart-canvas) { flex: 1; min-height: 0; height: auto; }
.horizon-field { display: flex; align-items: center; gap: 7px; white-space: nowrap; }.horizon-field select { min-width: 74px; height: 29px; font-size: 12px; }
.source-grid { display: grid; grid-template-columns: 1fr; gap: 15px; }.source-grid > div { padding-bottom: 12px; border-bottom: 1px solid var(--border-soft); }.source-grid span { color: var(--text-sub); font-size: 12px; }.source-grid p { margin: 7px 0 0; color: var(--text-main); font-size: 13px; line-height: 1.5; word-break: break-word; }.source-grid a { color: var(--accent); }
.source-footnote { margin: 19px 0 0; padding-top: 10px; border-top: 1px solid var(--border-soft); color: var(--text-sub); font-size: 12px; }.load-hint { padding-top: 10px; color: var(--text-sub); font-size: 12px; }
@media (max-width: 1180px) { .scenic-summary { grid-template-columns: repeat(2, 1fr); }.scenic-dashboard { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }.scenic-dashboard > section:nth-child(2) { grid-column: 2; }.scenic-dashboard > .source-panel { grid-column: 1 / -1; grid-row: 2; }.scenic-dashboard :deep(.chart-canvas) { flex: none; height: 320px; }.source-grid { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 820px) { .scenic-dashboard { grid-template-columns: 1fr; }.scenic-dashboard > section:nth-child(1), .scenic-dashboard > section:nth-child(2), .scenic-dashboard > .source-panel { grid-column: 1; grid-row: auto; }.source-grid { grid-template-columns: repeat(2, 1fr); }.scenic-heading .toolbar { justify-content: flex-start; } }
@media (max-width: 560px) { .scenic-summary, .source-grid { grid-template-columns: 1fr; } }
@media (min-width: 1101px) and (max-height: 850px) {
  .scenic-summary { gap: 8px; margin-bottom: 8px; }
  .summary-tile { min-height: 75px; padding: 8px 11px; gap: 3px; }
  .summary-tile strong { font-size: 17px; }
  .summary-tile strong.accent-number { font-size: 21px; }
  .scenic-dashboard { gap: 8px; }
  .source-grid { gap: 7px; }
  .source-grid > div { padding-bottom: 6px; }
  .source-grid p { margin-top: 3px; font-size: 12px; line-height: 1.3; }
  .source-footnote { margin-top: 8px; padding-top: 6px; line-height: 1.3; }
}
</style>
