import { motion, useReducedMotion } from 'motion/react'
import type { PlanEvent } from '../../api/types'
import { fmtInt } from '../../state/format'
import type { NodeProgress } from '../../state/run'

interface Props {
  plan: PlanEvent
  nodes: Record<string, NodeProgress>
}

/** The plan as steps; each lights up while it runs, then shows who decided what. */
export function PlanSteps({ plan, nodes }: Props) {
  const reduce = useReducedMotion()
  return (
    <ol className="steps">
      {plan.nodes.map((n, i) => {
        const p = nodes[n.id] ?? { status: 'pending', done: 0, items: 0 }
        const pct = p.items ? Math.min(100, (100 * p.done) / p.items) : 0
        const r = p.report
        return (
          <motion.li
            key={n.id}
            className={`card step ${p.status}`}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: reduce ? 0 : i * 0.08 }}
          >
            <div className="step-head">
              <span className="step-index">{i + 1}</span>
              <span className="step-tool">{n.tool}</span>
              <span className={`step-status ${p.status}`}>{p.status}</span>
            </div>
            {n.why && <p className="step-why">{n.why}</p>}
            <div className="progress" aria-hidden>
              <div className="progress-fill" style={{ transform: `scaleX(${pct / 100})` }} />
            </div>
            {r && (
              <p className="step-counts">
                {fmtInt(r.items_in)} in → {fmtInt(r.items_out)} out ·{' '}
                <span className="by-jev">{fmtInt(r.jev_decisions)} Jev</span> ·{' '}
                <span className="by-llm">{fmtInt(r.llm_decisions)} LLM</span> ·{' '}
                <span className="by-code">{fmtInt(r.code_ops)} code</span>
                {r.low_confidence > 0 && <> · {fmtInt(r.low_confidence)} low confidence</>}
              </p>
            )}
          </motion.li>
        )
      })}
    </ol>
  )
}
