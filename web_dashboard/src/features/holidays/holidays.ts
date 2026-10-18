import { computed, ref, watch } from 'vue'
import { backendMode, getCatalogEntities, getHolidayComparison, getHolidays, getMetricDefinitions, getSource } from '../../app/data'
import { displayError } from '../../app/format'
import type { Entity, Holiday, HolidayCompareResult, MetricDefinition, Source } from '../../app/types'

export type HolidayMode = 'daily_average' | 'period_total' | 'day_index'

export function useHolidays() {
  const kind = ref<Holiday['kind']>('national_day')
  const selectedIds = ref<string[]>([])
  const entityId = ref('')
  const mode = ref<HolidayMode>('daily_average')
  const holidays = ref<Holiday[]>([])
  const entities = ref<Entity[]>([])
  const metric = ref<MetricDefinition | null>(null)
  const comparison = ref<HolidayCompareResult | null>(null)
  const dailyAverageComparison = ref<HolidayCompareResult | null>(null)
  const loading = ref(false)
  const error = ref('')
  const availableIds = ref<string[]>([])
  const sources = ref<Record<string, Source>>({})
  const sourcesLoading = ref(false)
  const sourceIds = computed(() => [...new Set(comparison.value?.items.flatMap(item => item.source_ids) ?? [])])
  let sourcesRequest = 0
  let availabilityRequest = 0
  let catalogRequest = 0
  let compareRequest = 0
  const selectedHolidays = computed(() => selectedIds.value
    .map(id => holidays.value.find(item => item.id === id))
    .filter((item): item is Holiday => Boolean(item))
    .sort((a, b) => a.start.localeCompare(b.start)))

  async function loadCatalog() {
    const current = ++catalogRequest
    loading.value = true
    error.value = ''
    try {
      const [nextHolidays, nextEntities, metrics] = await Promise.all([
        getHolidays(), getCatalogEntities('scenic'), getMetricDefinitions('scenic'),
      ])
      if (current !== catalogRequest) return
      holidays.value = nextHolidays.sort((a, b) => a.year - b.year)
      entities.value = nextEntities
      metric.value = metrics.find((item) => item.code === 'scenic_visits') ?? null
      const validIds = selectedIds.value.filter(id => holidays.value.some(item => item.id === id))
      selectedIds.value = validIds.slice(0, 6)
      if (!entities.value.some((item) => item.id === entityId.value)) entityId.value = entities.value.find(item => item.available_grains.includes('holiday'))?.id ?? entities.value[0]?.id ?? ''
    } catch (cause) {
      if (current === catalogRequest) error.value = displayError(cause)
    } finally {
      if (current === catalogRequest) loading.value = false
    }
  }

  async function compareSelected(ids: string[], selectedMode: HolidayMode): Promise<HolidayCompareResult> {
    const result = await getHolidayComparison(ids, entityId.value, selectedMode)
    const unequalDays = new Set(selectedHolidays.value.map(item => item.days_count)).size > 1
    return {
      ...result,
      comparable: result.comparable && !(selectedMode === 'period_total' && unequalDays),
      reason: selectedMode === 'period_total' && unequalDays ? '所选假期天数不同，假期总量不宜直接比较；可选择日均值。' : result.reason,
    }
  }

  async function loadAvailable() {
    const current = ++availabilityRequest
    availableIds.value = []
    if (backendMode.value !== 'live' || !entityId.value || !holidays.value.length) return
    const requestedEntity = entityId.value
    try {
      const result = await getHolidayComparison(holidays.value.map(item => item.id), requestedEntity, 'daily_average')
      if (current !== availabilityRequest || requestedEntity !== entityId.value) return
      availableIds.value = result.items.filter(item => item.value != null).map(item => item.holiday_id)
      const candidate = [...holidays.value].reverse().find(item => availableIds.value.includes(item.id))
      if (candidate) {
        kind.value = candidate.kind
        selectedIds.value = [candidate.id]
      }
    } catch (cause) {
      if (current === availabilityRequest) error.value = displayError(cause)
    }
  }

  async function loadComparison() {
    const current = ++compareRequest
    const ids = selectedHolidays.value.map(item => item.id)
    if (ids.length < 1 || ids.length > 6 || !entityId.value) {
      comparison.value = null
      dailyAverageComparison.value = null
      loading.value = false
      error.value = ''
      return
    }
    loading.value = true
    error.value = ''
    comparison.value = null
    dailyAverageComparison.value = null
    try {
      const primaryRequest = compareSelected(ids, mode.value)
      const dailyRequest = mode.value === 'daily_average' ? primaryRequest : compareSelected(ids, 'daily_average')
      const [nextComparison, nextDailyAverage] = await Promise.all([primaryRequest, dailyRequest])
      if (current !== compareRequest) return
      comparison.value = nextComparison
      dailyAverageComparison.value = nextDailyAverage
    } catch (cause) {
      if (current === compareRequest) error.value = displayError(cause)
    } finally {
      if (current === compareRequest) loading.value = false
    }
  }

  watch(backendMode, loadCatalog, { immediate: true })
  watch([selectedIds, entityId, mode, backendMode], loadComparison)
  watch([entityId, holidays], loadAvailable)
  watch([sourceIds, backendMode], async () => {
    const current = ++sourcesRequest
    sourcesLoading.value = false
    if (backendMode.value !== 'live' || !sourceIds.value.length) return
    sourcesLoading.value = true
    try {
      const results = await Promise.allSettled(sourceIds.value.filter(id => !sources.value[id]).map(async id => [id, await getSource(id)] as const))
      if (current !== sourcesRequest) return
      for (const result of results) if (result.status === 'fulfilled') sources.value[result.value[0]] = result.value[1]
    } finally {
      if (current === sourcesRequest) sourcesLoading.value = false
    }
  })
  return { kind, selectedIds, entityId, mode, holidays, entities, metric, comparison, dailyAverageComparison, loading, error, selectedHolidays, availableIds, sourceIds, sources, sourcesLoading, loadComparison }
}
