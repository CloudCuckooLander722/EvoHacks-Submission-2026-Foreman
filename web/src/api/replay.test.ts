import { describe, expect, it } from 'vitest'
import decisionsJson from '../mocks/decisions.json'
import resultsJson from '../mocks/results.json'
import { initialRun, runReducer, type RunState } from '../state/run'
import { buildReplay } from './replay'
import type { Decision, ResultsFile } from './types'

const results = resultsJson as unknown as ResultsFile
const decisions = decisionsJson as unknown as Decision[]
const replay = buildReplay(results, decisions)
const types = replay.map((t) => t.event.type)

describe('buildReplay', () => {
  it('starts with plan and ends with done, inside ~20 s', () => {
    expect(types[0]).toBe('plan')
    expect(types.at(-1)).toBe('done')
    expect(replay.at(-1)!.at).toBeLessThanOrEqual(20_000)
    expect(types.filter((t) => t === 'plan')).toHaveLength(1)
  })

  it('is in time order', () => {
    for (let i = 1; i < replay.length; i++) expect(replay[i].at).toBeGreaterThanOrEqual(replay[i - 1].at)
  })

  it('runs the nodes one after another, in plan order, each running before done', () => {
    const nodeEvents = replay.flatMap((t) => (t.event.type === 'node' ? [t.event.data] : []))
    const starts = nodeEvents.filter((n) => n.status === 'running' && n.done === 0).map((n) => n.node)
    const dones = nodeEvents.filter((n) => n.status === 'done').map((n) => n.node)
    const order = results.plan.nodes.map((n) => n.id)
    expect(starts).toEqual(order)
    expect(dones).toEqual(order)
    for (const id of order) {
      const first = nodeEvents.findIndex((n) => n.node === id)
      const done = nodeEvents.findIndex((n) => n.node === id && n.status === 'done')
      expect(nodeEvents.slice(first, done).every((n) => n.node === id)).toBe(true)
    }
  })

  it('ends the meter on exactly results.stats, never counting down on the way', () => {
    const stats = replay.flatMap((t) => (t.event.type === 'stats' ? [t.event.data] : []))
    expect(stats.at(-1)).toEqual(results.stats)
    for (let i = 1; i < stats.length; i++)
      expect(stats[i].decisions_total).toBeGreaterThanOrEqual(stats[i - 1].decisions_total)
  })

  it('samples the ticker to at most ~20 decisions a second', () => {
    const n = types.filter((t) => t === 'decision').length
    expect(n).toBeGreaterThan(0)
    expect(n).toBeLessThanOrEqual(20 * 20)
    const big = buildReplay(results, Array(200).fill(decisions).flat(), 2_000)
    expect(big.filter((t) => t.event.type === 'decision').length).toBeLessThanOrEqual(2 * 20 + results.plan.nodes.length)
  })

  it('carries each node report on its done event', () => {
    for (const r of results.node_reports) {
      const done = replay.find(
        (t) => t.event.type === 'node' && t.event.data.node === r.node && t.event.data.status === 'done',
      )
      expect(done?.event.type === 'node' && done.event.data.report).toEqual(r)
    }
  })
})

describe('runReducer over the whole replay', () => {
  it('finishes done, all steps done, with the final stats', () => {
    let s: RunState = runReducer(runReducer(initialRun, { type: 'start' }), { type: 'started', runId: 'r' })
    for (const { event } of replay) s = runReducer(s, { type: 'event', event })
    expect(s.status).toBe('done')
    expect(Object.values(s.nodes).every((n) => n.status === 'done')).toBe(true)
    expect(s.stats).toEqual(results.stats)
    expect(s.recent.length).toBeGreaterThan(0)
  })

  it('a fatal error event stops the run; a non-fatal one does not', () => {
    const running: RunState = { ...initialRun, status: 'running' }
    const soft = runReducer(running, { type: 'event', event: { type: 'error', data: { message: 'x', node: 'n1', fatal: false } } })
    expect(soft.status).toBe('running')
    const hard = runReducer(running, { type: 'event', event: { type: 'error', data: { message: 'x', node: null, fatal: true } } })
    expect(hard.status).toBe('error')
  })
})
