import { computed, ref, watch } from 'vue'
import { backendMode, getCatalogEntities, getEvents, getHistoryForecast, getHolidayComparison, getHolidays, getOverview, getScenicSeries } from '../../app/data'
import { displayError } from '../../app/format'
import type { Entity, EventSummary, ForecastResult, HolidayCompareResult, Overview, SeriesResult, TrendGrain } from '../../app/types'

export function useOverview() {
  const grain = ref<TrendGrain>('month')
  const loading = ref(false)
  const error = ref('')
  const overview = ref<Overview | null>(null)
  const scenicEntities = ref<Entity[]>([])
  const scenicId = ref('')
  const selectedScenicId = computed(() =>
    scenicEntities.value.find(entity => entity.id === scenicId.value)?.id ?? scenicEntities.value[0]?.id ?? '',
  )
  const series = ref<SeriesResult | null>(null)
  const forecast = ref<ForecastResult | null>(null)
  const events = ref<EventSummary[]>([])
  const holidayComparison = ref<HolidayCompareResult | null>(null)
  const holidayError = ref('')
  let requestId = 0

  async function reload() {
    const current = ++requestId
    loading.value = true
    error.value = ''
    holidayComparison.value = null
    holidayError.value = ''
    try {
      const nextOverview = await getOverview(grain.value)
      if (current !== requestId) return
      // 地图只依赖总览数据，先挂载地图，再继续读取趋势、预测和事件。
      overview.value = nextOverview
      const [catalogScenicEntities, nextEvents] = await Promise.all([
        getCatalogEntities('scenic'), getEvents(),
      ])
      const scenic = catalogScenicEntities.find(entity => entity.id === scenicId.value) ?? catalogScenicEntities[0]
      const latestScenicPeriod = nextOverview.cards.find(card => card.metric === 'scenic_visits')?.period_end ?? new Date().toISOString().slice(0, 10)
      const seriesStart = new Date(`${latestScenicPeriod}T00:00:00Z`)
      seriesStart.setUTCFullYear(seriesStart.getUTCFullYear() - 1)
      const nextSeries = scenic ? await getScenicSeries(scenic.id, grain.value, seriesStart.toISOString().slice(0, 10), latestScenicPeriod) : null
      const lastPoint = nextSeries?.points.at(-1)
      const nextForecast = scenic && lastPoint
        ? await getHistoryForecast(scenic.id, grain.value, lastPoint.period_end, grain.value === 'week' ? 4 : 3)
        : null
      if (current !== requestId) return
      scenicEntities.value = catalogScenicEntities
      series.value = nextSeries
      forecast.value = nextForecast
      events.value = nextEvents
      if (scenic) {
        try {
          const holidays = (await getHolidays()).filter(item => item.kind === 'national_day').sort((a, b) => a.year - b.year)
          const pair = holidays.slice(-2)
          if (pair.length === 2) {
            const comparison = await getHolidayComparison(pair.map(item => item.id), scenic.id, 'daily_average')
            if (current === requestId) holidayComparison.value = comparison
          } else if (current === requestId) holidayError.value = '暂无两个可比较的国庆假期。'
        } catch (cause) {
          if (current === requestId) holidayError.value = displayError(cause)
        }
      }
    } catch (cause) {
      if (current === requestId) error.value = displayError(cause)
    } finally {
      if (current === requestId) loading.value = false
    }
  }

  watch([grain, backendMode], reload, { immediate: true })
  watch(scenicId, reload)
  return { grain, scenicId, selectedScenicId, scenicEntities, loading, error, overview, series, forecast, events, holidayComparison, holidayError, reload }
}
