/**
 * TypeScript mirrors of foreman/contracts.py, foreman/core/ledger.py and the MINH_PLAN §D
 * events. Field names match the Python exactly, so a shape change shows up as a type error.
 */

export type DecidedBy = 'jev' | 'llm' | 'code'

/** foreman/core/state.py ItemState */
export interface ItemState {
  id: string
  text: string
  fields: Record<string, unknown>
  meta: Record<string, unknown>
}

/** foreman/contracts.py DroppedItem */
export interface DroppedItem {
  id: string
  text: string
  fields: Record<string, unknown>
  reason: string
}

/** foreman/contracts.py NodeReport */
export interface NodeReport {
  node: string
  tool: string
  items_in: number
  items_out: number
  jev_decisions: number
  llm_decisions: number
  code_ops: number
  low_confidence: number
  duration_ms: number
  notes: string[]
}

/** foreman/core/ledger.py RunStats: the meter */
export interface RunStats {
  decisions_total: number
  by: Record<DecidedBy, number>
  low_confidence: number
  jev_cost_usd: number
  llm_cost_usd: number
  wall_time_s: number
  jev_calls: number
  llm_calls: number
}

/** foreman/core/ledger.py Decision: one ledger row (GET /runs/{id}/decisions) */
export interface Decision {
  run_id: string
  item_id: string
  tool: string
  question_id: string
  qtype: string
  value: unknown
  confidence: number | null
  probabilities: Record<string, number> | null
  decided_by: DecidedBy
  kind: 'decision' | 'proposal' | 'compute'
  jev_value: unknown
  jev_confidence: number | null
  low_confidence: boolean
  latency_ms: number | null
  cost_usd: number
  ts: number
}

/** One step of the plan, as sent in the `plan` event. `why` is the planner's reason. */
export interface PlanNode {
  id: string
  tool: string
  params: Record<string, unknown>
  why?: string
}

/** foreman/contracts.py ResultsFile (GET /runs/{id}/results) */
export interface ResultsFile {
  run_id: string
  job: string
  threshold: number
  started_at: string
  finished_at: string
  plan: { job?: string; nodes: PlanNode[] }
  items: ItemState[]
  dropped: DroppedItem[]
  /** Free-form; §D adds `summary: string` and `escalation_curve: [threshold, count][]`. */
  aggregates: Record<string, unknown> & {
    summary?: string
    escalation_curve?: [number, number][]
  }
  node_reports: NodeReport[]
  stats: RunStats
  decisions_file: string
}

// ---- §D SSE events ----

export interface PlanEvent {
  run_id: string
  job: string
  items_total: number
  nodes: PlanNode[]
}

export interface NodeEvent {
  node: string
  tool: string
  index: number
  total: number
  status: 'running' | 'done'
  /** Progress, only while running. */
  done?: number
  items?: number
  /** Only on `done`. */
  report?: NodeReport
}

/** A compact, sampled ledger row for the live ticker; totals always come from `stats`. */
export interface DecisionEvent {
  item_id: string
  node: string
  tool: string
  question_id: string
  qtype: string
  value: unknown
  confidence: number | null
  decided_by: DecidedBy
  jev_value: unknown
  jev_confidence: number | null
  low_confidence: boolean
  cost_usd: number
  ts: number
}

export interface DoneEvent {
  run_id: string
  results_url: string
}

/** `fatal: false` means the run continues. */
export interface ErrorEvent {
  message: string
  node: string | null
  fatal: boolean
}

export type RunEvent =
  | { type: 'plan'; data: PlanEvent }
  | { type: 'node'; data: NodeEvent }
  | { type: 'decision'; data: DecisionEvent }
  | { type: 'stats'; data: RunStats }
  | { type: 'done'; data: DoneEvent }
  | { type: 'error'; data: ErrorEvent }

export type ExportFormat = 'xlsx' | 'pdf' | 'docx'

/** What the UI needs from a backend; the real client and the mock both implement it. */
export interface ForemanApi {
  /** POST /runs. Resolves to the new run_id. */
  startRun(file: File | null, job: string, threshold: number): Promise<string>
  /** GET /runs/{id}/events. Returns an unsubscribe function. */
  subscribe(runId: string, onEvent: (e: RunEvent) => void): () => void
  getResults(runId: string): Promise<ResultsFile>
  getDecisions(runId: string, itemId: string): Promise<Decision[]>
  exportUrl(runId: string, format: ExportFormat): string
}
