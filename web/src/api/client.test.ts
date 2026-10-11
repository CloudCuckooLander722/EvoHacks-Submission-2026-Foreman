import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent } from './types'

/** Just enough EventSource to drive realApi.subscribe. */
class FakeEventSource {
  static CLOSED = 2
  static last: FakeEventSource
  readyState = 1
  listeners = new Map<string, ((e: Event) => void)[]>()
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener(name: string, fn: (e: Event) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn])
  }
  close() {
    this.readyState = FakeEventSource.CLOSED
  }
  emit(name: string, data?: unknown) {
    const e = data === undefined ? new Event(name) : new MessageEvent(name, { data: JSON.stringify(data) })
    for (const fn of this.listeners.get(name) ?? []) fn(e)
  }
}

beforeEach(() => vi.stubGlobal('EventSource', FakeEventSource))
afterEach(() => vi.unstubAllGlobals())

async function subscribe() {
  const { realApi } = await import('./client')
  const events: RunEvent[] = []
  realApi.subscribe('r_1', (e) => events.push(e))
  return { es: FakeEventSource.last, events }
}

describe('realApi.subscribe', () => {
  it('opens the §D events URL and forwards named events', async () => {
    const { es, events } = await subscribe()
    expect(es.url).toBe('/api/runs/r_1/events')
    es.emit('stats', { decisions_total: 3 })
    expect(events).toEqual([{ type: 'stats', data: { decisions_total: 3 } }])
  })

  it('closes on done, and ignores the connection drop that follows', async () => {
    const { es, events } = await subscribe()
    es.emit('done', { run_id: 'r_1', results_url: '/runs/r_1/results' })
    es.emit('error')
    expect(es.readyState).toBe(FakeEventSource.CLOSED)
    expect(events.map((e) => e.type)).toEqual(['done'])
  })

  it('tells a server error event from a connection error', async () => {
    const { es, events } = await subscribe()
    es.emit('error', { message: 'one node skipped items', node: 'n2', fatal: false })
    expect(es.readyState).toBe(1) // non-fatal: keep listening
    es.emit('error') // reconnecting: not reported
    expect(events).toHaveLength(1)
    es.readyState = FakeEventSource.CLOSED
    es.emit('error') // gave up: reported as fatal
    expect(events.at(-1)).toMatchObject({ type: 'error', data: { fatal: true } })
  })
})
