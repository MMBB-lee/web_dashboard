import type {
  Entity, EventSummary, ForecastResult, Grain, Holiday, HolidayCompareResult,
  MetricDefinition, NumericPoint, Overview, SeriesResult, TrendGrain,
} from './types'

// 本文件中的所有数值均为静态样例，不代表官方统计或真实预测。
export const DEMO_SCENIC_IDS = {
  huangshan: '00000000-0000-4000-8000-000000000001',
  jiuhua: '00000000-0000-4000-8000-000000000002',
}

export const demoEntities: Entity[] = [
  { id: DEMO_SCENIC_IDS.huangshan, name: '黄山风景区', type: 'scenic', region_code: '341000', longitude: 118.17, latitude: 30.13, available_metrics: ['scenic_visits'], available_grains: ['week', 'month'], status: 'demo' },
  { id: DEMO_SCENIC_IDS.jiuhua, name: '九华山风景区', type: 'scenic', region_code: '341700', longitude: 117.82, latitude: 30.48, available_metrics: ['scenic_visits'], available_grains: ['week', 'month'], status: 'demo' },
  { id: '00000000-0000-4000-8000-000000000003', name: '合肥南站', type: 'rail_station', region_code: '340100', longitude: 117.29, latitude: 31.79, available_metrics: ['rail_departures'], available_grains: ['week', 'month'], status: 'demo' },
  { id: '00000000-0000-4000-8000-000000000004', name: '合肥新桥国际机场', type: 'airport', region_code: '340100', longitude: 116.97, latitude: 31.99, available_metrics: ['airport_throughput'], available_grains: ['month'], status: 'demo' },
]

export const demoMetrics: MetricDefinition[] = [
  { code: 'scenic_visits', display_name: '景区接待人次', definition: '指定景区在统计周期内的接待人次。', unit: '人次', supported_entity_types: ['scenic'], aggregation_rule: '仅同一景区、同一口径的周期值可比较', comparability_note: '不能与铁路发送量、机场吞吐量直接相加。' },
  { code: 'rail_departures', display_name: '铁路发送人次', definition: '指定铁路站在统计周期内的旅客发送人次。', unit: '人次', supported_entity_types: ['rail_station'], aggregation_rule: '按站点和周期统计', comparability_note: '与机场旅客吞吐量不是同一指标。' },
  { code: 'airport_throughput', display_name: '机场旅客吞吐量', definition: '指定机场在统计周期内的旅客吞吐人次。', unit: '人次', supported_entity_types: ['airport'], aggregation_rule: '按机场和周期统计', comparability_note: '包含不同方向旅客，不能当作铁路发送量。' },
]

const monthlyPeriods = ['2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']
const weeklyPeriods = ['2026-07-06', '2026-07-13', '2026-07-20', '2026-07-27', '2026-08-03', '2026-08-10', '2026-08-17', '2026-08-24', '2026-08-31', '2026-09-07', '2026-09-14', '2026-09-21']
const monthlyValues: Record<string, number[]> = {
  [DEMO_SCENIC_IDS.huangshan]: [246000, 221000, 186000, 198000, 259000, 268000, 302000, 336000, 289000, 351000, 397000, 322000],
  [DEMO_SCENIC_IDS.jiuhua]: [116000, 103000, 89000, 97000, 128000, 139000, 153000, 176000, 148000, 181000, 204000, 167000],
}
const weeklyValues: Record<string, number[]> = {
  [DEMO_SCENIC_IDS.huangshan]: [77800, 83200, 88700, 92100, 97600, 103200, 98200, 91400, 86300, 82900, 78400, 81200],
  [DEMO_SCENIC_IDS.jiuhua]: [40100, 43800, 46200, 48100, 50900, 52700, 50100, 47700, 45200, 42800, 41100, 43300],
}

function monthEnd(month: string): string {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, number, 0)).toISOString().slice(0, 10)
}

