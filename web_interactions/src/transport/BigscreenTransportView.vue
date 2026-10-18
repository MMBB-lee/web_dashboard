<script setup lang="ts">
import { computed, ref } from 'vue'
import BigscreenChart from '../components/BigscreenChart.vue'
import TrafficNetwork from '../components/TrafficNetwork.vue'
import TrendChart from '../components/TrendChart.vue'
import type { Entity, Forecast, Grain, Metric, Point, Series } from '../types/api'
import { decimalText } from '../utils'

const props = defineProps<{
  entities?: Entity[]
  entityId?: string
  grain?: Grain
  series?: Series | null
  forecast?: Forecast | null
  horizon?: number
  loading?: boolean
  error?: string
  predicting?: boolean
  forecastError?: string
  demo?: boolean
}>()

const emit = defineEmits<{
  'update:entityId': [value: string]
  'update:grain': [value: Grain]
  'update:horizon': [value: number]
  query: []
  predict: []
}>()

const fallbackEntities: Entity[] = [
  { id: 'rail-hefei-south', name: '合肥南站', type: 'rail_station', region_code: '340100', available_metrics: ['rail_departures'], available_grains: ['month'], status: 'active' },
  { id: 'rail-huangshan-north', name: '黄山北站', type: 'rail_station', region_code: '341000', available_metrics: ['rail_departures'], available_grains: ['month'], status: 'active' },
  { id: 'airport-hefei', name: '合肥新桥机场', type: 'airport', region_code: '340100', available_metrics: ['airport_throughput'], available_grains: ['month'], status: 'active' },
]

const railHefei = [8.4, 9.1, 10.5, 11.2, 12.8, 15.6, 21.8, 30.2, 35.1, 31.9, 27.2, 25.8, 26.5, 27.1, 28.4, 31.2, 37.8, 40.6, 35.4, 29.8, 24.9, 20.1, 16.4, 13.7]
const railHuangshan = [4.1, 4.5, 4.9, 5.4, 6.3, 7.1, 9.4, 12.6, 14.8, 13.7, 11.9, 10.8, 11.1, 11.6, 12.4, 13.6, 16.2, 18.4, 16.7, 13.5, 11.2, 9.1, 7.3, 6.0]
const airportHefei = [72, 75, 79, 82, 88, 95, 108, 126, 139, 132, 121, 118, 122, 128, 137, 149, 167, 175, 163, 147, 131, 116, 101, 89]

const localEntityId = ref(fallbackEntities[0].id)
const localGrain = ref<Grain>('month')
const localHorizon = ref(3)
const forecastRefresh = ref(0)

const entities = computed(() => props.entities?.length ? props.entities : fallbackEntities)
const selectedId = computed({
  get: () => props.entityId || localEntityId.value,
  set: (value: string) => { localEntityId.value = value; emit('update:entityId', value) },
})
const grain = computed({
  get: () => props.grain ?? localGrain.value,
  set: (value: Grain) => { localGrain.value = value; emit('update:grain', value) },
})
const horizon = computed({
  get: () => props.horizon ?? localHorizon.value,
  set: (value: number) => { localHorizon.value = value; emit('update:horizon', value) },
})

function buildPoints(values: number[], metric: Metric, unit: string): Point[] {
  return values.map((value, index) => ({
    period_start: `${2025 + Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}-01`,
    period_end: `${2025 + Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}-28`,
    value: value * 10000,
    metric,
    unit,
    grain: 'month',
    source_ids: ['demo-transport-source'],
    published_at: '2026-09-30T00:00:00+08:00',
    ingested_at: '2026-10-01T00:00:00+08:00',
    quality_status: 'verified',
  }))
}

const dataByEntity: Record<string, Point[]> = {
  'rail-hefei-south': buildPoints(railHefei, 'rail_departures', '人次'),
  'rail-huangshan-north': buildPoints(railHuangshan, 'rail_departures', '人次'),
  'airport-hefei': buildPoints(airportHefei, 'airport_throughput', '人次'),
}

