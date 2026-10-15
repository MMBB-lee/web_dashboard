<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { backendMode } from '../../app/data'
import { formatPeriod, formatValue } from '../../app/format'
import type { Entity } from '../../app/types'
import MetricCard from '../../components/layout/MetricCard.vue'
import AnhuiMap from '../../components/map/AnhuiMap.vue'
import TrendChart from '../../components/charts/TrendChart.vue'
import ForecastChart from '../../components/charts/ForecastChart.vue'
import HolidayCompareChart from '../../components/charts/HolidayCompareChart.vue'
import ChartLoading from '../../components/charts/ChartLoading.vue'
import { useVisibleChartLoading } from '../../components/charts/useVisibleChartLoading'
import UiSelect from '../../components/ui/UiSelect.vue'
import { useOverview } from './overview'

const router = useRouter()
const {
  grain, scenicId, railId, airportId, selectedScenicId, selectedRailId, selectedAirportId,
  scenicEntities, railEntities, airportEntities, selectedCards, mapEntities,
  loading, error, overview, series, railSeries, airportSeries, seriesError, railError, airportError,
  forecast, forecastError, events, holidayComparison, holidayError, reload,
} = useOverview()
const chartLoading = useVisibleChartLoading(loading)
const isDemo = computed(() => backendMode.value === 'demo')
const latestPoint = computed(() => series.value?.points.at(-1))
const previousPoint = computed(() => series.value?.points.at(-2))
const transportMode = ref<'rail_station' | 'airport'>('rail_station')
const transportSeries = computed(() => transportMode.value === 'rail_station' ? railSeries.value : airportSeries.value)
const transportEntity = computed(() => transportMode.value === 'rail_station'
  ? railEntities.value.find(entity => entity.id === selectedRailId.value)
  : airportEntities.value.find(entity => entity.id === selectedAirportId.value))
const transportError = computed(() => transportMode.value === 'rail_station' ? railError.value : airportError.value)
const transportMetric = computed(() => transportMode.value === 'rail_station' ? '铁路发送人次' : '机场旅客吞吐量')
const transportLastPoint = computed(() => transportSeries.value?.points.at(-1))
const demoTrendNote = computed(() => {
  const latest = latestPoint.value?.value
  const previous = previousPoint.value?.value
  if (latest == null || previous == null) return '当前没有可比较的景区趋势数据。'
  const direction = latest < previous ? '回落' : latest > previous ? '上升' : '持平'
  return `${series.value?.entity.name ?? '所选景区'}最新样例周期接待 ${formatValue(latest)} 人次，较上一周期${direction}。`
})
const transportNotice = ref('')
const grainOptions = [{ value: 'week', label: '按周' }, { value: 'month', label: '按月' }]
const keyObjects = computed(() => [...(overview.value?.map_entities ?? [])]
  .filter(entity => entity.type === 'scenic' && entity.latest_metric === 'scenic_visits' && entity.latest_value != null)
  .sort((a, b) => (b.latest_value ?? 0) - (a.latest_value ?? 0))
  .slice(0, 5))
const maxKeyValue = computed(() => keyObjects.value[0]?.latest_value ?? 1)
const holidayItems = computed(() => holidayComparison.value?.items.filter(item => item.value != null) ?? [])

function setGrain(value: string) {
  if (value === 'week' || value === 'month') grain.value = value
}

function setScenic(value: string) {
  scenicId.value = value
}

function optionsFor(metric: string) {
  const entities = metric === 'scenic_visits' ? scenicEntities.value
    : metric === 'rail_departures' ? railEntities.value : airportEntities.value
  return entities.map(entity => ({ value: entity.id, label: entity.name }))
}

function selectedIdFor(metric: string) {
  return metric === 'scenic_visits' ? selectedScenicId.value
    : metric === 'rail_departures' ? selectedRailId.value : selectedAirportId.value
}

function setCardSelection(metric: string, id: string) {
  if (metric === 'scenic_visits') setScenic(id)
  else if (metric === 'rail_departures') railId.value = id
  else if (metric === 'airport_throughput') airportId.value = id
}

function openEntity(entity: Entity) {
  if (entity.type === 'scenic') void router.push({ name: 'scenic', query: { id: entity.id } })
  else if (router.hasRoute('b-transport')) void router.push({ name: 'b-transport', query: { entity_id: entity.id } })
  else transportNotice.value = `${entity.name}的交通客流页面尚未接入。`
}
</script>

