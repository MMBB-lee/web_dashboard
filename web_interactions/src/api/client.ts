import { normalizeForecast } from './forecast'
import { readSseEvents, StreamProtocolError } from './event-stream'
import type { Envelope } from '../types/api'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) { super(message) }
}
export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>
export function createApiClient(options: { fetcher?: Fetcher; csrf: () => string | null; unauthorized: () => void }) {
  const fetcher = options.fetcher ?? ((input, init) => fetch(input, init))
  async function request<T>(path: string, method = 'GET', body?: unknown): Promise<Envelope<T>> {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('API 路径必须以单个 / 开头')
    const headers: Record<string, string> = { Accept: 'application/json' }
    const multipart = body instanceof FormData
    if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json'
    if (method !== 'GET' && path !== '/auth/login') {
      const token = options.csrf()
      if (!token) throw new ApiError(401, 'missing_session', '登录已失效，请重新登录')
      headers['X-CSRF-Token'] = token
    }
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), path === '/assistant/chat' ? 90_000 : 30_000)
    try {
      const response = await fetcher(`/api/v1${path}`, { method, headers, credentials: 'same-origin', signal: controller.signal, ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }) })
      if (response.status === 401 && path !== '/auth/login') options.unauthorized()
      let payload = await response.json().catch(() => null)
      if (!response.ok) throw new ApiError(response.status, payload?.error?.code ?? 'request_failed', payload?.error?.message ?? '请求失败，请稍后重试', payload?.request_id)
      if (path === '/forecasts/history' && payload && Array.isArray(payload.forecast_points) && !('data' in payload)) {
        const input = body as { grain?: string; metric?: string }
        payload = { data: { ...payload, backtest: payload.backtest ? { ...payload.backtest,
          smape: payload.backtest.smape == null ? null : payload.backtest.smape * 100,
        } : null, forecast_points: payload.forecast_points.map((point: object) => ({
          ...point, grain: input.grain, metric: input.metric, unit: '人次', is_forecast: true,
          quality_status: 'forecast', source_ids: payload.source_ids ?? [],
        })) }, meta: { request_id: response.headers.get('x-request-id') ?? 'history-forecast' } }
      }
      if (!payload || !('data' in payload) || !payload.meta) throw new ApiError(502, 'invalid_response', '接口返回格式不符合约定')
      if (path === '/forecasts/history' || path.endsWith('/short-forecast')) payload.data = normalizeForecast(payload.data)
      return payload as Envelope<T>
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError(0, 'network_error', controller.signal.aborted ? '请求超时，请重试' : '暂时无法连接服务，请检查后端是否启动')
    } finally { clearTimeout(timer) }
  }
  async function stream<T>(path: string, body: unknown, handlers: {
    onStart?: (conversationId: string) => void; onDelta: (text: string) => void
  }, signal?: AbortSignal): Promise<T> {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('API 路径必须以单个 / 开头')
    const token = options.csrf()
    if (!token) throw new ApiError(401, 'missing_session', '登录已失效，请重新登录')
    const controller = new AbortController()
    const abort = () => controller.abort()
    let timedOut = false
    let timer: ReturnType<typeof setTimeout>
    const resetTimer = () => {
      clearTimeout(timer)
      timer = setTimeout(() => { timedOut = true; controller.abort() }, 90_000)
    }
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) controller.abort()
    resetTimer()
    try {
      const response = await fetcher(`/api/v1${path}`, {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json', 'X-CSRF-Token': token },
        body: JSON.stringify(body),
      })
      if (response.status === 401) options.unauthorized()
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new ApiError(response.status, payload?.error?.code ?? 'request_failed', payload?.error?.message ?? '请求失败，请稍后重试', payload?.request_id)
      }
      if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) {
        throw new ApiError(502, 'invalid_response', '接口没有返回流式回答，请检查后端版本')
      }
      for await (const frame of readSseEvents(response.body)) {
        resetTimer()
        const data = frame.data as Record<string, unknown> | null
        if (!data || typeof data !== 'object') throw new StreamProtocolError('流式响应格式无效，请重试')
        if (frame.event === 'start') {
          if (typeof data.conversation_id !== 'string') throw new StreamProtocolError('流式会话格式无效')
          handlers.onStart?.(data.conversation_id)
        } else if (frame.event === 'delta') {
          if (typeof data.text !== 'string') throw new StreamProtocolError('流式文字格式无效')
          handlers.onDelta(data.text)
        } else if (frame.event === 'done') {
          if (typeof data.answer !== 'string' || typeof data.conversation_id !== 'string') throw new StreamProtocolError('回答完成信息无效')
          return data as T
        } else if (frame.event === 'error') {
          throw new ApiError(503, typeof data.code === 'string' ? data.code : 'stream_failed',
            typeof data.message === 'string' ? data.message : '回答生成中断，请重试')
        }
      }
      throw new ApiError(502, 'stream_interrupted', '回答连接已中断，内容尚未完整生成，请重试')
    } catch (error) {
      if (error instanceof ApiError) throw error
      if (error instanceof StreamProtocolError) throw new ApiError(502, 'invalid_response', error.message)
      if (signal?.aborted && !timedOut) throw new ApiError(0, 'request_cancelled', '已停止生成，以上内容可能不完整')
      throw new ApiError(0, 'network_error', timedOut ? '回答连接超时，内容尚未完整生成，请重试' : '回答连接中断，请检查网络后重试')
    } finally {
      clearTimeout(timer!)
      signal?.removeEventListener('abort', abort)
    }
  }
  return { request, stream, get: <T>(path: string) => request<T>(path), post: <T>(path: string, body?: unknown) => request<T>(path, 'POST', body) }
}
export type ApiClient = ReturnType<typeof createApiClient>
export function query(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== '') params.set(key, String(value))
  return params.toString()
}
export function safeSourceUrl(value?: string): string | undefined {
  if (!value) return undefined
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '发生未知错误，请重试' }
