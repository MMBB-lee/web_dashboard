<script setup lang="ts">
import { computed } from 'vue'
import { backendMode } from '../../app/data'
import { formatPeriod, formatRate, formatValue } from '../../app/format'
import UiSelect from '../../components/ui/UiSelect.vue'
import WeekPicker from '../../components/ui/WeekPicker.vue'
import TrendChart from '../../components/charts/TrendChart.vue'
import ForecastChart from '../../components/charts/ForecastChart.vue'
import ChartLoading from '../../components/charts/ChartLoading.vue'
import { useVisibleChartLoading } from '../../components/charts/useVisibleChartLoading'
import { useScenic } from './scenic'

const { entityId, grain, setGrain, startPeriod, endPeriod, maxPeriod, refreshToday, horizon, entities, selectedEntity, metric, source, series, forecast, loading, error, reload } = useScenic()
const chartLoading = useVisibleChartLoading(loading)
const last = computed(() => series.value?.points.at(-1))
const isDemo = computed(() => backendMode.value === 'demo')
const entityOptions = computed(() => entities.value.map(entity => ({ value: entity.id, label: entity.name })))
const grainOptions = [{ value: 'week', label: '按周' }, { value: 'month', label: '按月' }]
const horizonOptions = computed(() => Array.from({ length: grain.value === 'week' ? 8 : 6 }, (_, index) => ({ value: String(index + 1), label: `${index + 1} ${grain.value === 'week' ? '周' : '月'}` })))
function setHorizon(value: string) { horizon.value = Number(value) }
</script>

