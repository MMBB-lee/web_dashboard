import type { EventItem } from './types/api'
export function dateText(value?: string | null, time = false) {
  if (!value) return '未提供'
  const date = new Date(value.length === 10 ? `${value}T00:00:00+08:00` : value)
  if (!Number.isFinite(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', ...(time ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}) }).format(date)
}
export function numberText(value?: number | null) { return value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(value) }
export function decimalText(value?: number | null) { return value == null || !Number.isFinite(value) ? '—' : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value) }
export function effectiveEventStatus(event: EventItem, now = Date.now()) { return event.status === 'resolved' ? 'resolved' : event.ends_at && Date.parse(event.ends_at) <= now ? 'expired' : Date.parse(event.starts_at) > now ? 'scheduled' : 'active' }
export const severityLabels = { info: '一般信息', notice: '注意', warning: '关注', critical: '重要' }
