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
import { useOverview } from './overview'

const router = useRouter()
const { grain, loading, error, overview, series, forecast, events, reload } = useOverview()
const isDemo = computed(() => backendMode.value === 'demo')
const latestPoint = computed(() => series.value?.points.at(-1))
const transportNotice = ref('')

function openEntity(entity: Entity) {
  if (entity.type === 'scenic') void router.push({ name: 'scenic', query: { id: entity.id } })
  else transportNotice.value = `${entity.name}的详情页由 B 模块提供，接入后可从此处打开。`
}
</script>

<template>
  <section class="page-heading">
    <div><span class="eyebrow">OVERVIEW / 安徽省</span><h1>全域客流总览</h1><p>景区、铁路站与机场按各自指标展示，帮助快速定位客流变化。</p></div>
    <div class="toolbar">
      <label class="field">统计粒度<select v-model="grain"><option value="week">按周</option><option value="month">按月</option></select></label>
      <button class="button-subtle" :disabled="loading" @click="reload">{{ loading ? '加载中…' : '刷新数据' }}</button>
    </div>
  </section>

  <div v-if="error" class="state-box error">{{ error }}</div>
  <div v-else-if="loading && !overview" class="state-box">正在加载总览数据…</div>
  <template v-else-if="overview">
    <div class="metric-grid">
      <MetricCard v-for="(card, index) in overview.cards" :key="`${card.metric}-${card.entity_id ?? index}`" :card="card" :index="index" :demo="isDemo" />
    </div>

    <div class="dashboard-grid">
      <div class="dashboard-column">
        <section class="panel section-panel chart-panel">
          <div class="panel-head"><h2 class="panel-title">景区历史趋势</h2><span class="panel-kicker">{{ series?.entity.name ?? '景区' }} / {{ grain === 'week' ? '周' : '月' }}</span></div>
          <TrendChart v-if="series?.points.length" :points="series.points" name="实际接待人次" unit="人次" />
          <div v-else class="state-box">没有符合当前粒度的已核验数据。</div>
          <div class="chart-caption">指标：景区接待人次 · 单位：人次 · 统计期：{{ latestPoint ? formatPeriod(latestPoint.period_start, latestPoint.period_end, latestPoint.grain) : '—' }} · {{ isDemo ? '来源与更新：演示数据' : `来源：${latestPoint?.source_ids.join('、') || '待标注'} · 更新：${latestPoint?.ingested_at || '待标注'}` }}</div>
        </section>
        <section class="panel section-panel side-summary">
        <div class="panel-head"><h2 class="panel-title">观察摘要</h2><span class="panel-kicker">STATUS / 当前视图</span></div>
        <div class="summary-hero"><small>当前有效事件</small><strong>{{ formatValue(overview.active_events_count) }}</strong><span>项</span></div>
        <div class="summary-rule" />
        <div class="summary-row"><span>景区示例趋势</span><strong>{{ series?.entity.name ?? '暂无景区' }}</strong></div>
        <div class="summary-row"><span>最新统计期</span><strong>{{ latestPoint ? formatPeriod(latestPoint.period_start, latestPoint.period_end, latestPoint.grain) : '暂无数据' }}</strong></div>
        <div class="summary-row"><span>历史预测</span><strong :class="forecast?.status === 'ready' ? 'text-green' : 'text-warn'">{{ forecast?.status === 'ready' ? '可展示' : '数据不足' }}</strong></div>
        <div class="summary-row"><span>最近更新</span><strong>{{ isDemo ? '演示数据' : overview.data_freshness[0]?.ingested_at ?? '尚未提供' }}</strong></div>
        </section>
      </div>
      <section class="panel section-panel map-panel">
        <div class="panel-head"><h2 class="panel-title">客流对象分布</h2><span class="panel-kicker">MAP / 点击景区进入详情</span></div>
        <AnhuiMap :entities="overview.map_entities" @select="openEntity" />
        <div v-if="transportNotice" class="transport-notice" role="status">{{ transportNotice }}</div>
        <div class="meta-strip"><span>覆盖对象 <strong>{{ overview.map_entities.length }} 个</strong></span><span>景区、铁路发送与机场吞吐量分开统计</span><span v-if="isDemo">位置与数值均为演示</span></div>
      </section>
      <div class="dashboard-column">
        <section class="panel section-panel chart-panel">
        <div class="panel-head"><h2 class="panel-title">历史趋势预测</h2><span class="panel-kicker">历史实线 / 预测虚线</span></div>
        <ForecastChart v-if="forecast?.status === 'ready' && forecast.forecast_points.length" :actual="series?.points ?? []" :forecast="forecast.forecast_points" unit="人次" />
        <div v-else class="state-box">{{ forecast?.reason ?? '历史数据不足，暂不绘制预测曲线。' }}</div>
        <div class="chart-caption">{{ forecast?.status === 'ready' ? `回测 MAE：${formatValue(forecast.backtest?.mae)} 人次 · sMAPE：${forecast.backtest?.smape ?? '—'}% · 模型：${forecast.model_version ?? '—'} · 训练截至：${forecast.training_window?.end ?? '—'} · ${isDemo ? '演示数据' : `来源：${forecast.source_ids?.join('、') || '待标注'}`}` : '数据不足时不生成预测值。' }}</div>
        </section>
        <section class="panel section-panel event-panel">
          <div class="panel-head"><h2 class="panel-title">事件摘要</h2><span class="panel-kicker">{{ isDemo ? '演示模式不模拟公告' : `${events.length} 条` }}</span></div>
          <div v-if="!events.length" class="event-empty">{{ isDemo ? '无演示事件。正式事件以官方来源为准。' : '当前筛选下无有效事件。' }}</div>
          <div v-for="event in events.slice(0, 3)" :key="event.id" class="event-item"><span :class="event.authority === 'official' ? 'text-accent' : 'text-warn'">{{ event.authority === 'official' ? '官方公告' : '模型提示' }}</span><strong>{{ event.title }}</strong></div>
        </section>
      </div>
    </div>
    <div v-if="loading" class="updating-tip">正在更新数据…</div>
  </template>
