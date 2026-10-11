import type { ItemState, ResultsFile } from '../../api/types'
import { fmtCell, prettyLabel } from './model'
import type { CellRef } from './ResultsTable'

const SHOW = 25

interface Props {
  results: ResultsFile
  onSelect: (cell: CellRef) => void
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** The ranked bug backlog: aggregates.rank_order, one row per merged issue, most urgent first. */
export function Backlog({ results, onSelect }: Props) {
  const order = results.aggregates.rank_order
  if (!Array.isArray(order) || order.length === 0) return null
  const byId = new Map(results.items.map((i) => [i.id, i]))
  const rows = order.map((id) => byId.get(String(id))).filter((i): i is ItemState => !!i)
  const top = rows.slice(0, SHOW)

  return (
    <div className="card backlog">
      <h3 className="backlog-title">
        Ranked bug backlog <span className="muted">· top {top.length} of {rows.length} issues · click a row for the "why"</span>
      </h3>
      <ol className="backlog-list">
        {top.map((item, n) => {
          const f = item.fields
          const reported = num(f.dup_group_count) ?? 1
          const urgency = num(f.urgency)
          const theme = typeof f.theme === 'string' ? prettyLabel(f.theme) : null
          return (
            <li key={item.id}>
              <button type="button" className="backlog-row" onClick={() => onSelect({ item, field: null })}>
                <span className="backlog-rank tabular">{n + 1}</span>
                <span className="backlog-text">{item.text}</span>
                <span className="backlog-meta">
                  {theme && <span className="pill">{theme}</span>}
                  <span className="pill strong">reported by {reported}</span>
                  {urgency !== null && <span className="muted tabular">urgency {fmtCell(Math.round(urgency))}</span>}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