function plusDays(start: string, days: number): string {
  const date = new Date(`${start}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function demoPoint(metric: string, value: number, start: string, end: string, grain: Grain): NumericPoint {
  return { metric, value, unit: '人次', period_start: start, period_end: end, grain, source_ids: [], published_at: null, ingested_at: null, quality_status: 'demo' }
}

export function demoSeries(entityId: string, grain: TrendGrain, start: string, end: string): SeriesResult {
  const entity = demoEntities.find((item) => item.id === entityId && item.type === 'scenic') ?? demoEntities[0]
  const periods = grain === 'month' ? monthlyPeriods : weeklyPeriods
  const values = (grain === 'month' ? monthlyValues : weeklyValues)[entity.id] ?? []
  const points = periods.map((period, index) => {
    const periodStart = grain === 'month' ? `${period}-01` : period
    const periodEnd = grain === 'month' ? monthEnd(period) : plusDays(period, 6)
    return demoPoint('scenic_visits', values[index], periodStart, periodEnd, grain)
  }).filter((point) => point.period_end >= start && point.period_start <= end)
  return { entity, metric: 'scenic_visits', grain, points, missing_periods: [], coverage_ratio: points.length ? 1 : 0 }
}

export function demoForecast(entityId: string, grain: TrendGrain, trainingEnd: string, horizon: number): ForecastResult {
  const expectedEnd = grain === 'month' ? '2026-09-30' : '2026-09-27'
  if (trainingEnd !== expectedEnd) {
    return { status: 'data_insufficient', reason: '样例预测只覆盖最新样例周期；所选历史区间暂无对应预测。', forecast_points: [] }
  }
  const isJiuhua = entityId === DEMO_SCENIC_IDS.jiuhua
  const values = grain === 'month'
    ? (isJiuhua ? [173000, 158000, 146000, 151000, 163000, 179000] : [331000, 299000, 272000, 281000, 307000, 338000])
    : (isJiuhua ? [44500, 46200, 47900, 49100, 50200, 51500, 52600, 53800] : [83700, 86100, 88200, 90100, 91600, 93400, 95100, 96800])
  const periods = grain === 'month'
    ? ['2026-10-01', '2026-11-01', '2026-12-01', '2027-01-01', '2027-02-01', '2027-03-01']
    : ['2026-09-28', '2026-10-05', '2026-10-12', '2026-10-19', '2026-10-26', '2026-11-02', '2026-11-09', '2026-11-16']
  return {
    status: 'ready', model_version: 'demo-baseline-v1', source_ids: [],
    training_window: { start: grain === 'month' ? '2025-10-01' : '2026-07-06', end: trainingEnd },
    backtest: { method: '样例回测', mae: isJiuhua ? 7200 : 13800, smape: isJiuhua ? 8.2 : 7.6, sample_count: 4 },
    forecast_points: periods.slice(0, horizon).map((period, index) => ({
      ...demoPoint('scenic_visits', values[index], period, grain === 'month' ? monthEnd(period.slice(0, 7)) : plusDays(period, 6), grain),
      is_forecast: true, lower_bound: Math.round(values[index] * 0.86), upper_bound: Math.round(values[index] * 1.14), model_version: 'demo-baseline-v1',
    })),
  }
}

export function demoOverview(grain: TrendGrain): Overview {
  const isMonth = grain === 'month'
  const start = isMonth ? '2026-09-01' : '2026-09-21'
  const end = isMonth ? '2026-09-30' : '2026-09-27'
  return {
    cards: [
      { ...demoPoint('scenic_visits', isMonth ? 322000 : 81200, start, end, grain), label: '黄山风景区 · 接待人次', entity_id: DEMO_SCENIC_IDS.huangshan, status: 'demo' },
      { ...demoPoint('rail_departures', isMonth ? 625000 : 148000, start, end, grain), label: '合肥南站 · 发送人次', entity_id: demoEntities[2].id, status: 'demo' },
      { ...demoPoint('airport_throughput', isMonth ? 1020000 : 0, start, end, grain), value: isMonth ? 1020000 : null, label: '新桥机场 · 旅客吞吐量', entity_id: demoEntities[3].id, status: isMonth ? 'demo' : 'unsupported_grain' },
    ],
    map_entities: demoEntities.map((entity, index) => ({ ...entity, latest_value: [322000, 167000, 625000, 1020000][index], latest_metric: entity.available_metrics[0] })),
    active_events_count: 0,
    data_freshness: demoMetrics.map((metric) => ({ metric: metric.code, latest_period_end: end, ingested_at: null })),
  }
}

export const demoEvents: EventSummary[] = []

export const demoHolidays: Holiday[] = [
  { id: 'demo-may-2024', name: '2024 年五一假期', kind: 'may_day', year: 2024, start: '2024-05-01', end: '2024-05-05', definition_source_id: null, days_count: 5 },
  { id: 'demo-may-2025', name: '2025 年五一假期', kind: 'may_day', year: 2025, start: '2025-05-01', end: '2025-05-05', definition_source_id: null, days_count: 5 },
  { id: 'demo-national-2024', name: '2024 年国庆假期', kind: 'national_day', year: 2024, start: '2024-10-01', end: '2024-10-07', definition_source_id: null, days_count: 7 },
  { id: 'demo-national-2025', name: '2025 年国庆假期', kind: 'national_day', year: 2025, start: '2025-10-01', end: '2025-10-08', definition_source_id: null, days_count: 8 },
]

export function demoHolidayCompare(holidayIds: string[], entityId: string, mode: string): HolidayCompareResult {
  const holidays = holidayIds.map((id) => demoHolidays.find((item) => item.id === id)).filter((item): item is Holiday => Boolean(item))
  if (holidays.length !== 2) return { comparable: false, reason: '请选择两个假期。', items: [] }
  if (mode === 'day_index') return { comparable: false, reason: '静态样例仅有假期总量，缺少完整逐日数据，不能绘制逐日曲线。', items: [] }
  const isJiuhua = entityId === DEMO_SCENIC_IDS.jiuhua
  const totals: Record<string, number> = isJiuhua
    ? { 'demo-may-2024': 135000, 'demo-may-2025': 151000, 'demo-national-2024': 188000, 'demo-national-2025': 219000 }
    : { 'demo-may-2024': 292000, 'demo-may-2025': 316000, 'demo-national-2024': 405000, 'demo-national-2025': 478000 }
  const sameLength = holidays[0].days_count === holidays[1].days_count
  return {
    comparable: mode === 'daily_average' || sameLength,
    reason: mode === 'period_total' && !sameLength ? '两个假期天数不同，假期总量不宜直接比较；可选择日均值。' : null,
    items: holidays.map((holiday) => ({
      holiday_id: holiday.id, label: holiday.name, metric: 'scenic_visits',
      value: mode === 'daily_average' ? Math.round(totals[holiday.id] / holiday.days_count) : totals[holiday.id],
      unit: mode === 'daily_average' ? '人次/日' : '人次', period_start: holiday.start, period_end: holiday.end,
      grain: 'holiday', missing_days: 0, source_ids: [], published_at: null, ingested_at: null, quality_status: 'demo',
    })),
  }
}
