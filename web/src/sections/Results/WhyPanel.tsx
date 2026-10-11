import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import type { Decision, PlanNode } from '../../api/types'
import { fmtUsd } from '../../state/format'
import { fmtCell, nodeForField, questionText } from './model'
import type { CellRef } from './ResultsTable'

interface Props {
  runId: string
  nodes: PlanNode[]
  cell: CellRef | null
  onClose: () => void
}

const WHO: Record<string, string> = { jev: 'Jev', llm: 'LLM', code: 'Code' }

function fmtAnswer(v: unknown): string {
  if (typeof v === 'number' && !Number.isInteger(v)) return v.toFixed(2)
  return fmtCell(v)
}

function Probabilities({ probs }: { probs: Record<string, number> }) {
  const top = Object.entries(probs).sort((a, b) => b[1] - a[1]).slice(0, 5)
  return (
    <ul className="probs">
      {top.map(([k, p]) => (
        <li key={k}>
          <span className="probs-label" title={k}>{k}</span>
          <span className="probs-bar"><span style={{ transform: `scaleX(${p})` }} /></span>
          <span className="tabular muted">{p.toFixed(2)}</span>
        </li>
      ))}
    </ul>
  )
}

function DecisionCard({ d, node, itemId }: { d: Decision; node: PlanNode | null; itemId: string }) {
  const question = questionText(node, d.question_id)
  const other = d.item_id.includes('|') ? d.item_id.split('|').filter((id) => id !== itemId).join(', ') : null
  const overrode = d.decided_by === 'llm' && d.jev_value !== null && d.jev_value !== undefined
  return (
    <li className={`card why-decision border-${d.decided_by}`}>
      <div className="why-row">
        <span className={`chip bg-${d.decided_by}`}>{WHO[d.decided_by]}</span>
        <span className="mono muted">{d.tool} · {d.question_id}</span>
        {d.confidence !== null && <span className="tabular why-conf">{d.confidence.toFixed(2)}</span>}
      </div>
      {question && <p className="why-question">“{question}”</p>}
      {other && <p className="hint">Compared with <span className="mono">{other}</span></p>}
      <p className="why-answer">
        Answer: <strong>{fmtAnswer(d.value)}</strong>
        {d.low_confidence && <span className="low-flag"> · low confidence</span>}
      </p>
      {overrode && (
        <p className="why-override">
          Jev said <strong>{fmtAnswer(d.jev_value)}</strong> at confidence{' '}
          <strong>{d.jev_confidence?.toFixed(2) ?? '—'}</strong>, below the bar, so the LLM gave a second opinion.
        </p>
      )}
      {d.probabilities && Object.keys(d.probabilities).length > 1 && <Probabilities probs={d.probabilities} />}
      <p className="hint">
        {d.latency_ms !== null && <>{d.latency_ms} ms · </>}
        {fmtUsd(d.cost_usd)}
      </p>
    </li>
  )
}

/** "Why is this cell `bug`?": the ledger rows behind one cell, with Jev's original answer. */
export function WhyPanel({ runId, nodes, cell, onClose }: Props) {
  const reduce = useReducedMotion()
  // Keyed by item, so switching cells never shows the previous item's rows.
  const [loaded, setLoaded] = useState<{ id: string; rows?: Decision[]; error?: string } | null>(null)
  const itemId = cell?.item.id
  const current = loaded && loaded.id === itemId ? loaded : null
  const rows = current?.rows ?? null
  const error = current?.error ?? null

  useEffect(() => {
    if (!itemId) return
    let live = true
    api.getDecisions(runId, itemId).then(
      (r) => live && setLoaded({ id: itemId, rows: r }),
      (e: unknown) => live && setLoaded({ id: itemId, error: e instanceof Error ? e.message : String(e) }),
    )
    return () => {
      live = false
    }
  }, [runId, itemId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const node = cell?.field ? nodeForField(nodes, cell.field) : null
  const shown = rows && node ? rows.filter((d) => d.tool === node.tool) : rows
  const field = cell?.field
  const by = field ? cell?.item.fields[`${field}__by`] : null
  const conf = field ? cell?.item.fields[`${field}__confidence`] : null

  return (
    <AnimatePresence>
      {cell && (
        <motion.aside
          className="why"
          role="dialog"
          aria-label={`Why: ${cell.item.id}`}
          initial={reduce ? false : { x: 40, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={reduce ? { opacity: 0 } : { x: 40, opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <div className="why-top">
            <h3>Why?</h3>
            <button className="icon-button" onClick={onClose} aria-label="Close">×</button>
          </div>
          <p className="mono muted">{cell.item.id}</p>
          <blockquote className="why-text">{cell.item.text}</blockquote>
          {field && (
            <p className="why-cell">
              <span className="muted">{field}</span> = <strong>{fmtCell(cell.item.fields[field])}</strong>
              {typeof by === 'string' && <> · decided by <strong className={`by-${by}`}>{WHO[by] ?? by}</strong></>}
              {typeof conf === 'number' && <> at {conf.toFixed(2)}</>}
            </p>
          )}
          {error && <p className="error card">{error}</p>}
          {!rows && !error && <p className="muted">Loading the ledger…</p>}
          {shown && shown.length === 0 && (
            <p className="muted">
              {by === 'code' ? 'Computed in code; no model was asked.' : 'No ledger rows for this cell.'}
            </p>
          )}
          {shown && shown.length > 0 && (
            <ul className="why-list">
              {shown.map((d, i) => <DecisionCard key={`${d.question_id}-${d.ts}-${i}`} d={d} itemId={cell.item.id} node={nodes.find((n) => n.tool === d.tool) ?? null} />)}
            </ul>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