</template>

<style scoped>
.dashboard-grid { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(390px, 1.42fr) minmax(260px, 1fr); gap: 11px; align-items: stretch; }
.dashboard-column { min-width: 0; display: grid; grid-template-rows: minmax(0, 1fr) auto; gap: 11px; }
.chart-panel { min-width: 0; display: flex; flex-direction: column; }
.chart-panel :deep(.chart-canvas) { height: clamp(200px, calc(100vh - 690px), 360px); flex: none; }
.chart-panel .chart-caption { margin-top: auto; }
.map-panel { display: flex; flex-direction: column; }
.map-panel :deep(.map-stage) { flex: none; height: clamp(350px, calc(100vh - 465px), 650px); }
.event-panel { min-height: 174px; }
.side-summary { display: flex; flex-direction: column; }
.summary-hero { display: flex; align-items: baseline; gap: 7px; padding: 5px 7px 10px; }
.summary-hero small { margin-right: auto; color: var(--text-sub); font-size: 11px; }
.summary-hero strong { color: var(--accent); font-size: 34px; font-weight: 700; line-height: 1; text-shadow: 0 0 17px rgba(0,212,255,.24); }
.summary-hero span { color: var(--text-sub); font-size: 12px; }
.summary-rule { height: 1px; margin: 0 2px 13px; background: var(--border-soft); }
.summary-row { display: flex; justify-content: space-between; gap: 9px; padding: 5px 3px; font-size: 10px; }
.summary-row span { color: var(--text-sub); }.summary-row strong { color: var(--text-main); font-weight: 500; text-align: right; }
.event-empty { margin-top: 8px; padding: 10px; border: 1px dashed var(--border-soft); color: var(--text-sub); font-size: 10px; line-height: 1.6; }
.event-item { display: grid; gap: 5px; padding: 10px 0; border-bottom: 1px solid var(--border-soft); font-size: 11px; }.event-item strong { font-weight: 500; }
.transport-notice { margin-top: 9px; color: var(--accent-warn); font-size: 11px; }
.updating-tip { padding-top: 10px; color: var(--text-sub); font-size: 11px; }
@media (max-width: 1160px) { .dashboard-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.map-panel { grid-column: 1 / -1; grid-row: 1; }.map-panel :deep(.map-stage) { height: 420px; } }
@media (max-width: 700px) { .dashboard-grid { grid-template-columns: 1fr; }.map-panel { grid-column: auto; grid-row: auto; }.dashboard-column { display: contents; } }
@media (min-width: 1161px) and (max-height: 850px) {
  .dashboard-grid, .dashboard-column { gap: 8px; }
  .map-panel :deep(.map-stage) { height: min(50vh, 360px); flex: none; }
  .chart-panel :deep(.chart-canvas) { height: min(28vh, 202px); min-height: 0; flex: none; }
  .side-summary { min-height: 0; }
  .summary-hero { padding: 2px 4px 5px; }
  .summary-hero strong { font-size: 26px; }
  .summary-rule { margin-bottom: 5px; }
  .summary-row { padding: 3px 2px; font-size: 9px; }
  .event-panel { min-height: 0; }
  .event-empty { margin-top: 4px; padding: 6px; }
  .meta-strip { margin-top: 6px; font-size: 8px; }
}
</style>