<template>
  <div class="dashboard-page">
  <section class="page-heading scenic-heading">
    <p>查看接待人次的历史变化与经过回测的周／月预测。</p>
    <div class="toolbar">
      <div class="field scenic-entity-field"><span>选择景区</span><UiSelect v-model="entityId" :options="entityOptions" label="选择景区" searchable /></div>
      <div class="field"><span>时间粒度</span><UiSelect :model-value="grain" :options="grainOptions" label="时间粒度" @update:model-value="setGrain" /></div>
      <label v-if="grain === 'month'" class="field period-field">开始月份<input v-model="startPeriod" type="month" :max="maxPeriod" @focus="refreshToday" /></label>
      <div v-else class="field period-field" @focusin="refreshToday"><span>开始周次</span><WeekPicker v-model="startPeriod" label="开始周次" :max="maxPeriod" /></div>
      <label v-if="grain === 'month'" class="field period-field">结束月份<input v-model="endPeriod" type="month" :min="startPeriod" :max="maxPeriod" @focus="refreshToday" /></label>
      <div v-else class="field period-field" @focusin="refreshToday"><span>结束周次</span><WeekPicker v-model="endPeriod" label="结束周次" :min="startPeriod" :max="maxPeriod" align="right" /></div>
      <button class="button-subtle" :disabled="loading" @click="reload">{{ loading ? '加载中…' : '查询' }}</button>
    </div>
  </section>

  <div v-if="error" class="state-box error">{{ error }}</div>
  <ChartLoading v-else-if="chartLoading && !series" class="initial-chart-loading" kind="line" label="正在读取景区数据…" />
  <template v-else>
    <div class="scenic-summary">
      <article class="panel metric-card scenic-stat">
        <div class="metric-icon" aria-hidden="true"><svg viewBox="0 0 48 48"><path d="M4 37 17 14l8 13 6-9 13 19H4Z"/><path d="m12 29 5-8 5 8m5 1 4-6 4 6"/></svg></div>
        <div class="metric-content"><div class="metric-head"><span class="metric-index">01</span><span class="metric-label">观察对象</span></div><div class="metric-amount"><strong class="entity-name">{{ selectedEntity?.name ?? '未选择' }}</strong></div><div class="metric-bottom"><span>指标：{{ metric?.display_name ?? '景区接待人次' }}</span></div></div>
      </article>
      <article class="panel metric-card scenic-stat">
        <div class="metric-icon" aria-hidden="true"><svg viewBox="0 0 48 48"><path d="M8 37h32M11 31l8-8 6 5 12-16"/><path d="M30 12h7v7"/></svg></div>
        <div class="metric-content"><div class="metric-head"><span class="metric-index">02</span><span class="metric-label">最新周期接待人次</span></div><div class="metric-amount"><strong>{{ formatValue(last?.value) }}</strong><span>{{ last?.unit ?? '人次' }}</span></div><div class="metric-bottom"><span>{{ last ? formatPeriod(last.period_start, last.period_end, last.grain) : '暂无数据' }}</span></div></div>
      </article>
      <article class="panel metric-card scenic-stat">
        <div class="metric-icon" aria-hidden="true"><svg viewBox="0 0 48 48"><path d="M24 5v19l15 11"/><path d="M20 5a19 19 0 1 0 22 25H24V5"/></svg></div>
        <div class="metric-content"><div class="metric-head"><span class="metric-index">03</span><span class="metric-label">数据覆盖率</span></div><div class="metric-amount"><strong>{{ formatRate(series ? series.coverage_ratio * 100 : null) }}</strong></div><div class="metric-bottom"><span>缺失周期 {{ series?.missing_periods.length ?? 0 }} 个</span></div></div>
      </article>
      <article class="panel metric-card scenic-stat">
        <div class="metric-icon" aria-hidden="true"><svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="17"/><circle cx="24" cy="24" r="8"/><path d="M24 3v8m0 26v8M3 24h8m26 0h8"/></svg></div>
        <div class="metric-content"><div class="metric-head"><span class="metric-index">04</span><span class="metric-label">预测回测误差</span></div><div class="metric-amount"><strong>{{ forecast?.backtest ? formatRate(forecast.backtest.smape) : '—' }}</strong></div><div class="metric-bottom"><span>sMAPE · {{ forecast?.model_version ?? '暂无模型' }}</span></div></div>
      </article>
    </div>

    <div class="scenic-dashboard dashboard-body">
      <section class="panel section-panel history-panel">
        <div class="panel-head"><h2 class="panel-title">历史接待趋势</h2><span class="panel-kicker">ACTUAL / {{ grain === 'week' ? '周' : '月' }}</span></div>
        <ChartLoading v-if="chartLoading" kind="line" label="正在加载历史接待趋势…" />
        <TrendChart v-else-if="series?.points.length" :points="series.points" name="实际接待人次" unit="人次" />
        <div v-else class="state-box">选定区间没有可展示的数据。</div>
        <div class="chart-caption">实线表示已核验实际值；缺失周期不补造。{{ series?.missing_periods.length ? `缺失 ${series.missing_periods.length} 个周期。` : '' }}</div>
      </section>
      <section class="panel section-panel forecast-panel">
        <div class="panel-head"><h2 class="panel-title">历史预测与区间</h2><div class="field horizon-field"><span>预测步数</span><UiSelect :model-value="String(horizon)" :options="horizonOptions" label="预测步数" @update:model-value="setHorizon" /></div></div>
        <ChartLoading v-if="chartLoading" kind="line" label="正在加载预测曲线…" />
        <ForecastChart v-else-if="forecast?.status === 'ready' && forecast.forecast_points.length" :actual="series?.points ?? []" :forecast="forecast.forecast_points" unit="人次" />
        <div v-else class="state-box">{{ forecast?.reason ?? '数据不足，暂不输出预测曲线。' }}</div>
        <div class="chart-caption">预测值用虚线、上下界用点线表示。回测 MAE {{ formatValue(forecast?.backtest?.mae) }} 人次；样本数 {{ forecast?.backtest?.sample_count ?? '—' }}。</div>
      </section>
      <section class="panel section-panel change-panel">
        <div class="panel-head"><h2 class="panel-title">{{ grain === 'week' ? '周度' : '月度' }}环比变化</h2><span class="panel-kicker">单位：%</span></div>
        <ChartLoading v-if="chartLoading" kind="bar" label="正在计算环比变化…" />
        <TrendChart v-else-if="series?.points.length" :points="series.points" variant="period-change" />
        <div v-else class="state-box">没有可计算环比的相邻周期。</div>
        <div class="chart-caption">仅按相邻有效周期计算；首期及缺失周期不计算环比。</div>
      </section>
      <section class="panel section-panel source-panel">
        <div class="panel-head"><h2 class="panel-title">指标口径与来源</h2></div>
        <div class="source-grid">
          <div><span>指标定义</span><p>{{ metric?.definition ?? '景区在统计周期内的接待人次。' }}</p></div>
          <div><span>统计时间</span><p>{{ last ? formatPeriod(last.period_start, last.period_end, last.grain) : '—' }}</p></div>
          <div><span>数据来源</span><p v-if="isDemo" class="text-warn">静态样例，非官方统计</p><p v-else-if="source"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.publisher }} · {{ source.title }} ↗</a></p><p v-else>{{ last?.source_ids.join('、') || '尚未提供' }}</p></div>
          <div><span>最近更新时间</span><p>{{ isDemo ? '暂无接口更新' : last?.ingested_at ?? '尚未提供' }}</p></div>
        </div>
      </section>
      <section class="panel section-panel quality-panel">
        <div class="panel-head"><h2 class="panel-title">回测与数据质量</h2></div>
        <dl class="quality-list">
          <div><dt>回测 MAE</dt><dd>{{ forecast?.backtest?.mae == null ? '—' : `${formatValue(forecast.backtest.mae)} 人次` }}</dd></div>
          <div><dt>回测 sMAPE</dt><dd>{{ formatRate(forecast?.backtest?.smape) }}</dd></div>
          <div><dt>样本数</dt><dd>{{ forecast?.backtest?.sample_count ?? '—' }}</dd></div>
          <div><dt>数据来源</dt><dd>{{ isDemo ? '静态样例，非官方统计' : source?.publisher || last?.source_ids.join('、') || '尚未提供' }}</dd></div>
          <div><dt>模型</dt><dd>{{ forecast?.model_version ?? '暂无模型' }}</dd></div>
        </dl>
        <p class="quality-footnote">数据不足时不生成预测；缺失周期保持缺失。</p>
      </section>
    </div>
  </template>
  </div>
