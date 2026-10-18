import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from '../../web_interactions/src/api/client'

const encoder = new TextEncoder()
const frame = (event: string, data: unknown) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`
const answer = { conversation_id: 'conversation', answer: '合肥机场', status: 'answered', citations: [{ id: 'source' }], limitations: [] }
function response(text: string, size = 7) {
  const bytes = encoder.encode(text)
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size))
      controller.close()
    },
  }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}
function client(fetcher: (input: string, init?: RequestInit) => Promise<Response>, unauthorized = vi.fn()) {
  return createApiClient({ fetcher, csrf: () => 'csrf-token', unauthorized })
}

describe('assistant streaming transport', () => {
  it('accepts the history forecast contract and normalizes units and fractional sMAPE', async () => {
    const app=client(async()=>new Response(JSON.stringify({status:'ready',source_ids:['source'],
      backtest:{mae:100,smape:0.128,sample_count:3},forecast_points:[{period_start:'2026-10-01',period_end:'2026-10-31',value:123,lower_bound:100,upper_bound:140}]}),
      {headers:{'Content-Type':'application/json'}}))
    const result=await app.post<any>('/forecasts/history',{grain:'month',metric:'scenic_visits'})
    expect(result.data.backtest.smape).toBe(12.8)
    expect(result.data.forecast_points[0]).toMatchObject({grain:'month',metric:'scenic_visits',unit:'人次',is_forecast:true,source_ids:['source']})
  })
  it('decodes split Chinese bytes and CRLF frames, emits deltas before final metadata', async () => {
    const onStart = vi.fn(), onDelta = vi.fn()
    const fetcher = vi.fn(async () => response(': heartbeat\r\n\r\n' + frame('start', { conversation_id: 'conversation' }) + frame('delta', { text: '合肥' }) + frame('delta', { text: '机场' }) + frame('done', answer), 1))
    const result = await client(fetcher).stream('/assistant/chat/stream', { message: '问题' }, { onStart, onDelta })
    expect(onStart).toHaveBeenCalledWith('conversation')
    expect(onDelta.mock.calls.map(item => item[0])).toEqual(['合肥', '机场'])
    expect(result).toEqual(answer)
    expect(fetcher.mock.calls[0]![0]).toBe('/api/v1/assistant/chat/stream')
    const init = fetcher.mock.calls[0]![1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(init.headers).toMatchObject({ 'X-CSRF-Token': 'csrf-token', Accept: 'text/event-stream' })
  })

  it('rejects an interrupted stream after preserving emitted partial text', async () => {
    const onDelta = vi.fn()
    await expect(client(async () => response(frame('delta', { text: '部分内容' }))).stream('/assistant/chat/stream', {}, { onDelta })).rejects.toMatchObject({ code: 'stream_interrupted' })
    expect(onDelta).toHaveBeenCalledWith('部分内容')
  })

  it('reports an upstream error without marking the partial answer complete', async () => {
    const onDelta = vi.fn()
    const text = frame('delta', { text: '部分内容' }) + frame('error', { code: 'dependency_not_available', message: '连接中断，请重试' })
    await expect(client(async () => response(text)).stream('/assistant/chat/stream', {}, { onDelta })).rejects.toMatchObject({ message: '连接中断，请重试' })
    expect(onDelta).toHaveBeenCalledOnce()
  })

  it('handles expired sessions before attempting to parse an event stream', async () => {
    const unauthorized = vi.fn()
    const app = client(async () => new Response(JSON.stringify({ error: { code: 'unauthorized', message: '请重新登录' } }), { status: 401 }), unauthorized)
    await expect(app.stream('/assistant/chat/stream', {}, { onDelta: vi.fn() })).rejects.toMatchObject({ status: 401 })
    expect(unauthorized).toHaveBeenCalledOnce()
  })

  it('stops the request and preserves partial output when the user cancels', async () => {
    const external = new AbortController()
    const onDelta = vi.fn(() => external.abort())
    const app = client(async (_input, init) => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame('delta', { text: '已生成的内容' })))
        init?.signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')))
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } }))
    await expect(app.stream('/assistant/chat/stream', {}, { onDelta }, external.signal)).rejects.toMatchObject({ code: 'request_cancelled' })
    expect(onDelta).toHaveBeenCalledWith('已生成的内容')
  })

  it('rejects malformed data instead of silently accepting an incomplete answer', async () => {
    await expect(client(async () => response('event: delta\ndata: invalid\n\n')).stream('/assistant/chat/stream', {}, { onDelta: vi.fn() })).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
