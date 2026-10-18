import { describe, expect, it } from 'vitest'
import { normalizeCounts } from '../../web_interactions/src/video/counting'

const metadata = { metric: 'camera_visible_people', count_model: 'p2pnet', weights_version: 'test', device: 'CPU', inference_engine: 'pytorch', roi: { points: [[0,0],[1,0],[1,1],[0,1]] }, manual_validation: null }
describe('P2PNet result normalization', () => {
  it('keeps zero counts and relative video time without inventing a date', () => {
    const raw = { ...metadata, time_basis: 'relative', points: [10,0].map(offset_seconds => ({ offset_seconds, value: offset_seconds, metric: 'camera_visible_people', unit: '人', grain: 'frame', quality_status: 'good', source_ids: ['video'] })) }
    const result = normalizeCounts(raw, 'frame')
    expect(result.points.map(p => p.value)).toEqual([0,10])
    expect(result.points.map(p => p.period_end)).toEqual(['00:00:00','00:00:10'])
    expect(result.roi).toHaveLength(4)
    expect(result.manual_validation).toBeNull()
  })
  it('accepts formal P2PNet recorded_at frame times', () => {
    const date = '2026-10-18T09:00:00+08:00'
    const result = normalizeCounts({ ...metadata, points: [{ metric: 'camera_visible_people', unit: '人', grain: 'frame', value: 3, recorded_at: date, quality_status: 'good', source_ids: ['video'] }] }, 'frame')
    expect(result.points[0]!.period_end).toBe(date)
  })
  it('rejects absent offsets and retired models in API mode', () => {
    expect(() => normalizeCounts({ ...metadata, count_model: 'dm_count', points: [] }, 'frame')).toThrow('P2PNet')
    expect(() => normalizeCounts({ ...metadata, time_basis: 'relative', points: [{ metric: 'camera_visible_people', grain: 'frame', unit: '人', value: 2 }] }, 'frame')).toThrow('相对时间')
  })
})