</template>

<style scoped>
.scenic-heading .toolbar { flex-wrap: nowrap; justify-content: flex-end; }
.scenic-heading .field { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
.scenic-entity-field { --select-min-width: 153px; }
.period-field input { min-width: 140px; border-radius: 0; }
.scenic-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 11px; margin-bottom: 11px; }
.initial-chart-loading { flex: 1; min-height: 320px; }
.scenic-stat { display: flex; align-items: center; gap: 14px; min-height: 104px; }
.scenic-stat .metric-icon { width: 64px; height: 64px; flex: none; display: grid; place-items: center; border: 1px solid rgba(0, 212, 255, .42); border-radius: 50%; color: var(--accent); background: radial-gradient(circle, rgba(0, 212, 255, .13), rgba(0, 212, 255, .02) 70%); }
.scenic-stat .metric-icon svg { width: 38px; height: 38px; fill: none; stroke: currentColor; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; filter: drop-shadow(0 0 5px rgba(0, 212, 255, .5)); }
.scenic-stat .metric-content { min-width: 0; flex: 1; }
.scenic-stat .metric-amount { min-width: 0; }
.scenic-stat .metric-amount strong { overflow: hidden; text-overflow: ellipsis; }
.scenic-stat .metric-amount .entity-name { color: var(--text-title); font-size: clamp(18px, 1.4vw, 24px); letter-spacing: 0; }
.scenic-stat .metric-bottom { min-height: 22px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.scenic-dashboard { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); grid-template-rows: minmax(0, 1fr) clamp(205px, 27vh, 290px); gap: 11px; }
.scenic-dashboard > .panel { min-height: 0; display: flex; flex-direction: column; }
.scenic-dashboard > .panel .chart-caption { margin-top: auto; }
.history-panel, .forecast-panel, .change-panel { grid-column: span 6; }
.source-panel, .quality-panel { grid-column: span 3; }
.scenic-dashboard :deep(.chart-canvas) { flex: 1; min-height: 0; height: auto; }
.horizon-field { display: flex; align-items: center; gap: 7px; white-space: nowrap; --select-min-width: 74px; --select-height: 29px; }.horizon-field :deep(.ui-select-trigger) { font-size: 12px; }
.source-grid { display: grid; grid-template-columns: 1fr; gap: 7px; }.source-grid > div { padding-bottom: 6px; border-bottom: 1px solid var(--border-soft); }.source-grid span { color: var(--text-sub); font-size: 11px; }.source-grid p { margin: 3px 0 0; color: var(--text-main); font-size: 12px; line-height: 1.3; word-break: break-word; }.source-grid a { color: var(--accent); }
.quality-footnote { margin: auto 0 0; padding-top: 7px; border-top: 1px solid var(--border-soft); color: var(--text-sub); font-size: 11px; line-height: 1.3; }
.quality-list { display: grid; gap: 0; margin: 0; font-size: 12px; }
.quality-list > div { display: grid; grid-template-columns: minmax(85px, 1fr) minmax(0, 1.3fr); gap: 8px; padding: 5px 2px; border-bottom: 1px solid var(--border-soft); }
.quality-list dt { color: var(--text-sub); }.quality-list dd { min-width: 0; margin: 0; color: var(--text-main); overflow-wrap: anywhere; }
@media (max-width: 1500px) { .scenic-heading { align-items: flex-start; flex-direction: column; }.scenic-heading .toolbar { flex-wrap: wrap; justify-content: flex-start; } }
@media (max-width: 1180px) { .scenic-summary { grid-template-columns: repeat(2, 1fr); }.scenic-dashboard { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }.history-panel, .forecast-panel, .change-panel, .source-panel, .quality-panel { grid-column: auto; }.change-panel { grid-column: 1 / -1; }.scenic-dashboard :deep(.chart-canvas) { flex: none; height: 320px; }.source-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 820px) { .scenic-dashboard { grid-template-columns: 1fr; }.history-panel, .forecast-panel, .change-panel, .source-panel, .quality-panel { grid-column: 1; }.scenic-heading .toolbar { justify-content: flex-start; } }
@media (max-width: 560px) { .scenic-summary, .source-grid { grid-template-columns: 1fr; } }
@media (min-width: 1101px) and (max-height: 850px) {
  .scenic-summary { gap: 8px; margin-bottom: 8px; }
  .scenic-stat { min-height: 77px; gap: 9px; }
  .scenic-stat .metric-icon { width: 46px; height: 46px; }
  .scenic-stat .metric-icon svg { width: 28px; height: 28px; }
  .scenic-stat .metric-amount { padding: 4px 0; }
  .scenic-stat .metric-amount strong { font-size: 25px; }
  .scenic-stat .metric-amount .entity-name { font-size: 17px; }
  .scenic-dashboard { grid-template-rows: minmax(0, 1fr) 190px; gap: 8px; }
  .source-grid { gap: 3px; }
  .source-grid > div { padding-bottom: 3px; }
  .source-grid span, .source-grid p, .quality-list, .quality-footnote { font-size: 10px; }
  .quality-list > div { padding: 3px 0; }
}
</style>
