import type { Grain, NumericPoint } from './types'
import { isoWeek } from './period'

export function formatValue(value: number | null | undefined, maximumFractionDigits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits }).format(value)
}

export function formatPeriod(start?: string | null, end?: string | null, grain?: Grain): string {
  if (!start) return '统计期未提供'
  if (grain === 'month') return start.slice(0, 7)
  if (!end || start === end) return start
  return `${start} — ${end}`
}

export function pointLabel(point: NumericPoint): string {
  if (point.grain === 'month') {
    const [year, month] = point.period_start.split('-')
    return `${year}年${Number(month)}月`
  }
  if (point.grain === 'week') {
    const [year, week] = isoWeek(point.period_start).split('-W')
    return `${year}年第${Number(week)}周`
  }
  return point.period_start.slice(5)
}

export function formatAxisValue(value: number): string {
  return Math.abs(value) >= 10000 ? `${formatValue(value / 10000, 2)}万` : formatValue(value, 2)
}

export function formatRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(1)}%`
}

export function displayError(error: unknown): string {
  return error instanceof Error ? error.message : '数据加载失败，请稍后重试。'
}