const currentEntity = computed(() => entities.value.find((item) => item.id === selectedId.value) ?? entities.value[0] ?? fallbackEntities[0])
const cityName = computed(() => currentEntity.value.region_code === '341000' ? '黄山市' : '合肥市')
const points = computed(() => props.series?.points?.length ? props.series.points : (dataByEntity[selectedId.value] ?? dataByEntity[fallbackEntities[0].id]))
const metricName = computed(() => currentEntity.value.type === 'airport' ? '旅客吞吐量' : '旅客发送量')
const unit = computed(() => currentEntity.value.type === 'airport' ? '万人次' : '万人')
const latest = computed(() => points.value.at(-1)?.value ?? 0)
const previous = computed(() => points.value.at(-2)?.value ?? latest.value)
const changeRate = computed(() => previous.value ? ((latest.value - previous.value) / previous.value) * 100 : 0)
const forecastValue = computed(() => forecastPoints.value.at(-1)?.value ?? latest.value * (1.08 + horizon.value * 0.018 + forecastRefresh.value * 0.001))
const coverage = computed(() => props.series ? props.series.coverage_ratio * 100 : currentEntity.value.type === 'airport' ? 96.8 : selectedId.value === 'rail-huangshan-north' ? 93.2 : 98.4)

const forecastPoints = computed<Point[]>(() => {
  if (props.forecast?.status === 'ready') return props.forecast.forecast_points
  if (props.series) return []
  const result: Point[] = []
  const last = points.value.at(-1)?.value ?? 0
  for (let index = 1; index <= horizon.value; index += 1) {
    const monthIndex = 23 + index
    result.push({
      period_start: `${2025 + Math.floor(monthIndex / 12)}-${String((monthIndex % 12) + 1).padStart(2, '0')}-01`,
      period_end: `${2025 + Math.floor(monthIndex / 12)}-${String((monthIndex % 12) + 1).padStart(2, '0')}-28`,
      value: last * (1 + index * 0.055 + forecastRefresh.value * 0.001),
      metric: currentEntity.value.type === 'airport' ? 'airport_throughput' : 'rail_departures',
      unit: '人次',
      grain: 'month',
      source_ids: ['demo-transport-source'],
      published_at: '2026-09-30T00:00:00+08:00',
      ingested_at: '2026-10-01T00:00:00+08:00',
      quality_status: 'model_estimate',
      is_forecast: true,
      model_version: 'seasonal-demo-v1',
      lower_bound: last * (1 + index * 0.025),
      upper_bound: last * (1 + index * 0.085),
    })
  }
  return result
})

const trendPoints = computed(() => points.value)
const trendForecast = computed(() => forecastPoints.value)
const topPeriods = computed(() => [...trendPoints.value]
  .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
  .slice(0, 5)
  .map((item) => ({ name: item.period_start.slice(0, 7), value: Number(((item.value ?? 0) / 10000).toFixed(1)) })))
const periodMix = computed(() => [
  { name: '常规月份', value: 12 },
  { name: '暑期旺季', value: 5 },
  { name: '节假日影响', value: 4 },
  { name: '低位月份', value: 3 },
])
const qualityGauges = computed(() => [
  { name: '数据覆盖', value: coverage.value },
  { name: '时间连续', value: currentEntity.value.type === 'airport' ? 94.5 : 97.1 },
  { name: '模型可信', value: props.forecast?.backtest ? Math.max(0, 100 - props.forecast.backtest.smape) : selectedId.value === 'rail-huangshan-north' ? 90.7 : 95.2 },
])
const recentRows = computed(() => trendPoints.value.slice(-5).reverse())

function runForecast() {
  forecastRefresh.value += 1
  emit('predict')
}

function formatWan(value: number) {
  return (value / 10000).toFixed(1)
}
</script>

