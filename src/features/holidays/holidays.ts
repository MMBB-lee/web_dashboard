import { computed, ref, watch } from 'vue'
import { backendMode, getCatalogEntities, getHolidayComparison, getHolidays, getMetricDefinitions } from '../../app/data'
import { displayError } from '../../app/format'
import type { Entity, Holiday, HolidayCompareItem, HolidayCompareResult, MetricDefinition } from '../../app/types'

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
      if (!entities.value.some((item) => item.id === entityId.value)) entityId.value = entities.value[0]?.id ?? ''
    } catch (cause) {
      if (current === catalogRequest) error.value = displayError(cause)
    } finally {
      if (current === catalogRequest) loading.value = false
    }
  }

  async function compareSelected(ids: string[], selectedMode: HolidayMode): Promise<HolidayCompareResult> {
    const [baseId, ...otherIds] = ids
    // The documented API accepts exactly two holiday IDs per request.
    const pairs = await Promise.all(otherIds.map(id => getHolidayComparison([baseId, id], entityId.value, selectedMode)))
    const findItem = (result: HolidayCompareResult, id: string): HolidayCompareItem | undefined => result.items.find(item => item.holiday_id === id)
    const items = [findItem(pairs[0], baseId), ...pairs.map((result, index) => findItem(result, otherIds[index]))]
    const completeItems = items.filter((item): item is HolidayCompareItem => Boolean(item))
    const complete = completeItems.length === ids.length
    const unit = items[0]?.unit
    const sameUnit = items.every(item => item?.unit === unit)
    const comparable = pairs.every(result => result.comparable) && complete && sameUnit && completeItems.every(item => item.value != null)
    const unequalDays = new Set(selectedHolidays.value.map(item => item.days_count)).size > 1
    return {
      comparable,
      reason: selectedMode === 'period_total' && unequalDays ? '所选假期天数不同，假期总量不宜直接比较；可选择日均值。' : pairs.find(result => !result.comparable)?.reason ?? (!complete ? '部分假期缺少可用数据。' : !sameUnit ? '所选假期的数值单位不同，不能放在同一图中比较。' : completeItems.some(item => item.value == null) ? '部分假期缺少可用数值。' : null),
      items: complete ? completeItems : [],
    }
  }

  async function loadComparison() {
    const current = ++compareRequest
    const ids = selectedHolidays.value.map(item => item.id)
    if (ids.length < 2 || ids.length > 6 || !entityId.value) {
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
  return { kind, selectedIds, entityId, mode, holidays, entities, metric, comparison, dailyAverageComparison, loading, error, selectedHolidays, loadComparison }
}
