import type { DecisionEvent, ErrorEvent, NodeReport, PlanEvent, RunEvent, RunStats } from '../api/types'

export type NodeStatus = 'pending' | 'running' | 'done'

export interface NodeProgress {
  status: NodeStatus
  done: number
  items: number
  report?: NodeReport
}

export interface RunState {
  status: 'idle' | 'starting' | 'running' | 'done' | 'error'
  runId: string | null
  plan: PlanEvent | null
  nodes: Record<string, NodeProgress>
  stats: RunStats | null
  /** Newest first, capped, for the live ticker. */
  recent: DecisionEvent[]
  errors: ErrorEvent[]
}

export const initialRun: RunState = {
  status: 'idle',
  runId: null,
  plan: null,
  nodes: {},
  stats: null,
  recent: [],
  errors: [],
}

const RECENT_MAX = 12

export type RunAction =
  | { type: 'start' }
  | { type: 'started'; runId: string }
  | { type: 'failed'; message: string }
  | { type: 'event'; event: RunEvent }

/** Folds §D events into what the live demo shows. */
export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'start':
      return { ...initialRun, status: 'starting' }
    case 'started':
      return { ...state, status: 'running', runId: action.runId }
    case 'failed':
      return {
        ...state,
        status: 'error',
        errors: [...state.errors, { message: action.message, node: null, fatal: true }],
      }
    case 'event':
      return applyEvent(state, action.event)
  }
}

function applyEvent(state: RunState, e: RunEvent): RunState {
  switch (e.type) {
    case 'plan': {
      const nodes: Record<string, NodeProgress> = {}
      for (const n of e.data.nodes) nodes[n.id] = { status: 'pending', done: 0, items: e.data.items_total }
      return { ...state, plan: e.data, nodes }
    }
    case 'node': {
      const prev = state.nodes[e.data.node] ?? { status: 'pending', done: 0, items: 0 }
      const next: NodeProgress =
        e.data.status === 'done'
          ? { ...prev, status: 'done', done: e.data.report?.items_in ?? prev.items, report: e.data.report }
          : { ...prev, status: 'running', done: e.data.done ?? prev.done, items: e.data.items ?? prev.items }
      return { ...state, nodes: { ...state.nodes, [e.data.node]: next } }
    }
    case 'decision':
      return { ...state, recent: [e.data, ...state.recent].slice(0, RECENT_MAX) }
    case 'stats':
      return { ...state, stats: e.data }
    case 'done':
      return { ...state, status: 'done' }
    case 'error':
      return {
        ...state,
        status: e.data.fatal ? 'error' : state.status,
        errors: [...state.errors, e.data],
      }
  }
}
