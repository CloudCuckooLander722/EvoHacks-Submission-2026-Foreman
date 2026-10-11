import { useVirtualizer } from '@tanstack/react-virtual'
import { useMemo, useRef, useState } from 'react'
import type { ItemState } from '../../api/types'
import { decidedBy, fmtCell } from './model'

export interface CellRef {
  item: ItemState
  field: string | null
}

interface Props {
  items: ItemState[]
  columns: string[]
  decided: Set<string>
  selected: CellRef | null
  onSelect: (cell: CellRef) => void
}

const ROW_H = 40
const ID_W = 96
const TEXT_W = 420
const COL_W = 150

/**
 * Every item, virtualized so 10,000 rows scroll smoothly. Decided cells are tinted by who
 * decided them; clicking one opens its "why" panel.
 */
export function ResultsTable({ items, columns, decided, selected, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter(
      (i) => i.id.toLowerCase().includes(q) || i.text.toLowerCase().includes(q) ||
        columns.some((c) => fmtCell(i.fields[c]).toLowerCase().includes(q)),
    )
  }, [items, columns, query])

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_H,
    overscan: 12,
  })
  const template = `${ID_W}px ${TEXT_W}px ${columns.map(() => `${COL_W}px`).join(' ')}`
  const width = ID_W + TEXT_W + COL_W * columns.length

  return (
    <div className="card table-card">
      <div className="table-tools">
        <input
          type="search"
          className="search"
          placeholder="Search rows"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search rows"
        />
        <span className="hint">{rows.length.toLocaleString('en-US')} rows · click a coloured cell to see why</span>
        <span className="legend">
          <span className="chip bg-jev">Jev</span>
          <span className="chip bg-llm">LLM</span>
          <span className="chip bg-code">Code</span>
          <span className="chip low">low confidence</span>
        </span>
      </div>
      <div className="table-scroll" ref={scroller} role="grid" aria-rowcount={rows.length}>
        <div className="table-head" style={{ gridTemplateColumns: template, width }} role="row">
          <span role="columnheader">id</span>
          <span role="columnheader">text</span>
          {columns.map((c) => <span key={c} role="columnheader" title={c}>{c}</span>)}
        </div>
        {rows.length === 0 && (
          <p className="placeholder">{query ? 'No rows match your search.' : 'No items were kept; see the Dropped sheet in the Excel export.'}</p>
        )}
        <div style={{ height: virtualizer.getTotalSize(), width, position: 'relative' }}>
          {virtualizer.getVirtualItems().map((v) => {
            const item = rows[v.index]
            return (
              <div
                key={item.id}
                className="table-row"
                role="row"
                style={{ gridTemplateColumns: template, transform: `translateY(${v.start}px)`, height: ROW_H }}
              >
                <span className="mono">{item.id}</span>
                <button
                  className={`cell text-cell${selected?.item.id === item.id && selected.field === null ? ' selected' : ''}`}
                  title={item.text}
                  onClick={() => onSelect({ item, field: null })}
                >
                  {item.text}
                </button>
                {columns.map((c) => {
                  const by = decided.has(c) ? decidedBy(item, c) : null
                  const value = fmtCell(item.fields[c])
                  if (!by) return <span key={c} className="cell plain" title={value}>{value}</span>
                  const low = item.fields[`${c}__low_confidence`] === true
                  const isSel = selected?.item.id === item.id && selected.field === c
                  return (
                    <button
                      key={c}
                      className={`cell bg-${by}${low ? ' low' : ''}${isSel ? ' selected' : ''}`}
                      title={`${value} · decided by ${by}`}
                      onClick={() => onSelect({ item, field: c })}
                    >
                      {value}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
