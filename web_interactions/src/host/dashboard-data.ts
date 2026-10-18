import { computed } from 'vue'
import { interactions, dataMode } from './runtime'
import type {
  ApiEnvelope, BackendMode, Entity, EventSummary, ForecastResult, Holiday,
  HolidayCompareResult, MetricDefinition, Overview, SeriesResult, Source, TrendGrain,
} from '../../../web_dashboard/src/app/types'

export const interactionsAvailable = true
export const backendMode = computed<BackendMode>(() => dataMode === 'demo' ? 'demo' : interactions.auth.state.session ? 'live' : 'auth')
export const csrfToken = computed(() => interactions.auth.state.session?.csrf_token ?? null)
export async function refreshBackendStatus(_force = false): Promise<BackendMode> {
  try { await interactions.auth.checkSession() } catch { /* 登录页显示连接失败，模式保持不变。 */ }
  return backendMode.value
}
// 接口统一经过 B 的会话客户端；错误按接口状态展示，不回退样例。
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await interactions.client.request<T>(path, init?.method ?? 'GET', init?.body ? JSON.parse(String(init.body)) : undefined)).data
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
  const value = await request<Entity[] | { items?: Entity[] }>(`/catalog/entities?${query({ type, page_size: 100 })}`)
  return asList(value)
}

export async function getMetricDefinitions(entityType?: string): Promise<MetricDefinition[]> {
  const value = await request<MetricDefinition[] | { items?: MetricDefinition[] }>(`/catalog/metrics?${query({ entity_type: entityType })}`)
  return asList(value)
}

export function getSource(sourceId: string): Promise<Source> {
  return request(`/sources/${encodeURIComponent(sourceId)}`)
}

export function getOverview(grain: TrendGrain, regionCode = '340000'): Promise<Overview> {
  return request(`/dashboard/overview?${query({ region_code: regionCode, grain })}`)
}

export function getScenicSeries(entityId: string, grain: TrendGrain, start: string, end: string): Promise<SeriesResult> {
  return request(`/scenic/${encodeURIComponent(entityId)}/series?${query({ metric: 'scenic_visits', grain, start, end, quality: 'verified' })}`)
}

export function getTransportSeries(entityId: string, metric: 'rail_departures' | 'airport_throughput', grain: TrendGrain, start: string, end: string): Promise<SeriesResult> {
  return request(`/transport/${encodeURIComponent(entityId)}/series?${query({ metric, grain, start, end, quality: 'verified' })}`)
}

export function getHistoryForecast(entityId: string, grain: TrendGrain, trainingEnd: string, horizon: number): Promise<ForecastResult> {
  const body = { entity_id: entityId, metric: 'scenic_visits', grain, training_end: trainingEnd, horizon, model: 'baseline' }
  return request('/forecasts/history', { method: 'POST', body: JSON.stringify(body) })
}

export async function getHolidays(year?: number, kind?: Holiday['kind']): Promise<Holiday[]> {
  const value = await request<Holiday[] | { items?: Holiday[] }>(`/holidays?${query({ year, kind, region_code: '340000' })}`)
  return asList(value)
}

export async function getHolidayComparison(holidayIds: string[], entityId: string, mode: 'daily_average' | 'period_total' | 'day_index'): Promise<HolidayCompareResult> {
  return request(`/holidays/statistics?${query({ holiday_ids: holidayIds.join(','), entity_id: entityId, mode })}`)
}

export async function getEvents(regionCode = '340000'): Promise<EventSummary[]> {
  const value = await request<EventSummary[] | { items?: EventSummary[] }>(`/events?${query({ region_code: regionCode, status: 'active', page_size: 5 })}`)
  return asList(value)
}

// D 的分析接口已预留，A 后续跨对象比较时可直接调用。
export function compareSeries(body: { series: Array<{ entity_id: string; metric: string }>; grain: TrendGrain; start: string; end: string; display_mode: 'separate_axes' | 'index_100'; base_period?: string }): Promise<unknown> {
  return request('/analytics/compare', { method: 'POST', body: JSON.stringify(body) })
}

export function getNativeSeries(entityId: string, metric: string, grain = 'auto', preferredGrain = 'month', start?: string, end?: string): Promise<SeriesResult> {
  return request(`/public-data/${encodeURIComponent(entityId)}/series?${query({ metric, grain, preferred_grain: preferredGrain, start, end })}`)
}
