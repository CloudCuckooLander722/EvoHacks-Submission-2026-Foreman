import type { ResultsFile } from '../../api/types'
import { fmtInt, fmtSeconds, fmtUsd } from '../../state/format'

/** The written summary (aggregates.summary, from foreman.format.summarize) and the headline numbers. */
export function Summary({ results }: { results: ResultsFile }) {
  const s = results.stats
  const itemsIn = results.node_reports[0]?.items_in ?? results.items.length
  const model = (s.by.jev ?? 0) + (s.by.llm ?? 0)
  const tiles = [
    { label: 'Items in', value: fmtInt(itemsIn) },
    { label: 'Kept', value: fmtInt(results.items.length) },
    { label: 'Decisions', value: fmtInt(s.decisions_total) },
    { label: 'By Jev', value: model ? `${((100 * (s.by.jev ?? 0)) / model).toFixed(1)}%` : '—', cls: 'by-jev' },
    { label: 'Cost', value: fmtUsd(s.jev_cost_usd + s.llm_cost_usd) },
    { label: 'Time', value: fmtSeconds(s.wall_time_s) },
  ]
  return (
    <div className="card summary">
      <p className="summary-text">{results.aggregates.summary ?? 'This run has no written summary.'}</p>
      <dl className="tiles">
        {tiles.map((t) => (
          <div key={t.label} className="tile">
            <dt>{t.label}</dt>
            <dd className={t.cls}>{t.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
