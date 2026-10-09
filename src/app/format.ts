import type { Grain, NumericPoint } from './types'

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
  return point.grain === 'month' ? point.period_start.slice(0, 7) : point.period_start.slice(5)
}

export function formatRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value.toFixed(1)}%`
}

export function displayError(error: unknown): string {
  return error instanceof Error ? error.message : '数据加载失败，请稍后重试。'
}
