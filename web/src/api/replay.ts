import type {
  Decision,
  DecisionEvent,
  NodeReport,
  PlanNode,
  ResultsFile,
  RunEvent,
  RunStats,
} from './types'

/** One scheduled event: `at` is milliseconds after subscribing. */
export interface Timed {
  at: number
  event: RunEvent
}

const TICK_MS = 250 // §D: `stats` about every 250 ms
const MAX_DECISIONS_PER_S = 20 // §D: the ticker is sampled to at most ~20/s
const PLAN_LEAD_MS = 600

/** The compact §D `decision` event for one ledger row. */
export function toDecisionEvent(d: Decision, node: string): DecisionEvent {
  return {
    item_id: d.item_id,
    node,
    tool: d.tool,
    question_id: d.question_id,
    qtype: d.qtype,
    value: d.value,
    confidence: d.confidence,
    decided_by: d.decided_by,
    jev_value: d.jev_value,
    jev_confidence: d.jev_confidence,
    low_confidence: d.low_confidence,
    cost_usd: d.cost_usd,
    ts: d.ts,
  }
}

/**
 * Ledger rows have a `tool` but no node id, so rows go to the first plan node with that
 * tool. A plan that uses one tool twice would credit both steps' rows to the first.
 */
function decisionsByNode(nodes: PlanNode[], decisions: Decision[]): Map<string, Decision[]> {
  const out = new Map<string, Decision[]>(nodes.map((n) => [n.id, []]))
  const nodeOfTool = new Map<string, string>()
  for (const n of [...nodes].reverse()) nodeOfTool.set(n.tool, n.id)
  for (const d of [...decisions].sort((a, b) => a.ts - b.ts)) {
    const id = nodeOfTool.get(d.tool)
    if (id) out.get(id)!.push(d)
  }
  return out
}

/** Meter values once `seen` of the run's decisions have happened. */
function partialStats(final: RunStats, seen: Decision[], total: number): RunStats {
  const f = total ? seen.length / total : 1
  const by = { jev: 0, llm: 0, code: 0 }
  for (const d of seen) by[d.decided_by] += 1
  return {
    decisions_total: seen.length,
    by,
    low_confidence: seen.filter((d) => d.low_confidence).length,
    // Costs, calls and time aren't per-row in the ledger, so they grow in proportion.
    jev_cost_usd: final.jev_cost_usd * f,
    llm_cost_usd: final.llm_cost_usd * f,
    wall_time_s: final.wall_time_s * f,
    jev_calls: Math.round(final.jev_calls * f),
    llm_calls: Math.round(final.llm_calls * f),
  }
}

/**
 * Turn a finished run into the event stream the real API would have sent (MINH_PLAN §C1):
 * `plan`, then per node `node` running → `decision`s → `stats` ticks → `node` done, then
 * `done`. The last `stats` is exactly `results.stats`, so the meter ends on the real totals.
 */
export function buildReplay(
  results: ResultsFile,
  decisions: Decision[],
  durationMs = 20_000,
): Timed[] {
  const nodes = results.plan.nodes
  const reports = new Map<string, NodeReport>(results.node_reports.map((r) => [r.node, r]))
  const byNode = decisionsByNode(nodes, decisions)
  const total = [...byNode.values()].reduce((s, ds) => s + ds.length, 0)
  const itemsTotal = results.node_reports[0]?.items_in ?? results.items.length

  const out: Timed[] = [
    {
      at: 0,
      event: {
        type: 'plan',
        data: { run_id: results.run_id, job: results.job, items_total: itemsTotal, nodes },
      },
    },
  ]

  // Busy nodes get more of the timeline, but even a pure-code node stays visible.
  const floor = Math.max(1, total / (nodes.length * 3))
  const weights = nodes.map((n) => Math.max(floor, byNode.get(n.id)!.length))
  const weightSum = weights.reduce((a, b) => a + b, 0)
  const span = Math.max(durationMs - PLAN_LEAD_MS, nodes.length)

  const seen: Decision[] = []
  let t0 = PLAN_LEAD_MS
  nodes.forEach((node, index) => {
    const len = (span * weights[index]) / weightSum
    const t1 = t0 + len
    const report = reports.get(node.id)
    const items = report?.items_in ?? itemsTotal
    const base = { node: node.id, tool: node.tool, index, total: nodes.length }
    out.push({ at: t0, event: { type: 'node', data: { ...base, status: 'running', done: 0, items } } })

    const ds = byNode.get(node.id)!
    const stride = Math.max(1, Math.ceil(ds.length / ((len / 1000) * MAX_DECISIONS_PER_S)))
    const timed = ds.map((d, k) => ({ at: t0 + ((k + 0.5) / ds.length) * len, d }))
    timed.forEach(({ at, d }, k) => {
      if (k % stride === 0) out.push({ at, event: { type: 'decision', data: toDecisionEvent(d, node.id) } })
    })

    let k = 0
    for (let at = t0 + TICK_MS; at < t1; at += TICK_MS) {
      while (k < timed.length && timed[k].at <= at) seen.push(timed[k++].d)
      const done = Math.round((items * (at - t0)) / len)
      out.push({ at, event: { type: 'node', data: { ...base, status: 'running', done, items } } })
      out.push({ at, event: { type: 'stats', data: partialStats(results.stats, seen, total) } })
    }
    while (k < timed.length) seen.push(timed[k++].d)
    out.push({ at: t1, event: { type: 'node', data: { ...base, status: 'done', report } } })
    t0 = t1
  })

  out.push({ at: durationMs, event: { type: 'stats', data: results.stats } })
  out.push({
    at: durationMs,
    event: {
      type: 'done',
      data: { run_id: results.run_id, results_url: `/runs/${results.run_id}/results` },
    },
  })
  // Stable sort keeps same-time events in the order pushed above.
  return out.sort((a, b) => a.at - b.at)
}
