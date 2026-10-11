import { useMemo, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ReferenceDot, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from 'recharts'
import type { RunStats } from '../../api/types'
import { fmtInt, fmtUsd } from '../../state/format'
import { AnimatedNumber } from '../LiveDemo/AnimatedNumber'
import { escalatedAt } from './model'

interface Props {
  curve: [number, number][]
  stats: RunStats
  runThreshold: number
}

const fmtPct = (n: number) => `${n.toFixed(1)}%`
const fmtApproxUsd = (n: number) => `≈ ${fmtUsd(n)}`

/**
 * The key demo moment (MINH_PLAN §C2): drag the bar and see, without re-running, how many
 * Jev decisions would have gone to the LLM and roughly what that costs. All the math is
 * here in code, from the ledger's escalation curve.
 */
export function ConfidenceCurve({ curve, stats, runThreshold }: Props) {
  const [t, setT] = useState(runThreshold)
  const data = useMemo(() => curve.map(([x, y]) => ({ x, y })), [curve])
  const modelDecisions = (stats.by.jev ?? 0) + (stats.by.llm ?? 0)
  const count = escalatedAt(curve, t)
  const share = modelDecisions ? (100 * count) / modelDecisions : 0
  const perLlm = stats.by.llm ? stats.llm_cost_usd / stats.by.llm : 0
  const extra = perLlm * count

  return (
    <div className="card curve">
      <div className="curve-head">
        <h3>Move the confidence bar</h3>
        <p className="muted">No re-run needed: every Jev answer's confidence is already in the ledger.</p>
      </div>
      <p className="curve-sentence" aria-live="polite">
        At <strong className="tabular">{t.toFixed(2)}</strong>,{' '}
        <strong className="by-llm big"><AnimatedNumber value={count} format={fmtInt} /></strong> of{' '}
        {fmtInt(modelDecisions)} model decisions (<AnimatedNumber value={share} format={fmtPct} />) would go to
        the LLM, for <AnimatedNumber value={extra} format={fmtApproxUsd} /> of LLM calls.
      </p>
      <div className="curve-chart" aria-hidden>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={data} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="curveFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--llm)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--llm)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="x" type="number" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickLine={false} axisLine={false} fontSize={12} />
            <YAxis tickLine={false} axisLine={false} fontSize={12} width={48} tickFormatter={(v: number) => fmtInt(v)} />
            <Area type="monotone" dataKey="y" stroke="var(--llm)" strokeWidth={2} fill="url(#curveFill)" isAnimationActive={false} />
            <ReferenceLine x={runThreshold} stroke="var(--muted)" strokeDasharray="4 4" label={{ value: 'this run', position: 'insideTopLeft', fontSize: 11, fill: 'var(--muted)' }} />
            <ReferenceLine x={t} stroke="var(--accent)" />
            <ReferenceDot x={t} y={count} r={6} fill="var(--accent)" stroke="#fff" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="slider-row">
        <label className="field-label" htmlFor="curve-threshold">Confidence bar</label>
        <input
          id="curve-threshold"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={t}
          onChange={(e) => setT(Number(e.target.value))}
        />
        <output htmlFor="curve-threshold" className="slider-value">{t.toFixed(2)}</output>
      </div>
      <p className="hint">You choose the trade-off between cost and certainty. Cost is estimated from this run's average LLM call.</p>
    </div>
  )
}
