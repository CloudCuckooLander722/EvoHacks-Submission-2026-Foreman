// TODO(nathan): Oafer landing link
import { motion, useReducedMotion } from 'motion/react'
import { HERO_RUN } from '../../content/numbers'

/** Set to the recorded demo (MINH_PLAN §E), e.g. `${import.meta.env.BASE_URL}demo.mp4`. */
const DEMO_VIDEO: string | null = null

function VideoPlaceholder() {
  return (
    <div className="video-placeholder" role="img" aria-label="Demo video coming soon">
      <div className="vp-bar">
        <span /><span /><span />
      </div>
      <div className="vp-body">
        <div className="vp-line w70" />
        <div className="vp-line w40" />
        <div className="vp-steps">
          {['filter', 'classify', 'group', 'dedupe', 'score', 'rank', 'calculate'].map((t, i) => (
            <span key={t} className="vp-step" style={{ animationDelay: `${i * 0.6}s` }}>{t}</span>
          ))}
        </div>
        <p className="muted">Demo video coming soon. Try the live demo below.</p>
      </div>
    </div>
  )
}

/** Video, one-line headline, subline, Try it (MINH_PLAN §C3). */
export function Hero() {
  const reduce = useReducedMotion()
  const rise = (delay: number) =>
    reduce ? {} : { initial: { opacity: 0, y: 16 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.6, delay } }

  return (
    <header className="hero">
      <motion.p className="eyebrow" {...rise(0)}>Foreman · AI for tedious bulk work</motion.p>
      <motion.h1 {...rise(0.05)}>
        Tedious bulk work, done fast and cheap, with a confidence score on every decision.
      </motion.h1>
      <motion.p className="lede" {...rise(0.12)}>
        Describe the job in plain English. A light LLM plans it once, a fast calibrated model decides every
        item, and only the unsure cases get a second opinion.
      </motion.p>
      <motion.div className="hero-actions" {...rise(0.18)}>
        <a className="button primary" href="#demo">Try it</a>
        <a className="button ghost" href="#how">How it works</a>
      </motion.div>
      <motion.div className="hero-media" {...rise(0.25)}>
        {DEMO_VIDEO ? (
          <video src={DEMO_VIDEO} autoPlay muted loop playsInline controls />
        ) : (
          <VideoPlaceholder />
        )}
      </motion.div>
      <dl className="hero-numbers">
        {HERO_RUN.map((s) => (
          <div key={s.label}>
            <dt>{s.label}</dt>
            <dd>{s.value}</dd>
            {s.note && <p className="hint">{s.note}</p>}
          </div>
        ))}
      </dl>
      <p className="hint center">A real run on 10,000 public app reviews, threshold 0.7.</p>
    </header>
  )
}
