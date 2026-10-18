import type { Entity, EventItem, Point, VideoItem, CountPoint, Counts } from '../src/types/api'
export const entities: Entity[] = [
  { id: '10000000-0000-4000-8000-000000000001', name: '合肥南站', type: 'rail_station', region_code: '340100', longitude: 117.29, latitude: 31.79, available_metrics: ['rail_departures'], available_grains: ['month'], status: 'available' },
  { id: '10000000-0000-4000-8000-000000000002', name: '黄山北站', type: 'rail_station', region_code: '341000', longitude: 118.28, latitude: 29.82, available_metrics: ['rail_departures'], available_grains: ['month'], status: 'available' },
  { id: '10000000-0000-4000-8000-000000000003', name: '合肥新桥机场', type: 'airport', region_code: '340100', longitude: 116.97, latitude: 31.99, available_metrics: ['airport_throughput'], available_grains: ['month'], status: 'available' },
  { id: '10000000-0000-4000-8000-000000000004', name: '芜湖站', type: 'rail_station', region_code: '340200', longitude: 118.42, latitude: 31.36, available_metrics: ['rail_departures'], available_grains: ['month'], status: 'available' },
  { id: '10000000-0000-4000-8000-000000000005', name: '安庆站', type: 'rail_station', region_code: '340800', longitude: 117.06, latitude: 30.53, available_metrics: ['rail_departures'], available_grains: ['month'], status: 'available' },
]
export const sourceId = '20000000-0000-4000-8000-000000000001'
export function seriesPoints(entity: Entity): Point[] {
  const values = entity.type === 'airport' ? [76, 84, 79, 83, 91, 87, 98, 106, 93]
    : entity.name === '黄山北站' ? [28, 39, 32, 43, 48, 42, 56, 61, 49]
    : entity.name === '芜湖站' ? [38, 43, 40, 46, 49, 45, 57, 59, 52.7]
    : entity.name === '安庆站' ? [20, 24, 22, 25, 27, 24, 31, 33, 28.6]
    : [118, 143, 126, 139, 152, 144, 172, 184, 154.8]
  return values.map((value, i) => ({
    metric: entity.available_metrics[0]!, value: value * 10000, unit: '人次', period_start: `2026-${String(i + 1).padStart(2, '0')}-01`,
    period_end: `2026-${String(i + 1).padStart(2, '0')}-${new Date(2026, i + 1, 0).getDate()}`, grain: 'month',
    source_ids: [sourceId], published_at: '2026-10-08T09:00:00+08:00', ingested_at: '2026-10-08T10:00:00+08:00', quality_status: 'demo',
  }))
}
export const events: (EventItem & { region_code: string })[] = [
  { id: '30000000-0000-4000-8000-000000000001', title: '黄山景区部分游步道临时维护', kind: 'scenic_closure', status: 'active', authority: 'official', severity: 'notice', region_code: '341000', affected_entity_ids: [entities[1]!.id], starts_at: '2026-10-09T08:00:00+08:00', ends_at: '2026-10-15T18:00:00+08:00', published_at: '2026-10-08T16:00:00+08:00', last_verified_at: '2026-10-09T08:00:00+08:00', source_id: sourceId, description: '这是一条模拟公告，用于演示事件详情。部分游步道维护期间，请查看入口指引与开放范围。该内容不能作为出行依据。', source_url: '', timeline: [{ at: '2026-10-08T16:00:00+08:00', label: '公告发布', description: '演示来源发布维护说明' }, { at: '2026-10-09T08:00:00+08:00', label: '开始生效', description: '部分路段进入维护状态' }] },
  { id: '30000000-0000-4000-8000-000000000002', title: '合肥南站发送量波动提示', kind: 'rail_adjustment', status: 'active', authority: 'model_signal', severity: 'warning', region_code: '340100', affected_entity_ids: [entities[0]!.id], starts_at: '2026-10-09T09:00:00+08:00', ends_at: '2026-10-15T23:59:00+08:00', published_at: '2026-10-09T09:00:00+08:00', last_verified_at: '2026-10-09T09:00:00+08:00', source_id: sourceId, description: '模拟规则发现月度发送量变化，仅用于展示模型提示。模型提示不是官方预警，也不能用于判断车站实时拥挤程度。', timeline: [{ at: '2026-10-09T09:00:00+08:00', label: '模型提示生成', description: '演示规则 demo-signal-v1' }] },
  { id: '30000000-0000-4000-8000-000000000003', title: '合肥新桥机场航班调整通知', kind: 'flight_adjustment', status: 'resolved', authority: 'official', severity: 'info', region_code: '340100', affected_entity_ids: [entities[2]!.id], starts_at: '2026-09-28T08:00:00+08:00', ends_at: '2026-09-28T18:00:00+08:00', published_at: '2026-09-27T16:00:00+08:00', last_verified_at: '2026-09-28T18:00:00+08:00', source_id: sourceId, description: '模拟航班调整公告，已解除。保留时间线用于说明历史公告与当前有效状态的区别。', timeline: [{ at: '2026-09-27T16:00:00+08:00', label: '公告发布', description: '模拟时刻调整' }, { at: '2026-09-28T18:00:00+08:00', label: '影响解除', description: '演示调整已结束' }] },
]
type RecordedDemoVideo = VideoItem & { scenic_id: string; camera_id: string; recorded_start_at: string; recorded_end_at: string; permission_record_id: string }
export const videos: RecordedDemoVideo[] = [
  { id: '40000000-0000-4000-8000-000000000001', scenic_id: '00000000-0000-4000-8000-000000000001', camera_id: 'demo-east-gate', recorded_start_at: '2026-09-28T09:00:00+08:00', recorded_end_at: '2026-09-28T12:00:00+08:00', status: 'ready', permission_record_id: 'demo-no-video-file' },
  { id: '40000000-0000-4000-8000-000000000002', scenic_id: '00000000-0000-4000-8000-000000000001', camera_id: 'demo-sparse-area', recorded_start_at: '2026-09-28T09:00:00+08:00', recorded_end_at: '2026-09-28T12:00:00+08:00', status: 'ready', permission_record_id: 'demo-no-video-file' },
]
export const analysisId = '60000000-0000-4000-8000-000000000001'
export const demoAnalysisIds: Record<string, string> = {
  [videos[0]!.id]: analysisId,
  [videos[1]!.id]: '60000000-0000-4000-8000-000000000002',
}
export const demoCameraNames: Record<string, string> = {
  'demo-east-gate': '黄山 · 密集画面模拟样本',
  'demo-sparse-area': '黄山 · 稀疏画面模拟样本',
}
export function chinaTime(time: number) { return new Date(time + 8 * 3600000).toISOString().slice(0, 19) + '+08:00' }
function cameraPoints(video: RecordedDemoVideo, sparse: boolean) {
  const start = Date.parse(video.recorded_start_at)
  const frames: CountPoint[] = Array.from({ length: 1080 }, (_, frame) => {
    const window = Math.floor(frame / 30), sample = frame % 30
    const quality_status = window === 1 ? 'missing' : window === 21 ? 'occluded' : 'demo'
    const estimate = sparse ? Math.max(0, Math.round(3 + 2 * Math.sin(window / 6) + sample % 3 - 1)) : Math.round(75 + 25 * Math.sin(window / 6) + window / 2 + sample % 5 - 2)
    const at = chinaTime(start + frame * 10000)
    return {
      metric: 'camera_visible_people', value: quality_status === 'demo' ? window === 0 ? 0 : estimate : null, unit: '人',
      period_start: at, period_end: at, grain: 'frame', source_ids: [`demo-camera-${video.camera_id}`],
      published_at: video.recorded_end_at, ingested_at: video.recorded_end_at, quality_status,
    }
  })
  const points: CountPoint[] = Array.from({ length: 36 }, (_, window) => {
    const samples = frames.slice(window * 30, (window + 1) * 30)
    const values = samples.map(p => p.value).filter((value): value is number => value !== null).sort((a, b) => a - b)
    const middle = Math.floor(values.length / 2)
    const value = values.length ? values.length % 2 ? values[middle]! : (values[middle - 1]! + values[middle]!) / 2 : null
    return {
      ...samples[0]!, period_start: chinaTime(start + window * 300000), period_end: chinaTime(start + (window + 1) * 300000),
      grain: '5min', value, sample_count: values.length, aggregation_rule: 'median',
    }
  })
  const analysis: Counts = {
    metric: 'camera_visible_people', roi: [[.12, .18], [.88, .18], [.88, .9], [.12, .9]], points,
    count_model: 'dm_count', weights_version: 'demo-dm-count-simulated', inference_engine: 'pytorch', device: '模拟数据 · 未执行推理',
    sample_interval_seconds: 10, aggregation_minutes: 5, manual_validation: { sample_count: 12, mae: sparse ? .8 : 3.6 },
  }
  return { analysis, frames }
}
export const demoCameraAnalyses = Object.fromEntries(videos.map((video, index) => [demoAnalysisIds[video.id]!, cameraPoints(video, index === 1)])) as Record<string, { analysis: Counts; frames: CountPoint[] }>
export const counts = demoCameraAnalyses[analysisId]!.analysis.points
