import { computed, ref, watch } from 'vue'
import {
  backendMode, getCatalogEntities, getEvents, getHistoryForecast, getHolidayComparison,
  getHolidays, getOverview, getScenicSeries, getTransportSeries, getNativeSeries,
} from '../../app/data'
import { displayError } from '../../app/format'
import type {
  Entity, EventSummary, ForecastResult, HolidayCompareResult, Overview, OverviewCard,
  SeriesResult, TrendGrain,
} from '../../app/types'

type EntityType = Entity['type']
type MapEntity = Overview['map_entities'][number]

export function useOverview() {
  const grain = ref<TrendGrain>('month')
  const loading = ref(false)
  const error = ref('')
  const overview = ref<Overview | null>(null)
  const catalogEntities = ref<Entity[]>([])
  const scenicId = ref('')
  const railId = ref('')
  const airportId = ref('')
  const series = ref<SeriesResult | null>(null)
  const railSeries = ref<SeriesResult | null>(null)
  const airportSeries = ref<SeriesResult | null>(null)
  const seriesError = ref('')
  const railError = ref('')
  const airportError = ref('')
  const forecast = ref<ForecastResult | null>(null)
  const forecastError = ref('')
  const events = ref<EventSummary[]>([])
  const holidayComparison = ref<HolidayCompareResult | null>(null)
  const holidayError = ref('')
  let requestId = 0

  const availableEntities = computed<Entity[]>(() =>
    catalogEntities.value.length ? catalogEntities.value : overview.value?.map_entities ?? [],
  )
  const scenicEntities = computed(() => availableEntities.value.filter(entity => entity.type === 'scenic'))
  const railEntities = computed(() => availableEntities.value.filter(entity => entity.type === 'rail_station'))
  const airportEntities = computed(() => availableEntities.value.filter(entity => entity.type === 'airport'))

  function selectedEntity(type: EntityType, id: string, metric: string): Entity | undefined {
    const entities = availableEntities.value.filter(entity => entity.type === type)
    const defaultId = overview.value?.cards.find(card => card.metric === metric)?.entity_id
    return entities.find(entity => entity.id === id)
      ?? entities.find(entity => entity.id === defaultId)
      ?? entities[0]
  }

  const selectedScenic = computed(() => selectedEntity('scenic', scenicId.value, 'scenic_visits'))
  const selectedRail = computed(() => selectedEntity('rail_station', railId.value, 'rail_departures'))
  const selectedAirport = computed(() => selectedEntity('airport', airportId.value, 'airport_throughput'))
  const selectedScenicId = computed(() => selectedScenic.value?.id ?? '')
  const selectedRailId = computed(() => selectedRail.value?.id ?? '')
  const selectedAirportId = computed(() => selectedAirport.value?.id ?? '')

  function cardFor(metric: string, entity: Entity | undefined, result: SeriesResult | null, label: string): OverviewCard | null {
    const base = overview.value?.cards.find(card => card.metric === metric)
    if (!base) return null
    if (!entity) return base
    const latest = result?.entity.id === entity.id ? result.points.at(-1) : undefined
    const fallback = base.entity_id === entity.id ? base : { value: null, source_ids: [], status: 'no_data' }
    return { ...base, ...fallback, ...latest, entity_id: entity.id, label: `${entity.name} · ${label}`, status: latest?.quality_status ?? fallback.status }
  }

  const selectedCards = computed(() => [
    cardFor('scenic_visits', selectedScenic.value, series.value, '接待人次'),
    cardFor('rail_departures', selectedRail.value, railSeries.value, '发送人次'),
    cardFor('airport_throughput', selectedAirport.value, airportSeries.value, '旅客吞吐量'),
  ].filter((card): card is OverviewCard => card !== null))

  const mapEntities = computed<MapEntity[]>(() => {
    const values = [...(overview.value?.map_entities ?? [])]
    for (const [entity, result] of [
      [selectedScenic.value, series.value],
      [selectedRail.value, railSeries.value],
      [selectedAirport.value, airportSeries.value],
    ] as const) {
      if (!entity) continue
      const latest = result?.entity.id === entity.id ? result.points.at(-1) : undefined
      const index = values.findIndex(item => item.id === entity.id)
      if (index >= 0) {
        if (latest) values[index] = { ...values[index], latest_value: latest.value, latest_metric: latest.metric }
      } else {
        values.push({ ...entity, latest_value: latest?.value ?? null, latest_metric: entity.available_metrics[0] })
      }
    }
    return values
  })

  function rangeFor(metric: string, result: Overview, fallback: string): { start: string; end: string } {
    const end = result.data_freshness.find(item => item.metric === metric)?.latest_period_end
      ?? result.cards.find(card => card.metric === metric)?.period_end
      ?? fallback
    const startDate = new Date(`${end}T00:00:00Z`)
    startDate.setUTCFullYear(startDate.getUTCFullYear() - 1)
    return { start: startDate.toISOString().slice(0, 10), end }
  }

  function transportGrain(entity: Entity): TrendGrain {
    return entity.available_grains.includes(grain.value) ? grain.value : 'month'
  }

  async function load(refreshBase: boolean) {
    const current = ++requestId
    loading.value = true
    error.value = ''
    try {
      const nextOverview = refreshBase || !overview.value ? await getOverview(grain.value) : overview.value
      if (current !== requestId) return
      // 地图先展示已取得的总览数据；切换对象只更新图层，不重建地图。
      overview.value = nextOverview
      const [entities, nextEvents] = refreshBase || !catalogEntities.value.length
        ? await Promise.all([getCatalogEntities(), getEvents()])
        : [catalogEntities.value, events.value]
      if (current !== requestId) return
      catalogEntities.value = entities
      events.value = nextEvents

      const scenic = selectedEntity('scenic', scenicId.value, 'scenic_visits')
      const rail = selectedEntity('rail_station', railId.value, 'rail_departures')
      const airport = selectedEntity('airport', airportId.value, 'airport_throughput')
      const scenicRange = rangeFor('scenic_visits', nextOverview, new Date().toISOString().slice(0, 10))
      const railRange = rangeFor('rail_departures', nextOverview, scenicRange.end)
      const airportRange = rangeFor('airport_throughput', nextOverview, scenicRange.end)
      const scenicChanged = refreshBase || !series.value || series.value.entity.id !== scenic?.id || series.value.grain !== grain.value
      const railGrain = rail ? transportGrain(rail) : grain.value
      const airportGrain = airport ? transportGrain(airport) : grain.value
      const railChanged = refreshBase || !railSeries.value || railSeries.value.entity.id !== rail?.id || railSeries.value.grain !== railGrain
      const airportChanged = refreshBase || !airportSeries.value || airportSeries.value.entity.id !== airport?.id || airportSeries.value.grain !== airportGrain
      if (scenicChanged) {
        seriesError.value = ''
        forecastError.value = ''
        forecast.value = null
        holidayComparison.value = null
        holidayError.value = ''
      }
      if (railChanged) railError.value = ''
      if (airportChanged) airportError.value = ''
      const responses = await Promise.allSettled([
        scenicChanged && scenic ? backendMode.value === 'live' ? getNativeSeries(scenic.id, 'scenic_visits', 'auto', grain.value) : getScenicSeries(scenic.id, grain.value, scenicRange.start, scenicRange.end) : Promise.resolve(scenic ? series.value : null),
        railChanged && rail ? backendMode.value === 'live' ? getNativeSeries(rail.id, 'rail_departures', 'auto', grain.value) : getTransportSeries(rail.id, 'rail_departures', railGrain, railRange.start, railRange.end) : Promise.resolve(rail ? railSeries.value : null),
        airportChanged && airport ? backendMode.value === 'live' ? getNativeSeries(airport.id, 'airport_throughput', 'auto', grain.value) : getTransportSeries(airport.id, 'airport_throughput', airportGrain, airportRange.start, airportRange.end) : Promise.resolve(airport ? airportSeries.value : null),
      ])
      if (current !== requestId) return
      series.value = responses[0].status === 'fulfilled' ? responses[0].value : null
      railSeries.value = responses[1].status === 'fulfilled' ? responses[1].value : null
      airportSeries.value = responses[2].status === 'fulfilled' ? responses[2].value : null
      if (responses[0].status === 'rejected') seriesError.value = displayError(responses[0].reason)
      if (responses[1].status === 'rejected') railError.value = displayError(responses[1].reason)
      if (responses[2].status === 'rejected') airportError.value = displayError(responses[2].reason)

      if (scenicChanged) {
        const lastPoint = series.value?.points.at(-1)
        const forecastTask = scenic && lastPoint && ['week', 'month'].includes(series.value?.grain ?? '') ? getHistoryForecast(scenic.id, grain.value, lastPoint.period_end, grain.value === 'week' ? 4 : 3)
          .then(result => { if (current === requestId) forecast.value = result })
          .catch(cause => { if (current === requestId) forecastError.value = displayError(cause) }) : Promise.resolve()
        const holidayTask = scenic ? getHolidays()
          .then(async items => {
            const pair = items.filter(item => item.kind === 'national_day').sort((a, b) => a.year - b.year).slice(-2)
            if (pair.length !== 2) {
              if (current === requestId) holidayError.value = '暂无两个可比较的国庆假期。'
              return
            }
            const comparison = await getHolidayComparison(pair.map(item => item.id), scenic.id, 'daily_average')
            if (current === requestId) holidayComparison.value = comparison
          })
          .catch(cause => { if (current === requestId) holidayError.value = displayError(cause) }) : Promise.resolve()
        await Promise.all([forecastTask, holidayTask])
      }
    } catch (cause) {
      if (current === requestId) error.value = displayError(cause)
    } finally {
      if (current === requestId) loading.value = false
    }
  }

  function reload() { return load(true) }

  watch([grain, backendMode], () => { void load(true) }, { immediate: true })
  watch([scenicId, railId, airportId], () => { void load(false) })
  return {
    grain, scenicId, railId, airportId, selectedScenicId, selectedRailId, selectedAirportId,
    scenicEntities, railEntities, airportEntities, selectedCards, mapEntities,
    loading, error, overview, series, railSeries, airportSeries, seriesError, railError, airportError,
    forecast, forecastError, events, holidayComparison, holidayError, reload,
  }
}