<template>
  <div class="dashboard-page">
  <section class="page-heading">
    <p>景区、铁路站与机场按各自指标展示，帮助快速定位客流变化。</p>
    <div class="toolbar">
      <div class="field grain-field"><span>统计粒度</span><UiSelect :model-value="grain" :options="grainOptions" label="统计粒度" @update:model-value="setGrain" /></div>
      <button class="button-subtle" :disabled="loading" @click="reload">{{ loading ? '加载中…' : '刷新数据' }}</button>
    </div>
  </section>

  <div v-if="error" class="state-box error">{{ error }}</div>
  <ChartLoading v-else-if="chartLoading && !overview" class="initial-chart-loading" kind="line" label="正在读取总览数据…" />
  <template v-else-if="overview">
    <div class="metric-grid">
      <MetricCard v-for="(card, index) in selectedCards" :key="`${card.metric}-${index}`" :card="card" :index="index" :demo="isDemo" :selected-id="selectedIdFor(card.metric)" :options="optionsFor(card.metric)" @select="setCardSelection(card.metric, $event)" />
    </div>

    <div class="dashboard-grid dashboard-body">
      <div class="dashboard-column">
        <section class="panel section-panel chart-panel">
          <div class="panel-head"><h2 class="panel-title">景区历史及未来趋势</h2><span class="panel-kicker">{{ grain === 'week' ? '周' : '月' }}度 · 历史实线 / 预测虚线</span></div>
          <ChartLoading v-if="chartLoading" kind="line" label="正在加载景区趋势…" />
          <ForecastChart v-else-if="forecast?.status === 'ready' && forecast.forecast_points.length" :actual="series?.points ?? []" :forecast="forecast.forecast_points" unit="人次" />
          <TrendChart v-else-if="series?.points.length" :points="series.points" name="实际接待人次" unit="人次" />
          <div v-else class="state-box">{{ seriesError || '没有符合当前粒度的已核验数据。' }}</div>
          <div class="chart-caption">{{ series?.entity.name ?? '景区' }} · 景区接待人次 · 人次 · 统计期：{{ latestPoint ? formatPeriod(latestPoint.period_start, latestPoint.period_end, latestPoint.grain) : '—' }} · {{ isDemo ? '静态样例，非官方统计' : `来源：${latestPoint?.source_ids.join('、') || '待标注'} · 更新：${latestPoint?.ingested_at || '待标注'}` }}<template v-if="forecast?.status === 'ready'"> · 回测 MAE：{{ formatValue(forecast.backtest?.mae) }} 人次 · sMAPE：{{ forecast.backtest?.smape ?? '—' }}% · 模型：{{ forecast.model_version ?? '—' }}</template><template v-else-if="forecastError || forecast?.reason"> · {{ forecastError || forecast?.reason }}</template></div>
        </section>
        <section class="panel section-panel holiday-panel">
          <div class="panel-head"><h2 class="panel-title">节假日对比</h2><span class="panel-kicker">国庆节 · 同期日均</span></div>
          <ChartLoading v-if="chartLoading && !holidayComparison" kind="bar" label="正在读取假期数据…" />
          <HolidayCompareChart v-else-if="holidayComparison?.comparable && holidayItems.length === 2" :items="holidayItems" />
          <div v-else class="state-box compact-state">{{ holidayError || holidayComparison?.reason || '缺少可比较的假期数据。' }}</div>
          <div class="chart-caption">{{ series?.entity.name ?? '景区' }} · 景区接待人次 · 人次/日 · {{ isDemo ? '静态样例，非官方统计' : '来源见假期专题' }}</div>
        </section>
      </div>
      <div class="dashboard-column dashboard-center">
        <section class="panel section-panel map-panel">
          <div class="panel-head"><h2 class="panel-title">客流对象分布</h2><span class="panel-kicker">MAP / 点击对象进入详情</span></div>
          <AnhuiMap :entities="mapEntities" :scenic-id="selectedScenicId" :rail-id="selectedRailId" :airport-id="selectedAirportId" :demo="isDemo" @select="openEntity" />
          <div v-if="transportNotice" class="transport-notice" role="status">{{ transportNotice }}</div>
          <div class="meta-strip"><span>覆盖对象 <strong>{{ mapEntities.length }} 个</strong></span><span>动态连线仅示意地点关联，非实际出行轨迹</span><span v-if="isDemo">位置与数值为静态样例</span></div>
        </section>
        <section class="panel section-panel key-panel">
          <div class="panel-head"><h2 class="panel-title">重点对象</h2><span class="panel-kicker">景区接待人次 TOP 5</span></div>
          <div v-if="keyObjects.length" class="key-list">
            <button v-for="(entity, index) in keyObjects" :key="entity.id" class="key-row" type="button" :aria-label="`查看${entity.name}，${formatValue(entity.latest_value)}人次`" @click="openEntity(entity)">
              <span class="key-rank">{{ index + 1 }}</span><span class="key-name">{{ entity.name }}</span>
              <span class="key-track"><span :style="{ width: `${Math.max(8, (entity.latest_value ?? 0) / maxKeyValue * 100)}%` }" /></span>
              <strong>{{ formatValue(entity.latest_value) }}</strong>
            </button>
          </div>
          <div v-else class="state-box compact-state">当前{{ grain === 'week' ? '周' : '月' }}粒度下暂无景区接待数据。</div>
          <div class="chart-caption">单位：人次 · {{ grain === 'week' ? '周' : '月' }}度最新值 · {{ isDemo ? '静态样例' : '以接口返回口径为准' }}</div>
        </section>
      </div>
      <div class="dashboard-column">
        <section class="panel section-panel chart-panel transport-chart-panel">
        <div class="panel-head"><h2 class="panel-title">交通客流趋势</h2><div class="transport-switch" role="group" aria-label="选择交通客流类型"><button type="button" :aria-pressed="transportMode === 'rail_station'" :class="{ active: transportMode === 'rail_station' }" @click="transportMode = 'rail_station'">火车站</button><button type="button" :aria-pressed="transportMode === 'airport'" :class="{ active: transportMode === 'airport' }" @click="transportMode = 'airport'">机场</button></div></div>
        <ChartLoading v-if="chartLoading" kind="line" label="正在加载交通客流…" />
        <TrendChart v-else-if="transportSeries?.points.length" :points="transportSeries.points" :name="transportMetric" unit="人次" />
        <div v-else class="state-box">{{ transportError || '所选地点暂无可展示的客流序列。' }}</div>
        <div class="chart-caption">{{ transportEntity?.name ?? '所选地点' }} · {{ transportMetric }} · 人次 · 统计期：{{ transportLastPoint ? formatPeriod(transportLastPoint.period_start, transportLastPoint.period_end, transportLastPoint.grain) : '—' }} · {{ isDemo ? '静态样例，非官方统计' : `来源：${transportLastPoint?.source_ids.join('、') || '待标注'} · 更新：${transportLastPoint?.ingested_at || '待标注'}` }}</div>
        </section>
        <section class="panel section-panel event-panel">
          <div class="panel-head"><h2 class="panel-title">事件摘要</h2><span class="panel-kicker">{{ isDemo ? '演示数据 · 以官方公告为准' : `${events.length} 条有效事件` }}</span></div>
          <div v-if="isDemo && !events.length" class="event-notes">
            <div class="event-note"><span class="note-dot" /><div><strong>景区样例趋势</strong><p>{{ demoTrendNote }}</p></div></div>
            <div class="event-note"><span class="note-dot green" /><div><strong>重点对象持续关注</strong><p>地图已覆盖 {{ overview.map_entities.length }} 个演示对象，可分别查看景区、铁路站与机场。</p></div></div>
            <div class="event-note"><span class="note-dot warn" /><div><strong>官方事件公告</strong><p>暂无已接入公告。以上为样例数据观察，不构成官方预警。</p></div></div>
          </div>
          <div v-else-if="!events.length" class="event-empty">当前筛选下无有效事件。</div>
          <div v-for="event in events.slice(0, 3)" :key="event.id" class="event-item"><span :class="event.authority === 'official' ? 'text-accent' : 'text-warn'">{{ event.authority === 'official' ? '官方公告' : '模型提示' }}</span><strong>{{ event.title }}</strong></div>
        </section>
      </div>
    </div>
  </template>
  </div>
