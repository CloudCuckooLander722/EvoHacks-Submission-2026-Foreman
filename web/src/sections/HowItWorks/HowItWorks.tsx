import { motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'
import { DEMO_RUN } from '../../content/numbers'
import { TOOLS } from '../../content/tools'

/** A tiny seeded sequence so the visuals look the same on every load. */
function seeded(n: number, seed = 7): number[] {
  const out: number[] = []
  let x = seed
  for (let i = 0; i < n; i++) {
    x = (x * 9301 + 49297) % 233280
    out.push(x / 233280)
  }
  return out
}

const CONF = seeded(96).map((r) => 0.35 + 0.65 * Math.sqrt(r))

type Target = Record<string, string | number>

/**
 * Props for a reveal-on-scroll. With reduced motion the element renders straight at its
 * final state (`initial={false}` + `animate`), so nothing moves or fades.
 */
function useReveal() {
  const reduce = useReducedMotion()
  return (from: Target, to: Target, delay = 0, duration?: number) =>
    reduce
      ? { initial: false as const, animate: to }
      : { initial: from, whileInView: to, viewport: { once: true }, transition: { delay, duration } }
}

function PlanVisual() {
  const reveal = useReveal()
  const steps = ['filter', 'classify', 'group', 'dedupe', 'score', 'rank', 'calculate']
  return (
    <div className="viz viz-plan">
      <div className="bubble">“Turn these reviews into a backlog: drop spam, split bugs from requests, group into themes…”</div>
      <div className="arrow" aria-hidden>↓</div>
      <ol className="plan-chips">
        {steps.map((s, i) => (
          <motion.li key={s} {...reveal({ opacity: 0, x: -8 }, { opacity: 1, x: 0 }, 0.08 * i)}>
            <span className="step-index">{i + 1}</span>{s}
          </motion.li>
        ))}
      </ol>
    </div>
  )
}

function DecideVisual() {
  const reveal = useReveal()
  return (
    <div className="viz viz-grid" aria-hidden>
      {CONF.map((_, i) => (
        <motion.span
          key={i}
          className="cell-dot"
          {...reveal({ backgroundColor: 'var(--code-bg)' }, { backgroundColor: 'var(--jev)' }, 0.012 * i, 0.3)}
        />
      ))}
    </div>
  )
}

function EscalateVisual() {
  const reveal = useReveal()
  const bar = 0.7
  return (
    <div className="viz viz-escalate" aria-hidden>
      <div className="esc-plot">
        <div className="esc-line" style={{ bottom: `${bar * 92}%` }}><span>confidence bar 0.70</span></div>
        {CONF.slice(0, 48).map((c, i) => (
          <motion.span
            key={i}
            className={`esc-dot ${c < bar ? 'below' : 'above'}`}
            style={{ left: `${(i / 47) * 96 + 2}%`, bottom: `${c * 92}%` }}
            {...reveal({ opacity: 0, scale: 0.4 }, { opacity: 1, scale: 1 }, 0.015 * i)}
          />
        ))}
      </div>
      <p className="hint"><span className="dot by-jev inline" /> Jev keeps it · <span className="dot by-llm inline" /> the LLM takes a second look</p>
    </div>
  )
}

function LedgerVisual() {
  const reveal = useReveal()
  const rows = [
    { id: 'r-291', q: 'type', v: 'bug', c: '0.96', by: 'jev' },
    { id: 'r-291', q: 'dup_group', v: 'dup-3', c: '0.76', by: 'llm' },
    { id: 'r-1341', q: 'urgency', v: '97.0', c: '0.95', by: 'jev' },
    { id: 'r-291', q: 'rank', v: '1', c: '1.00', by: 'code' },
  ] as const
  return (
    <ul className="viz viz-ledger">
      {rows.map((r, i) => (
        <motion.li key={i} {...reveal({ opacity: 0, y: 6 }, { opacity: 1, y: 0 }, 0.12 * i)}>
          <span className="mono">{r.id}</span>
          <span className="muted">{r.q}</span>
          <strong>{r.v}</strong>
          <span className="tabular muted">{r.c}</span>
          <span className={`chip bg-${r.by}`}>{r.by === 'jev' ? 'Jev' : r.by === 'llm' ? 'LLM' : 'Code'}</span>
        </motion.li>
      ))}
    </ul>
  )
}

const STEPS: { title: string; body: string; visual: ReactNode }[] = [
  {
    title: 'Plan once.',
    body: 'A light LLM turns your request into a plan of tools: filter, classify, group, dedupe, score, rank, calculate. One call, before any item is touched.',
    visual: <PlanVisual />,
  },
  {
    title: 'Decide everywhere.',
    body: 'Jev, a fast and calibrated decision model, answers typed questions about every item, in parallel, at about $0.04 per million tokens. Math, counting and dates are done in code.',
    visual: <DecideVisual />,
  },
  {
    title: 'Escalate only when unsure.',
    body: 'Every Jev answer comes with a confidence. Below the bar you set, a light LLM gives one second opinion. Everything above it stays fast and cheap.',
    visual: <EscalateVisual />,
  },
  {
    title: 'Show the work.',
    body: 'Every decision is logged with who made it and how sure it was. Click any cell in the results to see the question, the answer and, if the LLM stepped in, what Jev said first.',
    visual: <LedgerVisual />,
  },
]

/** Four scroll-driven sections, the tool strip and the live-run numbers (MINH_PLAN §C3). */
export function HowItWorks() {
  const reveal = useReveal()({ opacity: 0, y: 24 }, { opacity: 1, y: 0 }, 0, 0.6)

  return (
    <section id="how" className="section how">
      <h2 className="section-title center">How Foreman works</h2>
      {STEPS.map((s, i) => (
        <motion.article key={s.title} className={`how-step${i % 2 ? ' flip' : ''}`} {...reveal}>
          <div className="how-copy">
            <p className="eyebrow">{i + 1} / 4</p>
            <h3>{s.title}</h3>
            <p>{s.body}</p>
          </div>
          <div className="how-visual">{s.visual}</div>
        </motion.article>
      ))}

      <h3 className="subhead center">The toolkit</h3>
      <div className="tool-strip">
        {TOOLS.map((t) => (
          <div key={t.name} className={`card tool${t.ready ? '' : ' soon'}`}>
            <div className="tool-head">
              <strong>{t.name}</strong>
              <span className={`chip ${t.uses === 'code' ? 'bg-code' : 'bg-jev'}`}>{t.uses === 'code' ? 'code' : 'Jev'}</span>
            </div>
            <p className="muted">{t.does}</p>
            {!t.ready && <p className="hint">next</p>}
          </div>
        ))}
      </div>

      <h3 className="subhead center">The run you can replay below</h3>
      <dl className="numbers">
        {DEMO_RUN.map((s) => (
          <div key={s.label}>
            <dd>{s.value}</dd>
            <dt>{s.label}</dt>
          </div>
        ))}
      </dl>
    </section>
  )
}
