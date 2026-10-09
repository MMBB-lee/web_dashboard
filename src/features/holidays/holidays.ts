import { computed, ref, watch } from 'vue'
import { backendMode, getCatalogEntities, getHolidayComparison, getHolidays, getMetricDefinitions } from '../../app/data'
import { displayError } from '../../app/format'
import type { Entity, Holiday, HolidayCompareResult, MetricDefinition } from '../../app/types'

export type HolidayMode = 'daily_average' | 'period_total' | 'day_index'

export function useHolidays() {
  const kind = ref<Holiday['kind']>('national_day')
  const firstId = ref('')
  const secondId = ref('')
  const entityId = ref('')
  const mode = ref<HolidayMode>('daily_average')
  const holidays = ref<Holiday[]>([])
  const entities = ref<Entity[]>([])
  const metric = ref<MetricDefinition | null>(null)
  const comparison = ref<HolidayCompareResult | null>(null)
  const loading = ref(false)
  const error = ref('')
  let catalogRequest = 0
  let compareRequest = 0

  const firstHoliday = computed(() => holidays.value.find((item) => item.id === firstId.value) ?? null)
  const secondHoliday = computed(() => holidays.value.find((item) => item.id === secondId.value) ?? null)
  const entity = computed(() => entities.value.find((item) => item.id === entityId.value) ?? null)

  async function loadCatalog() {
    const current = ++catalogRequest
    loading.value = true
    error.value = ''
    try {
      const [nextHolidays, nextEntities, metrics] = await Promise.all([
        getHolidays(undefined, kind.value), getCatalogEntities('scenic'), getMetricDefinitions('scenic'),
      ])
      if (current !== catalogRequest) return
      holidays.value = nextHolidays.sort((a, b) => a.year - b.year)
      entities.value = nextEntities
      metric.value = metrics.find((item) => item.code === 'scenic_visits') ?? null
      if (!holidays.value.some((item) => item.id === firstId.value)) firstId.value = holidays.value[0]?.id ?? ''
      if (!holidays.value.some((item) => item.id === secondId.value) || secondId.value === firstId.value) secondId.value = holidays.value.at(-1)?.id ?? ''
      if (!entities.value.some((item) => item.id === entityId.value)) entityId.value = entities.value[0]?.id ?? ''
    } catch (cause) {
      if (current === catalogRequest) error.value = displayError(cause)
    } finally {
      if (current === catalogRequest) loading.value = false
    }
  }

  async function loadComparison() {
    const current = ++compareRequest
    if (!firstId.value || !secondId.value || !entityId.value) { comparison.value = null; return }
    if (firstId.value === secondId.value) { comparison.value = null; error.value = '请选择两个不同的假期。'; return }
    loading.value = true
    error.value = ''
    try {
      const next = await getHolidayComparison([firstId.value, secondId.value], entityId.value, mode.value)
      if (current === compareRequest) comparison.value = next
    } catch (cause) {
      if (current === compareRequest) error.value = displayError(cause)
    } finally {
      if (current === compareRequest) loading.value = false
    }
  }

  watch([kind, backendMode], loadCatalog, { immediate: true })
  watch([firstId, secondId, entityId, mode, backendMode], loadComparison)
  return { kind, firstId, secondId, entityId, mode, holidays, entities, metric, comparison, loading, error, firstHoliday, secondHoliday, entity, loadComparison }
}
