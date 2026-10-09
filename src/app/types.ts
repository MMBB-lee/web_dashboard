export type Grain = 'day' | 'week' | 'month' | 'holiday'
export type TrendGrain = 'week' | 'month'
export type MetricCode = 'scenic_visits' | 'rail_departures' | 'airport_throughput'
export type BackendMode = 'checking' | 'demo' | 'live' | 'auth' | 'offline'

export interface NumericPoint {
  metric: string
  value: number | null
  unit: string
  period_start: string
  period_end: string
  grain: Grain
  source_ids: string[]
  published_at: string | null
  ingested_at: string | null
  quality_status: string
  is_forecast?: boolean
  lower_bound?: number | null
  upper_bound?: number | null
  model_version?: string
}

export interface Entity {
  id: string
  name: string
  type: 'scenic' | 'rail_station' | 'airport'
  region_code: string
  longitude: number | null
  latitude: number | null
  available_metrics: string[]
  available_grains: Grain[]
  status: string
}

export interface MetricDefinition {
  code: string
  display_name: string
  definition: string
  unit: string
  supported_entity_types: string[]
  aggregation_rule: string
  comparability_note: string
}

export interface Source {
  id: string
  publisher: string
  title: string
  url: string
  published_at: string
  retrieved_at: string
  coverage_start: string
  coverage_end: string
  native_grain: Grain
  usage_note: string
  verification_status: string
}

export interface OverviewCard extends NumericPoint {
  label?: string
  entity_id?: string
  status?: string
}

export interface Overview {
  cards: OverviewCard[]
  map_entities: Array<Entity & { latest_value?: number | null; latest_metric?: string }>
  active_events_count: number
  data_freshness: Array<{ metric: string; latest_period_end: string; ingested_at: string | null }>
}

export interface SeriesResult {
  entity: Entity
  metric: string
  grain: Grain
  points: NumericPoint[]
  missing_periods: Array<{ start: string; end: string }>
  coverage_ratio: number
}

export interface ForecastResult {
  status: 'ready' | 'data_insufficient'
  reason?: string
  forecast_points: NumericPoint[]
  backtest?: { method: string; mae: number | null; smape: number | null; sample_count: number }
  training_window?: { start: string; end: string }
  model_version?: string
  source_ids?: string[]
}

export interface Holiday {
  id: string
  name: string
  kind: 'may_day' | 'national_day' | 'summer' | 'winter'
  year: number
  start: string
  end: string
  definition_source_id: string | null
  days_count: number
}

export interface HolidayCompareItem {
  holiday_id: string
  label: string
  metric?: string
  value: number | null
  unit: string
  period_start?: string
  period_end?: string
  grain?: Grain
  missing_days: number
  source_ids: string[]
  published_at?: string | null
  ingested_at?: string | null
  quality_status?: string
}

export interface HolidayCompareResult {
  comparable: boolean
  reason: string | null
  items: HolidayCompareItem[]
}

export interface EventSummary {
  id: string
  title: string
  kind: string
  status: 'active' | 'resolved'
  authority: 'official' | 'model_signal'
  severity: string
  affected_entity_ids: string[]
  starts_at: string
  ends_at: string | null
  published_at: string
  last_verified_at: string
  source_id: string | null
}

export interface ApiEnvelope<T> {
  data: T
  meta?: { request_id: string; page?: number; page_size?: number; total?: number }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}
