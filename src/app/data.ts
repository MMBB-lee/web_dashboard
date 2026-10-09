import { ref } from 'vue'
import { demoEntities, demoEvents, demoForecast, demoHolidayCompare, demoHolidays, demoMetrics, demoOverview, demoSeries } from './demo'
import { ApiError } from './types'
import type {
  ApiEnvelope, BackendMode, Entity, EventSummary, ForecastResult, Holiday,
  HolidayCompareResult, MetricDefinition, Overview, SeriesResult, Source, TrendGrain,
} from './types'

const API = '/api/v1'
const CHECK_INTERVAL_MS = 12_000
export const backendMode = ref<BackendMode>('checking')
export const csrfToken = ref<string | null>(null)
let checkedAt = 0
let checkInFlight: Promise<BackendMode> | null = null

function timedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 5000)
  return fetch(`${API}${path}`, { credentials: 'include', ...init, signal: controller.signal })
    .finally(() => window.clearTimeout(timeout))
}

export function refreshBackendStatus(force = false): Promise<BackendMode> {
  if (!force && checkedAt && Date.now() - checkedAt < CHECK_INTERVAL_MS) return Promise.resolve(backendMode.value)
  if (checkInFlight) return checkInFlight
  checkInFlight = (async () => {
    try {
      const health = await timedFetch('/health/live')
      if (!health.ok) throw new Error('API unavailable')
      const session = await timedFetch('/auth/me')
      if (session.status === 401) {
        csrfToken.value = null
        backendMode.value = 'auth'
      } else if (session.ok) {
        const payload = await session.json() as ApiEnvelope<{ csrf_token?: string }>
        csrfToken.value = payload.data?.csrf_token ?? null
        backendMode.value = 'live'
      } else {
        backendMode.value = 'live'
      }
    } catch {
      csrfToken.value = null
      backendMode.value = 'demo'
    }
    checkedAt = Date.now()
    return backendMode.value
  })().finally(() => { checkInFlight = null })
  return checkInFlight
}

async function request<T>(path: string, demo: () => T, init?: RequestInit): Promise<T> {
  const mode = await refreshBackendStatus()
  if (mode === 'demo') return demo()
  if (mode === 'auth') throw new ApiError(401, 'unauthorized', '请先登录后查看业务数据。')
  const headers = new Headers(init?.headers)
  if (init?.body) headers.set('Content-Type', 'application/json')
  if (init?.method && init.method !== 'GET' && csrfToken.value) headers.set('X-CSRF-Token', csrfToken.value)
  let response: Response
  try {
    response = await timedFetch(path, { ...init, headers })
  } catch (error) {
    if (await refreshBackendStatus(true) === 'demo') return demo()
    throw error
  }
  if (response.status >= 500 && await refreshBackendStatus(true) === 'demo') return demo()
  if (response.status === 401) {
    backendMode.value = 'auth'
    csrfToken.value = null
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null
    throw new ApiError(response.status, payload?.error?.code ?? 'request_failed', payload?.error?.message ?? `请求失败（${response.status}）`)
  }
  const payload = await response.json() as ApiEnvelope<T>
  return payload.data
}

function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined) params.set(key, String(value))
  return params.toString()
}

function asList<T>(value: T[] | { items?: T[]; results?: T[] }): T[] {
  return Array.isArray(value) ? value : value.items ?? value.results ?? []
}

export async function getCatalogEntities(type?: Entity['type']): Promise<Entity[]> {
  const value = await request<Entity[] | { items?: Entity[] }>(`/catalog/entities?${query({ type, page_size: 100 })}`,
    () => demoEntities.filter((entity) => !type || entity.type === type))
  return asList(value)
}

export async function getMetricDefinitions(entityType?: string): Promise<MetricDefinition[]> {
  const value = await request<MetricDefinition[] | { items?: MetricDefinition[] }>(`/catalog/metrics?${query({ entity_type: entityType })}`,
    () => demoMetrics.filter((metric) => !entityType || metric.supported_entity_types.includes(entityType)))
  return asList(value)
}

export function getSource(sourceId: string): Promise<Source> {
  return request(`/sources/${encodeURIComponent(sourceId)}`, () => { throw new ApiError(404, 'demo_source', '演示数据没有官方来源。') })
}

export function getOverview(grain: TrendGrain, regionCode = '340000'): Promise<Overview> {
  return request(`/dashboard/overview?${query({ region_code: regionCode, grain })}`, () => demoOverview(grain))
}

export function getScenicSeries(entityId: string, grain: TrendGrain, start: string, end: string): Promise<SeriesResult> {
  return request(`/scenic/${encodeURIComponent(entityId)}/series?${query({ metric: 'scenic_visits', grain, start, end, quality: 'verified' })}`,
    () => demoSeries(entityId, grain, start, end))
}

export function getHistoryForecast(entityId: string, grain: TrendGrain, trainingEnd: string, horizon: number): Promise<ForecastResult> {
  const body = { entity_id: entityId, metric: 'scenic_visits', grain, training_end: trainingEnd, horizon, model: 'baseline' }
  return request('/forecasts/history', () => demoForecast(entityId, grain, trainingEnd, horizon), { method: 'POST', body: JSON.stringify(body) })
}

export async function getHolidays(year?: number, kind?: Holiday['kind']): Promise<Holiday[]> {
  const value = await request<Holiday[] | { items?: Holiday[] }>(`/holidays?${query({ year, kind, region_code: '340000' })}`,
    () => demoHolidays.filter((holiday) => (!year || holiday.year === year) && (!kind || holiday.kind === kind)))
  return asList(value)
}

export async function getHolidayComparison(holidayIds: string[], entityId: string, mode: 'daily_average' | 'period_total' | 'day_index'): Promise<HolidayCompareResult> {
  const body = { holiday_ids: holidayIds, series: [{ entity_id: entityId, metric: 'scenic_visits' }], mode }
  const value = await request<HolidayCompareResult & { results?: HolidayCompareResult['items']; comparisons?: HolidayCompareResult['items'] }>(
    '/holidays/compare', () => demoHolidayCompare(holidayIds, entityId, mode), { method: 'POST', body: JSON.stringify(body) })
  return { comparable: value.comparable, reason: value.reason ?? null, items: value.items ?? value.results ?? value.comparisons ?? [] }
}

export async function getEvents(regionCode = '340000'): Promise<EventSummary[]> {
  const value = await request<EventSummary[] | { items?: EventSummary[] }>(`/events?${query({ region_code: regionCode, status: 'active', page_size: 5 })}`,
    () => demoEvents)
  return asList(value)
}

// D 的分析接口已预留，A 后续跨对象比较时可直接调用。
export function compareSeries(body: { series: Array<{ entity_id: string; metric: string }>; grain: TrendGrain; start: string; end: string; display_mode: 'separate_axes' | 'index_100'; base_period?: string }): Promise<unknown> {
  return request('/analytics/compare', () => ({ series: [], comparison_note: '演示模式暂无跨指标比较数据。' }), { method: 'POST', body: JSON.stringify(body) })
}
