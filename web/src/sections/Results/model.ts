/**
 * Pure helpers for the results view. The chart rules mirror foreman/format/charts.py:
 * aggregates are charted by their shape, not their name, so new tools and plans just work.
 */
import type { DecidedBy, ItemState, PlanNode, ResultsFile } from '../../api/types'

export interface Bar {
  label: string
  value: number
}

export interface Chart {
  key: string
  title: string
  bars: Bar[]
  split?: boolean
}

const SKIP = new Set(['escalation_curve', 'summary'])
const LABEL_KEYS = ['theme', 'name', 'label', 'category', 'group', 'key']
const MAX_BARS = 15

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** `theme_counts` -> `Theme counts` */
export function titleOf(key: string): string {
  const words = key.replaceAll('__', ' ').replaceAll('_', ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The bars for one aggregate, or null when its shape is not chartable. */
export function barsOf(value: unknown): Bar[] | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value)
    if (entries.length >= 2 && entries.every(([, v]) => isNum(v))) {
      return entries.map(([label, v]) => ({ label: prettyLabel(label), value: v as number }))
    }
    return null
  }
  if (Array.isArray(value) && value.length > 0) {
    const rows = value as Record<string, unknown>[]
    if (!rows.every((r) => r && typeof r === 'object' && !Array.isArray(r))) return null
    const label = LABEL_KEYS.find((k) => typeof rows[0][k] === 'string')
    if (!label || !rows.every((r) => typeof r[label] === 'string' && isNum(r.count))) return null
    return rows.map((r) => ({ label: prettyLabel(r[label] as string), value: r.count as number }))
  }
  return null
}

/** The Jev / LLM / code split first, then every chartable aggregate, duplicates dropped. */
export function findCharts(results: ResultsFile): Chart[] {
  const by = results.stats.by
  const charts: Chart[] = [
    {
      key: 'decided_by',
      title: 'Decisions by Jev / LLM / code',
      split: true,
      bars: (['jev', 'llm', 'code'] as const).map((k) => ({ label: k, value: by[k] ?? 0 })),
    },
  ]
  const seen = new Set<string>()
  for (const [key, value] of Object.entries(results.aggregates)) {
    if (SKIP.has(key)) continue
    const bars = barsOf(value)
    if (!bars) continue
    const top = [...bars].sort((a, b) => b.value - a.value).slice(0, MAX_BARS)
    const sig = JSON.stringify([...top].sort((a, b) => a.label.localeCompare(b.label)))
    if (seen.has(sig)) continue
    seen.add(sig)
    charts.push({ key, title: titleOf(key), bars: top })
  }
  return charts
}

/** `type__by`, `type__probs`... are bookkeeping, not a column of their own. */
export const isMetaField = (name: string) => name.includes('__')

/** Visible fields in first-seen order: input columns, then each tool's outputs. */
export function columnsOf(items: ItemState[]): string[] {
  const seen = new Set<string>()
  for (const item of items) for (const k of Object.keys(item.fields)) if (!isMetaField(k)) seen.add(k)
  // The source text column is already shown as the item's text.
  const first = items[0]
  return [...seen].filter((k) => !first || first.fields[k] !== first.text)
}

/** Fields some tool decided: they carry a `__by` companion. */
export function decidedFields(items: ItemState[]): Set<string> {
  const out = new Set<string>()
  for (const item of items) {
    for (const k of Object.keys(item.fields)) if (k.endsWith('__by')) out.add(k.slice(0, -4))
  }
  return out
}

/** Ranked items first, in rank order, when the run ranked anything. */
export function orderedItems(results: ResultsFile): ItemState[] {
  const order = results.aggregates.rank_order
  if (!Array.isArray(order) || order.length === 0) return results.items
  const pos = new Map(order.map((id, n) => [String(id), n]))
  const ranked = results.items.filter((i) => pos.has(i.id)).sort((a, b) => pos.get(a.id)! - pos.get(b.id)!)
  return [...ranked, ...results.items.filter((i) => !pos.has(i.id))]
}

export function decidedBy(item: ItemState, field: string): DecidedBy | null {
  const by = item.fields[`${field}__by`]
  return by === 'jev' || by === 'llm' || by === 'code' ? by : null
}

/** The plan step that wrote `field`: its `output_field` is the field or a prefix of it. */
export function nodeForField(nodes: PlanNode[], field: string): PlanNode | null {
  let best: PlanNode | null = null
  for (const n of nodes) {
    const out = n.params.output_field
    if (typeof out !== 'string') continue
    if (field === out || field.startsWith(`${out}_`)) {
      if (!best || out.length > String(best.params.output_field).length) best = n
    }
  }
  return best
}

/** The question a step asked, in words, when its params say it plainly. */
export function questionText(node: PlanNode | null, questionId: string): string | null {
  if (!node) return null
  const p = node.params
  if (typeof p.condition === 'string') return p.condition
  if (typeof p.instructions === 'string') return p.instructions
  if (questionId.startsWith('dim_') && Array.isArray(p.dimensions)) {
    const dim = (p.dimensions as Record<string, unknown>[]).find((d) => `dim_${d.name}` === questionId)
    if (dim && typeof dim.instructions === 'string') return dim.instructions
  }
  if (typeof p.criterion === 'string') return p.criterion
  return null
}

/** Escalation count at threshold `t` from the 21-step curve (exact on its 0.05 grid). */
export function escalatedAt(curve: [number, number][], t: number): number {
  if (curve.length === 0) return 0
  let prev = curve[0]
  for (const point of curve) {
    if (Math.abs(point[0] - t) < 1e-9) return point[1]
    if (point[0] > t) {
      const f = (t - prev[0]) / (point[0] - prev[0] || 1)
      return prev[1] + f * (point[1] - prev[1])
    }
    prev = point
  }
  return curve.at(-1)![1]
}

/** Cell display: numbers trimmed, booleans as yes/no, nested values as compact JSON. */
/** Jev's escape option reads as "Other" to people. */
export const prettyLabel = (label: string) => (label === 'none_of_these' ? 'Other' : label)

export function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (v === 'none_of_these') return 'Other'
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString('en-US') : String(Math.round(v * 1000) / 1000)
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}
