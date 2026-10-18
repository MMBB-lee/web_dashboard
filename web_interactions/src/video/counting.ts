import type { Counts, CountGrain, CountPoint } from '../types/api'

export function elapsed(seconds: number) {
  return `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`
}

type RawPoint = Omit<CountPoint, 'period_start' | 'period_end' | 'published_at' | 'ingested_at'> & {
  period_start?: string | null; period_end?: string | null; recorded_at?: string | null;
  published_at?: string | null; ingested_at?: string | null
}
type RawCounts = Omit<Counts, 'roi' | 'points'> & { roi: Counts['roi'] | { points: Counts['roi'] }; points: RawPoint[] }

export function normalizeCounts(input: unknown, grain: CountGrain, demo = false): Counts {
  const raw = input as RawCounts
  if (!raw || raw.metric !== 'camera_visible_people' || !Array.isArray(raw.points)) throw Error('计数结果格式不正确')
  if (!(raw.count_model === 'p2pnet' || (demo && raw.count_model === 'dm_count')) || !raw.weights_version || !raw.device || !['pytorch', 'openvino'].includes(raw.inference_engine)) throw Error('计数接口未提供有效的 P2PNet 模型信息')
  if (!demo && (raw.inference_engine !== 'pytorch' || raw.device !== 'CPU')) throw Error('当前 P2PNet 仅支持 PyTorch CPU 计数结果')
  const roi = Array.isArray(raw.roi) ? raw.roi : raw.roi?.points
  if (!Array.isArray(roi) || roi.length < 3 || roi.some(p => !Array.isArray(p) || p.length !== 2 || p.some(v => !Number.isFinite(v) || v < 0 || v > 1))) throw Error('计数接口未提供有效的归一化 ROI 多边形')
  const relative = raw.time_basis === 'relative'
  const points = raw.points.map(p => {
    if (p.metric !== 'camera_visible_people' || p.unit !== '人' || p.grain !== grain || (p.value != null && (!Number.isFinite(p.value) || p.value < 0))) throw Error('计数接口的指标、粒度或人数不符合约定')
    if (grain === '5min' && (p.aggregation_rule !== 'median' || !Number.isInteger(p.sample_count) || p.sample_count! < 0)) throw Error('计数接口的聚合信息不符合约定')
    if (relative && (!Number.isFinite(p.offset_seconds) || p.offset_seconds! < 0)) throw Error('计数接口缺少录像相对时间')
    const end = relative ? elapsed(p.offset_seconds!) : p.period_end || p.recorded_at || ''
    const start = relative ? elapsed(p.period_start_seconds ?? p.offset_seconds!) : p.period_start || p.recorded_at || ''
    if (!relative && (!Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end)) || Date.parse(start) > Date.parse(end))) throw Error('计数接口未提供有效统计时间')
    return { ...p, recorded_at: p.recorded_at || undefined, period_start: start, period_end: end, published_at: p.published_at || '', ingested_at: p.ingested_at || '' }
  }).sort((a, b) => relative ? a.offset_seconds! - b.offset_seconds! : Date.parse(a.period_end) - Date.parse(b.period_end))
  return { ...raw, grain, roi, points } as Counts
}