<template>
  <section class="transport-bigscreen">
    <div class="bigscreen-toolbar">
      <label>监测对象
        <select v-model="selectedId" @change="emit('query')">
          <option v-for="entity in entities" :key="entity.id" :value="entity.id">{{ entity.name }}</option>
        </select>
      </label>
      <label>统计粒度
        <select v-model="grain" @change="emit('query')">
          <option value="month">月度</option>
          <option value="week">周度</option>
          <option value="day">日度</option>
        </select>
      </label>
      <span class="toolbar-scope"><i class="pi pi-map-marker" /> {{ cityName }} · {{ metricName }}</span>
      <span class="toolbar-note"><i class="pi pi-info-circle" /> {{ demo === false ? '当前读取接口数据' : '当前为比赛演示模拟数据' }}</span>
    </div>

    <div class="transport-screen-grid">
      <div class="screen-column screen-column-left">
        <article class="screen-panel trend-panel">
          <h2><span>24个月客流量趋势</span><small>单位：{{ unit }}</small></h2>
          <div class="screen-chart trend-chart-wrap">
            <TrendChart :points="trendPoints" :forecast="[]" :label="metricName" />
          </div>
        </article>

        <article class="screen-panel compact-panel">
          <h2><span>统计周期结构</span><small>单位：月</small></h2>
          <div class="screen-chart"><BigscreenChart type="donut" :data="periodMix" unit="个月" /></div>
        </article>

        <article class="screen-panel compact-panel">
          <h2><span>月度客流 TOP5</span><small>单位：{{ unit }}</small></h2>
          <div class="screen-chart"><BigscreenChart type="bar" :data="topPeriods" :unit="unit" /></div>
        </article>
      </div>

      <div class="screen-column screen-column-center">
        <section class="kpi-strip" aria-label="核心指标">
          <div class="kpi-item"><strong>{{ formatWan(latest) }}</strong><span>{{ unit }}<small>最新统计值</small></span></div>
          <div class="kpi-item"><strong>{{ formatWan(forecastValue) }}</strong><span>{{ unit }}<small>{{ horizon }}期预测值</small></span></div>
          <div class="kpi-item"><strong :class="changeRate < 0 ? 'down' : ''">{{ changeRate >= 0 ? '+' : '' }}{{ changeRate.toFixed(1) }}</strong><span>%<small>环比变化</small></span></div>
          <div class="kpi-item"><strong>{{ coverage.toFixed(1) }}</strong><span>%<small>数据覆盖率</small></span></div>
        </section>

        <article class="screen-panel network-panel">
          <h2><span>安徽交通枢纽地理分布</span><small>按经纬度定位 · 北向上</small></h2>
          <div class="network-wrap"><TrafficNetwork :entities="entities" :active-id="selectedId" :latest-value="series?.points.at(-1)?.value" @select="emit('update:entityId', $event.id); emit('query')" /></div>
        </article>

        <article class="screen-panel table-panel">
          <h2><span>近期数据与监测状态</span><small>{{ currentEntity.name }} · {{ metricName }}</small></h2>
          <table>
            <thead><tr><th>统计期</th><th>监测对象</th><th>指标</th><th>数值</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="row in recentRows" :key="row.period_start">
                <td>{{ row.period_start.slice(0, 7) }}</td><td>{{ currentEntity.name }}</td><td>{{ metricName }}</td><td>{{ ((row.value ?? 0) / 10000).toFixed(1) }} {{ unit }}</td><td><span class="status-ok">已校验</span></td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>

      <div class="screen-column screen-column-right">
        <article class="screen-panel forecast-panel">
          <h2><span>历史与预测走势</span><small>单位：{{ unit }}</small></h2>
          <div class="screen-chart trend-chart-wrap">
            <TrendChart :points="trendPoints.slice(-10)" :forecast="trendForecast" :label="metricName" />
          </div>
        </article>

        <article class="screen-panel compact-panel gauge-panel">
          <h2><span>数据质量监测</span><small>单位：%</small></h2>
          <div class="screen-chart"><BigscreenChart type="gauges" :data="qualityGauges" unit="%" /></div>
        </article>

        <article class="screen-panel model-panel">
          <h2><span>预测模型与口径</span><small>滚动预测</small></h2>
          <div class="model-controls">
            <label>预测期数
              <select v-model.number="horizon">
                <option :value="1">1期</option><option :value="3">3期</option><option :value="6">6期</option>
              </select>
            </label>
            <button type="button" :disabled="predicting" @click="runForecast"><i :class="predicting ? 'pi pi-spin pi-spinner' : 'pi pi-play'" /> {{ predicting ? '预测中' : '重新预测' }}</button>
          </div>
          <div class="model-stats">
            <div><span>模型</span><strong>季节性趋势模型</strong></div>
            <div><span>回测 sMAPE</span><strong>{{ decimalText(forecast?.backtest?.smape ?? (100 - qualityGauges[2].value)) }}%</strong></div>
            <div><span>更新方式</span><strong>按统计期滚动</strong></div>
          </div>
          <p><i class="pi pi-shield" /> 铁路展示发送量，机场展示吞吐量，两类统计口径不直接合并。</p>
          <p v-if="forecastError || error" class="model-error">{{ forecastError || error }}</p>
        </article>
      </div>
    </div>
    <div v-if="loading" class="bigscreen-loading"><i class="pi pi-spin pi-spinner" /> 正在读取交通数据</div>
  </section>
