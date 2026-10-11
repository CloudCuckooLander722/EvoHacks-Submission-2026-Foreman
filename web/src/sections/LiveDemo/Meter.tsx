import type { RunStats } from '../../api/types'
import { fmtInt, fmtSeconds, fmtUsd } from '../../state/format'
import { AnimatedNumber } from './AnimatedNumber'

const ZERO: RunStats = {
  decisions_total: 0,
  by: { jev: 0, llm: 0, code: 0 },
  low_confidence: 0,
  jev_cost_usd: 0,
  llm_cost_usd: 0,
  wall_time_s: 0,
  jev_calls: 0,
  llm_calls: 0,
}

/** Decisions by Jev / LLM / code, plus cost and elapsed time: always straight from `stats`. */
export function Meter({ stats }: { stats: RunStats | null }) {
  const s = stats ?? ZERO
  const total = s.decisions_total || 1
  const cells = [
    { key: 'jev', label: 'Jev', n: s.by.jev ?? 0 },
    { key: 'llm', label: 'LLM', n: s.by.llm ?? 0 },
    { key: 'code', label: 'Code', n: s.by.code ?? 0 },
  ] as const

  return (
    <div className="card meter">
      <div className="meter-split" aria-hidden>
        {cells.map((c) => (
          <div key={c.key} className={`split-${c.key}`} style={{ flexGrow: c.n / total || 0.0001 }} />
        ))}
      </div>
      <dl className="meter-grid">
        {cells.map((c) => (
          <div key={c.key} className={`meter-cell by-${c.key}`}>
            <dt>{c.label}</dt>
            <dd><AnimatedNumber value={c.n} format={fmtInt} /></dd>
          </div>
        ))}
        <div className="meter-cell">
          <dt>Cost</dt>
          <dd><AnimatedNumber value={s.jev_cost_usd + s.llm_cost_usd} format={fmtUsd} /></dd>
        </div>
        <div className="meter-cell">
          <dt>Elapsed</dt>
          <dd><AnimatedNumber value={s.wall_time_s} format={fmtSeconds} /></dd>
        </div>
      </dl>
      <p className="hint">
        {fmtInt(s.decisions_total)} decisions
        {s.low_confidence > 0 && <> · {fmtInt(s.low_confidence)} kept at low confidence</>}
      </p>
    </div>
  )
}
