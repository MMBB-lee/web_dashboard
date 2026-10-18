import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { backendMode, getCatalogEntities, getHistoryForecast, getMetricDefinitions, getScenicSeries, getSource, getNativeSeries } from '../../app/data'
import { displayError } from '../../app/format'
import { isPeriodInput, localDateString, periodEnd, periodInputValue, periodStart } from '../../app/period'
import type { Entity, ForecastResult, MetricDefinition, SeriesResult, Source, TrendGrain, Grain } from '../../app/types'

export function useScenic() {
  const route = useRoute()
  const entityId = ref(typeof route.query.id === 'string' ? route.query.id : '')
  const grain = ref<Grain>('month'), today = ref(localDateString())
  const start = ref(`${Number(today.value.slice(0, 4)) - 1}-${today.value.slice(5, 7)}-01`), end = ref(today.value)
  const horizon = ref(3), entities = ref<Entity[]>([])
  const metric = ref<MetricDefinition | null>(null), source = ref<Source | null>(null)
  const series = ref<SeriesResult | null>(null), forecast = ref<ForecastResult | null>(null)
  const loading = ref(false), error = ref('')
  let requestId = 0, fittedEntity = ''
  const selectedEntity = computed(() => entities.value.find(e => e.id === entityId.value) ?? null)
  const standardPeriod = computed(() => grain.value === 'week' || grain.value === 'month')
  const maxPeriod = computed(() => standardPeriod.value ? periodInputValue(today.value, grain.value as TrendGrain) : today.value)
  function inputValue(value: string) { return standardPeriod.value ? periodInputValue(value, grain.value as TrendGrain) : value }
  function updatePeriod(value: string, isStart: boolean) {
    if (standardPeriod.value && !isPeriodInput(value, grain.value as TrendGrain)) return
    if (!standardPeriod.value && !/^\d{4}-\d{2}-\d{2}$/.test(value)) return
    const chosen = value > maxPeriod.value ? maxPeriod.value : value
    if (isStart) start.value = standardPeriod.value ? periodStart(chosen, grain.value as TrendGrain) : chosen
    else end.value = standardPeriod.value ? periodEnd(chosen, grain.value as TrendGrain, today.value) : chosen
  }
  const startPeriod = computed({ get: () => inputValue(start.value), set: (v: string) => updatePeriod(v, true) })
  const endPeriod = computed({ get: () => inputValue(end.value), set: (v: string) => updatePeriod(v, false) })
  function refreshToday() { today.value = localDateString() }
  function setGrain(value: string) { grain.value = value as Grain; fittedEntity = '' }
  async function reload() {
    const current = ++requestId; loading.value = true; error.value = ''
    try {
      const [nextEntities, definitions] = await Promise.all([getCatalogEntities('scenic'), getMetricDefinitions('scenic')])
      if (current !== requestId) return
      entities.value = nextEntities
      const selected = nextEntities.find(e => e.id === entityId.value) ?? nextEntities[0]
      if (!selected) { series.value = null; forecast.value = null; return }
      if (entityId.value !== selected.id) { entityId.value = selected.id; return }
      const fit = backendMode.value === 'live' && fittedEntity !== selected.id
      let result: SeriesResult
      if (backendMode.value === 'live') {
        result = await getNativeSeries(selected.id, 'scenic_visits', fit ? 'auto' : grain.value, grain.value,
          fit ? undefined : standardPeriod.value ? periodStart(startPeriod.value, grain.value as TrendGrain) : start.value,
          fit ? undefined : standardPeriod.value ? periodEnd(endPeriod.value, grain.value as TrendGrain, today.value) : end.value)
      } else {
        result = await getScenicSeries(selected.id, grain.value as TrendGrain,
          periodStart(startPeriod.value, grain.value as TrendGrain), periodEnd(endPeriod.value, grain.value as TrendGrain, today.value))
      }
      if (current !== requestId) return
      fittedEntity = selected.id
      if (fit && result.points.length) {
        grain.value = result.grain
        start.value = result.points[0]!.period_start; end.value = result.points.at(-1)!.period_end
      }
      series.value = result
      metric.value = definitions.find(item => item.code === 'scenic_visits') ?? null
      const last = result.points.at(-1)
      source.value = last?.source_ids[0] ? await getSource(last.source_ids[0]).catch(() => null) : null
      if (last && (grain.value === 'week' || grain.value === 'month')) {
        forecast.value = await getHistoryForecast(selected.id, grain.value, last.period_end, horizon.value).catch(cause => ({ status: 'data_insufficient', reason: displayError(cause), forecast_points: [] } as ForecastResult))
      } else {
        forecast.value = { status: 'data_insufficient', reason: '当前为原始统计周期，历史预测仅支持完整周/月序列。', forecast_points: [] }
      }
    } catch (cause) { if (current === requestId) error.value = displayError(cause) }
    finally { if (current === requestId) loading.value = false }
  }
  watch(() => route.query.id, id => { if (typeof id === 'string') entityId.value = id })
  watch([entityId, grain, start, end, today, horizon, backendMode], reload, { immediate: true })
  return { entityId, grain, setGrain, startPeriod, endPeriod, maxPeriod, refreshToday, horizon, entities, selectedEntity, metric, source, series, forecast, loading, error, reload }
}