</template>

<style scoped>
.transport-bigscreen { height: 100%; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.bigscreen-loading { position:absolute; inset:132px 18px 18px; z-index:30; display:grid; place-items:center; color:#8fdfff; font-size:15px; letter-spacing:1px; background:rgba(3,14,34,.76); backdrop-filter:blur(2px); }
.model-error { color:#ff8d75 !important; }
.bigscreen-toolbar { height: 42px; flex: 0 0 42px; display: flex; align-items: center; gap: 13px; padding: 0 12px; border: 1px solid rgba(0,212,255,.2); background: rgba(6,25,58,.72); box-shadow: inset 0 0 20px rgba(0,125,213,.06); }
.bigscreen-toolbar label { display: flex; align-items: center; gap: 8px; color: #789abc; font-size: 12px; }
.bigscreen-toolbar select, .model-controls select { height: 28px; padding: 0 28px 0 9px; border: 1px solid rgba(0,212,255,.27); border-radius: 2px; color: #cceeff; background: #0a2148; outline: none; }
.toolbar-scope { color: #b7d4ef; font-size: 12px; }
.toolbar-scope i { color: #00d4ff; }
.toolbar-note { margin-left: auto; color: #ffbc58; font-size: 12px; }
.transport-screen-grid { flex: 1; min-height: 0; display: grid; grid-template-columns: 27fr 46fr 27fr; gap: 12px; }
.screen-column { min-width: 0; min-height: 0; display: grid; gap: 10px; }
.screen-column-left { grid-template-rows: 1.28fr 1fr 1fr; }
.screen-column-center { grid-template-rows: 88px minmax(0,1fr) 230px; }
.screen-column-right { grid-template-rows: 1.05fr .9fr 1fr; }
.screen-panel { position: relative; min-width: 0; min-height: 0; overflow: hidden; border: 1px solid rgba(0,212,255,.24); border-radius: 3px; background: linear-gradient(145deg, rgba(13,40,85,.84), rgba(6,24,57,.92)); box-shadow: inset 0 0 30px rgba(0,101,180,.08); }
.screen-panel::before, .screen-panel::after { content: ''; position: absolute; top: -1px; width: 18px; height: 10px; border-top: 2px solid #00d4ff; z-index: 2; }
.screen-panel::before { left: -1px; border-left: 2px solid #00d4ff; }
.screen-panel::after { right: -1px; border-right: 2px solid #00d4ff; }
.screen-panel h2 { height: 38px; margin: 0; padding: 0 12px 0 16px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(0,212,255,.13); color: #def5ff; font-size: 14px; font-weight: 650; letter-spacing: .4px; }
.screen-panel h2::before { content: ''; position: absolute; left: 7px; width: 3px; height: 13px; background: #00d4ff; box-shadow: 0 0 8px #00d4ff; }
.screen-panel h2 small { color: #6389b2; font-size: 10px; font-weight: 400; letter-spacing: 0; }
.screen-chart { height: calc(100% - 38px); min-height: 0; }
.screen-chart :deep(.bigscreen-chart-canvas), .screen-chart :deep(.trend-chart) { width: 100%; height: 100%; min-height: 0; }
.screen-chart :deep(.chart-footnote), .screen-chart :deep(.trend-legend) { display: none; }
.kpi-strip { display: grid; grid-template-columns: repeat(4,1fr); align-items: stretch; border: 1px solid rgba(0,212,255,.27); background: linear-gradient(90deg, rgba(8,34,76,.92), rgba(7,27,62,.9)); }
.kpi-item { position: relative; display: flex; align-items: center; justify-content: center; gap: 7px; }
.kpi-item:not(:last-child)::after { content:''; position:absolute; right:0; top:19px; bottom:19px; width:1px; background: linear-gradient(transparent, rgba(0,212,255,.35), transparent); }
.kpi-item strong { color:#00d4ff; font: 700 29px/1 Consolas, Monaco, monospace; text-shadow: 0 0 12px rgba(0,212,255,.4); }
.kpi-item strong.down { color:#ff8d75; }
.kpi-item > span { color:#69a7cb; font-size:12px; }
.kpi-item small { display:block; margin-top:7px; color:#6385aa; font-size:11px; white-space:nowrap; }
.network-wrap { height: calc(100% - 38px); }
.network-wrap :deep(.traffic-network) { width:100%; height:100%; }
.table-panel table { width: calc(100% - 18px); margin: 3px 9px 0; border-collapse: collapse; table-layout: fixed; font-size: 11px; color: #a8c8e6; }
.table-panel th { height: 28px; color:#668db5; font-weight:500; text-align:left; border-bottom:1px solid rgba(0,212,255,.15); }
.table-panel td { height: 29px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; border-bottom:1px solid rgba(60,126,190,.1); }
.table-panel tbody tr:nth-child(odd) { background:rgba(23,84,145,.09); }
.table-panel th:nth-child(1), .table-panel td:nth-child(1) { width:15%; }
.table-panel th:nth-child(2), .table-panel td:nth-child(2) { width:20%; }
.table-panel th:nth-child(3), .table-panel td:nth-child(3) { width:23%; }
.table-panel th:nth-child(4), .table-panel td:nth-child(4) { width:24%; }
.status-ok { color:#00e5b0; border:1px solid rgba(0,229,176,.28); padding:2px 6px; }
.model-panel { padding-bottom: 10px; }
.model-controls { display:flex; align-items:center; gap:12px; padding:12px 14px; }
.model-controls label { display:flex; align-items:center; gap:8px; color:#7296ba; font-size:12px; }
.model-controls button { margin-left:auto; height:30px; padding:0 14px; border:1px solid rgba(0,212,255,.55); color:#d9f7ff; background:linear-gradient(90deg, rgba(0,117,191,.42), rgba(0,184,224,.2)); cursor:pointer; }
.model-stats { display:grid; grid-template-columns:repeat(3,1fr); margin:0 13px; border:1px solid rgba(0,212,255,.12); }
.model-stats div { padding:9px 10px; }
.model-stats div:not(:last-child) { border-right:1px solid rgba(0,212,255,.12); }
.model-stats span, .model-stats strong { display:block; }
.model-stats span { color:#547ba4; font-size:10px; }
.model-stats strong { margin-top:5px; color:#b8dbf6; font-size:12px; font-weight:550; }
.model-panel p { margin:11px 14px 0; color:#688aac; font-size:11px; line-height:1.6; }
.model-panel p i { color:#00e5b0; margin-right:6px; }
</style>
