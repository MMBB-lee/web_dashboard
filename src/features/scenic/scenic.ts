import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { backendMode, getCatalogEntities, getHistoryForecast, getMetricDefinitions, getScenicSeries, getSource } from '../../app/data'
import { displayError } from '../../app/format'
import type { Entity, ForecastResult, MetricDefinition, SeriesResult, Source, TrendGrain } from '../../app/types'

export function useScenic() {
  const route = useRoute()
  const entityId = ref(typeof route.query.id === 'string' ? route.query.id : '')
  const grain = ref<TrendGrain>('month')
  const start = ref('2025-10-01')
  const end = ref('2026-10-31')
  const horizon = ref(3)
  const entities = ref<Entity[]>([])
  const metric = ref<MetricDefinition | null>(null)
  const source = ref<Source | null>(null)
  const series = ref<SeriesResult | null>(null)
  const forecast = ref<ForecastResult | null>(null)
  const loading = ref(false)
  const error = ref('')
  let requestId = 0

  const selectedEntity = computed(() => entities.value.find((entity) => entity.id === entityId.value) ?? null)

  async function reload() {
    const current = ++requestId
    loading.value = true
    error.value = ''
    try {
      const [nextEntities, definitions] = await Promise.all([getCatalogEntities('scenic'), getMetricDefinitions('scenic')])
      const selected = nextEntities.find((entity) => entity.id === entityId.value) ?? nextEntities[0]
      if (!selected) {
        if (current === requestId) { entities.value = []; series.value = null; forecast.value = null }
        return
      }
      if (start.value > end.value) throw new Error('开始日期不能晚于结束日期。')
      if (!selected.available_grains.includes(grain.value)) throw new Error(`${selected.name} 暂不支持按${grain.value === 'week' ? '周' : '月'}查看。`)
      const nextSeries = await getScenicSeries(selected.id, grain.value, start.value, end.value)
      const last = nextSeries.points.at(-1)
      const nextForecast = last
        ? await getHistoryForecast(selected.id, grain.value, last.period_end, horizon.value)
        : { status: 'data_insufficient', reason: '当前时间范围内没有可用于预测的实际数据。', forecast_points: [] } as ForecastResult
      const sourceId = last?.source_ids[0]
      const nextSource = sourceId ? await getSource(sourceId).catch(() => null) : null
      if (current !== requestId) return
      entities.value = nextEntities
      entityId.value = selected.id
      metric.value = definitions.find((item) => item.code === 'scenic_visits') ?? null
      series.value = nextSeries
      forecast.value = nextForecast
      source.value = nextSource
    } catch (cause) {
      if (current === requestId) error.value = displayError(cause)
    } finally {
      if (current === requestId) loading.value = false
    }
  }

  watch(() => route.query.id, (id) => { if (typeof id === 'string') entityId.value = id })
  watch([entityId, grain, start, end, horizon, backendMode], reload, { immediate: true })
  return { entityId, grain, start, end, horizon, entities, selectedEntity, metric, source, series, forecast, loading, error, reload }
}
