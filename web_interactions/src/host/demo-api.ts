import type { DemoHandler } from '../../dev/mock'
import { entities, seriesPoints, events } from '../../dev/fixtures'
import { demoEntities, demoMetrics, demoOverview, demoSeries, demoForecast, demoHolidays, demoHolidayCompare } from '../../../web_dashboard/tests/demoData'
import type { TrendGrain } from '../../../web_dashboard/src/app/types'
export function dashboardDemo(scenario: string): DemoHandler {
 const ok = (data: unknown) => new Response(JSON.stringify({ data, meta: { request_id: 'demo-dashboard' } }))
 const fail = (status: number, message: string) => new Response(JSON.stringify({ error: { code: 'invalid_query', message } }), { status })
 return (url, init) => {
  const path = url.pathname.replace('/api/v1', ''), q = url.searchParams
  const body = init?.body ? JSON.parse(String(init.body)) : {}
  const grain = (q.get('grain') ?? body.grain ?? 'month') as TrendGrain
  const empty = scenario === 'empty'
  if (path === '/catalog/entities') {
   const all = [...demoEntities.filter(e => e.type === 'scenic'), ...entities]
   const filtered = empty ? [] : all.filter(e => !q.get('type') || e.type === q.get('type'))
   const size = Math.min(100, Math.max(1, Number(q.get('page_size')) || 20)), page = Math.max(1, Number(q.get('page')) || 1)
   return new Response(JSON.stringify({data:filtered.slice((page-1)*size,page*size),meta:{request_id:'demo-catalog',page,page_size:size,total:filtered.length}}))
  }
  if (path === '/catalog/metrics') return ok(demoMetrics.filter(m => !q.get('entity_type') || m.supported_entity_types.includes(q.get('entity_type')!)))
  if (path === '/dashboard/overview') {
   if (!['week', 'month'].includes(grain)) return fail(422, '不支持的统计粒度')
   const result = demoOverview(grain)
   for (const card of result.cards) {
    const entity = entities.find(e => e.id === card.entity_id)
    if (entity) { card.value = grain === 'month' ? seriesPoints(entity).at(-1)!.value : null; card.status = grain === 'month' ? 'demo' : 'unsupported_grain' }
   }
   for (const point of result.map_entities) { const e = entities.find(e => e.id === point.id); if (e) { point.latest_value = grain === 'month' ? seriesPoints(e).at(-1)!.value : null; point.available_grains = e.available_grains } }
   result.active_events_count = events.filter(e => e.status === 'active').length
   if (empty) { result.cards = []; result.map_entities = []; result.active_events_count = 0 }
   return ok(result)
  }
  if (/^\/scenic\/[^/]+\/series$/.test(path)) {
   const id = path.split('/')[2]!
   if (!demoEntities.some(e => e.id === id && e.type === 'scenic')) return fail(404, '对象不存在')
   if (!['week', 'month'].includes(grain) || q.get('metric') !== 'scenic_visits') return fail(422, '指标或粒度不匹配')
   const result = demoSeries(id, grain, q.get('start') ?? '', q.get('end') ?? '9999')
   if (empty) { result.points = []; result.coverage_ratio = 0 }
   return ok(result)
  }
  if (path === '/forecasts/history' && body.metric === 'scenic_visits') return ok(empty || scenario === 'insufficient' ? { status: 'data_insufficient', forecast_points: [], reason: '历史样本不足' } : demoForecast(body.entity_id, grain, body.training_end, body.horizon))
  if (path === '/analytics/compare') {
   const requested = body.series as Array<{entity_id:string;metric:string}>
   if (!Array.isArray(requested) || requested.length < 2 || requested.length > 4 || !['separate_axes','index_100'].includes(body.display_mode) || !body.start || !body.end || body.start > body.end) return fail(422, '请提供 2—4 个对象及有效比较范围')
   const series = []
   for (const item of requested) {
    const scenic = demoEntities.find(e=>e.type === 'scenic' && e.id === item.entity_id)
    const transport = entities.find(e=>e.id === item.entity_id)
    const entity = scenic ?? transport
    if (!entity || !entity.available_metrics.includes(item.metric) || !entity.available_grains.includes(grain)) return fail(422,'对象、指标或粒度不匹配')
    let points = scenic ? demoSeries(item.entity_id,grain,body.start,body.end).points : seriesPoints(transport!).filter(p=>p.period_start >= body.start && p.period_end <= body.end)
    if (empty) points = []
    if (body.display_mode === 'index_100') {
     const base = points.find(p=>p.period_start === body.base_period)?.value
     if (!base) return fail(422,'指数化基期必须有非零观测值')
     points = points.map(p=>({...p,value:p.value === null ? null : p.value / base * 100,unit:'指数（基期=100）'}))
    }
    series.push({entity_id:item.entity_id,metric:item.metric,points,missing_periods:[],source_ids:[...new Set(points.flatMap(p=>p.source_ids))]})
   }
   return ok({series,comparison_note:'演示数据：各对象按相同时间范围分别比较，不相加；缺失值不补造。'})
  }
  if (path === '/holidays') return ok(empty ? [] : demoHolidays.filter(h => (!q.get('year') || h.year === Number(q.get('year'))) && (!q.get('kind') || h.kind === q.get('kind'))))
  if (path === '/holidays/compare') return ok(empty ? { comparable: false, reason: '暂无数据', items: [] } : demoHolidayCompare(body.holiday_ids, body.series[0].entity_id, body.mode))
  if (path === '/holidays/statistics') return ok(empty ? { comparable: false, reason: '暂无数据', items: [] } : demoHolidayCompare((q.get('holiday_ids') ?? '').split(','), q.get('entity_id') ?? '', q.get('mode') ?? 'daily_average'))
  return undefined
 }
}