</template>

<style scoped>
/* 压缩总览指标卡，把剩余高度交给下方图表和地图。 */
.metric-grid :deep(.metric-card) { padding: 6px 12px; gap: 10px; }
.metric-grid :deep(.metric-icon) { width: 48px; height: 48px; }
.metric-grid :deep(.metric-icon svg) { width: 30px; height: 30px; }
.metric-grid :deep(.metric-entity-select) { --select-height: 26px; }
.metric-grid :deep(.metric-amount) { padding: 3px 0; }
.metric-grid :deep(.metric-amount strong) { font-size: clamp(24px, 1.7vw, 29px); }
.metric-grid :deep(.metric-bottom) { padding-top: 4px; font-size: 11px; }
.dashboard-grid { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(390px, 1.42fr) minmax(260px, 1fr); grid-template-rows: minmax(0, 1fr); gap: 11px; align-items: stretch; }
.dashboard-column { min-width: 0; min-height: 0; display: grid; grid-template-rows: minmax(0, 1fr) clamp(210px, 23vh, 270px); gap: 11px; }
.dashboard-center { grid-template-rows: minmax(0, 1fr) 180px; }
.grain-field { display: flex; align-items: center; gap: 10px; }
.grain-field > span { white-space: nowrap; }
.chart-panel { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.initial-chart-loading { flex: 1; min-height: 320px; }
.transport-switch { display: inline-flex; flex: none; overflow: hidden; padding: 2px; border: 1px solid var(--border-glow); border-radius: 999px; background: rgba(0, 212, 255, .06); }
.transport-switch button { min-width: 52px; padding: 3px 10px; border: 0; border-radius: 999px; background: transparent; color: var(--text-sub); font-size: 12px; line-height: 1.2; }
.transport-switch button.active { background: var(--accent); color: #062540; font-weight: 700; }
.transport-switch button:not(.active):hover { color: var(--text-title); }
.chart-panel :deep(.chart-canvas) { height: auto; min-height: 0; flex: 1; }
.chart-panel .chart-caption { margin-top: auto; }
.map-panel { min-height: 0; display: flex; flex-direction: column; }
.map-panel :deep(.map-stage) { flex: 1; min-height: 0; height: auto; }
.holiday-panel, .key-panel, .event-panel { min-height: 0; display: flex; flex-direction: column; }
.key-panel { padding: 8px 12px; }
.key-panel .panel-head { padding-bottom: 4px; }
.key-panel .chart-caption { padding-top: 4px; font-size: 11px; line-height: 1.2; }
.holiday-panel :deep(.chart-canvas) { height: auto; min-height: 105px; flex: 1; }
.holiday-panel .chart-caption, .key-panel .chart-caption { margin-top: auto; }
.compact-state { min-height: 90px; flex: 1; }
.key-list { display: grid; align-content: center; flex: 1; min-height: 0; }
.key-row { display: grid; grid-template-columns: 22px minmax(80px, 1.1fr) minmax(50px, 1.5fr) 56px; align-items: center; gap: 9px; width: 100%; min-height: 21px; padding: 1px 2px; border: 0; border-bottom: 1px solid var(--border-soft); background: transparent; color: var(--text-main); text-align: left; font-size: 12px; }
.key-row:hover, .key-row:focus-visible { background: rgba(0, 212, 255, .08); }
.key-rank { color: var(--accent); font: 700 14px Consolas, monospace; }
.key-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.key-track { height: 9px; background: rgba(0, 212, 255, .11); }
.key-track span { display: block; height: 100%; background: linear-gradient(90deg, #1486d1, var(--accent)); box-shadow: 0 0 6px rgba(0, 212, 255, .36); }
.key-row strong { color: var(--text-title); text-align: right; font-variant-numeric: tabular-nums; }
.event-empty { margin-top: 8px; padding: 10px; border: 1px dashed var(--border-soft); color: var(--text-sub); font-size: 12px; line-height: 1.45; }
.event-notes { display: grid; align-content: space-around; flex: 1; min-height: 0; }
.event-note { display: flex; gap: 9px; padding: 4px 0; }
.event-note strong { color: var(--text-title); font-size: 12px; }
.event-note p { margin: 3px 0 0; color: var(--text-sub); font-size: 11px; line-height: 1.35; }
.note-dot { flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 7px currentColor; color: var(--accent); }
.note-dot.green { background: var(--accent-green); color: var(--accent-green); }
.note-dot.warn { background: var(--accent-warn); color: var(--accent-warn); }
.event-item { display: grid; gap: 5px; padding: 10px 0; border-bottom: 1px solid var(--border-soft); font-size: 12px; }.event-item strong { font-weight: 500; }
.transport-notice { margin-top: 9px; color: var(--accent-warn); font-size: 12px; }
@media (max-width: 1160px) { .dashboard-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }.dashboard-column { grid-template-rows: auto auto; }.dashboard-center { grid-column: 1 / -1; grid-row: 1; }.map-panel :deep(.map-stage) { flex: none; height: 460px; }.chart-panel :deep(.chart-canvas) { flex: none; height: 310px; }.holiday-panel :deep(.chart-canvas) { flex: none; height: 180px; } }
@media (max-width: 700px) { .dashboard-grid { grid-template-columns: 1fr; }.dashboard-center { grid-column: auto; grid-row: auto; }.key-row { grid-template-columns: 20px minmax(75px, 1fr) minmax(50px, 1fr) 50px; gap: 6px; } }
@media (min-width: 1161px) and (max-height: 850px) {
  .dashboard-grid, .dashboard-column { gap: 8px; }
  .dashboard-column { grid-template-rows: minmax(0, 1fr) 190px; }
  .dashboard-center { grid-template-rows: minmax(0, 1fr) 162px; }
  .key-row { min-height: 19px; padding: 1px 2px; }
  .event-empty { margin-top: 4px; padding: 6px; }
  .meta-strip { margin-top: 6px; font-size: 12px; }
}
</style>
