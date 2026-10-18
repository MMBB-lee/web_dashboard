export class StreamProtocolError extends Error {}

/** Decode UTF-8 and SSE frames across arbitrary network chunk boundaries. */
export async function* readSseEvents(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        let event = 'message'
        const data: string[] = []
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
        }
        if (!data.length) continue // Heartbeat comments have no data.
        let parsed: unknown
        try { parsed = JSON.parse(data.join('\n')) }
        catch { throw new StreamProtocolError('流式响应格式无效，请重试') }
        yield { event, data: parsed }
      }
      if (done) {
        if (buffer.trim()) throw new StreamProtocolError('回答连接已中断，请重试')
        break
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
