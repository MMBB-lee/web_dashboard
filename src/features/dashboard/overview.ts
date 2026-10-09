import { ref, watch } from 'vue'
import { backendMode, getCatalogEntities, getEvents, getHistoryForecast, getOverview, getScenicSeries } from '../../app/data'
import { displayError } from '../../app/format'
import type { EventSummary, ForecastResult, Overview, SeriesResult, TrendGrain } from '../../app/types'

export function useOverview() {
  const grain = ref<TrendGrain>('month')
  const loading = ref(false)
  const error = ref('')
  const overview = ref<Overview | null>(null)
  const series = ref<SeriesResult | null>(null)
  const forecast = ref<ForecastResult | null>(null)
  const events = ref<EventSummary[]>([])
  let requestId = 0

  async function reload() {
    const current = ++requestId
    loading.value = true
    error.value = ''
    try {
      const [nextOverview, scenicEntities, nextEvents] = await Promise.all([
        getOverview(grain.value), getCatalogEntities('scenic'), getEvents(),
      ])
      const scenic = scenicEntities[0]
      const nextSeries = scenic ? await getScenicSeries(scenic.id, grain.value, '2025-10-01', '2026-10-31') : null
      const lastPoint = nextSeries?.points.at(-1)
      const nextForecast = scenic && lastPoint
        ? await getHistoryForecast(scenic.id, grain.value, lastPoint.period_end, grain.value === 'week' ? 4 : 3)
        : null
      if (current !== requestId) return
      overview.value = nextOverview
      series.value = nextSeries
      forecast.value = nextForecast
      events.value = nextEvents
    } catch (cause) {
      if (current === requestId) error.value = displayError(cause)
    } finally {
      if (current === requestId) loading.value = false
    }
  }

  watch([grain, backendMode], reload, { immediate: true })
  return { grain, loading, error, overview, series, forecast, events, reload }
}
