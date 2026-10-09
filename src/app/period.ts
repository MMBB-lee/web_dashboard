import type { TrendGrain } from './types'

const DAY_MS = 24 * 60 * 60 * 1000

export function localDateString(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function utcDate(value: string): Date {
  return new Date(`${value}T00:00:00Z`)
}

export function addDays(value: string, days: number): string {
  return new Date(utcDate(value).getTime() + days * DAY_MS).toISOString().slice(0, 10)
}

export function monthEnd(month: string): string {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, number, 0)).toISOString().slice(0, 10)
}

export function isoWeek(dateValue: string): string {
  const date = utcDate(dateValue)
  date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7))
  const year = date.getUTCFullYear()
  const yearStart = new Date(Date.UTC(year, 0, 1))
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / DAY_MS + 1) / 7)
  return `${year}-W${String(week).padStart(2, '0')}`
}

export function weekStart(weekValue: string): string {
  const [year, week] = weekValue.split('-W').map(Number)
  const januaryFourth = new Date(Date.UTC(year, 0, 4))
  const dayFromMonday = (januaryFourth.getUTCDay() + 6) % 7
  januaryFourth.setUTCDate(januaryFourth.getUTCDate() - dayFromMonday + (week - 1) * 7)
  return januaryFourth.toISOString().slice(0, 10)
}

export function periodInputValue(dateValue: string, grain: TrendGrain): string {
  return grain === 'month' ? dateValue.slice(0, 7) : isoWeek(dateValue)
}

export function periodStart(value: string, grain: TrendGrain): string {
  return grain === 'month' ? `${value}-01` : weekStart(value)
}

export function periodEnd(value: string, grain: TrendGrain, today: string): string {
  const lastDay = grain === 'month' ? monthEnd(value) : addDays(weekStart(value), 6)
  return lastDay > today ? today : lastDay
}

export function isPeriodInput(value: string, grain: TrendGrain): boolean {
  if (grain === 'month') return /^\d{4}-(0[1-9]|1[0-2])$/.test(value)
  return /^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$/.test(value) && isoWeek(weekStart(value)) === value
}
