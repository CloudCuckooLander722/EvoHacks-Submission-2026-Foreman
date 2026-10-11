import { useEffect, useReducer, useRef, useState } from 'react'
import { api, checkApi, setMode, type Mode } from '../../api'
import { API_DOWN } from '../../api/client'
import type { ResultsFile } from '../../api/types'
import { Results } from '../Results/Results'
import { initialRun, runReducer } from '../../state/run'
import { JobComposer } from './JobComposer'
import { Meter } from './Meter'
import { PlanSteps } from './PlanSteps'

function fmtValue(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
  if (typeof v === 'string' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

/** Composer → plan steps + meter, driven by the §D event stream (real or replayed). */
export function LiveDemo() {
  const [run, dispatch] = useReducer(runReducer, initialRun)
  const unsubscribe = useRef<(() => void) | null>(null)

  const [results, setResults] = useState<ResultsFile | null>(null)
  const [resultsError, setResultsError] = useState<string | null>(null)

  const [apiOk, setApiOk] = useState<boolean | null>(null)
  const [mode, setModeState] = useState<Mode>('live')

  useEffect(() => () => unsubscribe.current?.(), [])
  useEffect(() => {
    void checkApi().then((h) => setApiOk(h.ok))
  }, [])

  // The run is done: fetch results.json for the results view.
  useEffect(() => {
    if (run.status !== 'done' || !run.runId) return
    let live = true
    api.getResults(run.runId).then(
      (r) => live && setResults(r),
      (e: unknown) => live && setResultsError(e instanceof Error ? e.message : String(e)),
    )
    return () => {
      live = false
    }
  }, [run.status, run.runId])

  const begin = async (m: Mode, file: File | null, job: string, threshold: number, plan?: string) => {
    unsubscribe.current?.()
    setMode(m)
    setModeState(m)
    setResults(null)
    setResultsError(null)
    dispatch({ type: 'start' })
    try {
      const runId = await api.startRun(file, job, threshold, plan)
      dispatch({ type: 'started', runId })
      unsubscribe.current = api.subscribe(runId, (event) => dispatch({ type: 'event', event }))
    } catch (e) {
      let message = e instanceof Error ? e.message : String(e)
      if (m === 'live' && !(await checkApi()).ok) {
        setApiOk(false)
        message = API_DOWN
      }
      dispatch({ type: 'failed', message })
    }
  }
  const start = (file: File | null, job: string, threshold: number, plan: string) =>
    begin('live', file, job, threshold, plan || undefined)
  const replay = (threshold: number) =>
    begin('replay', null, 'Turn these app reviews into a ranked bug backlog', threshold)

  const busy = run.status === 'starting' || run.status === 'running'

  return (
    <>
    <section id="demo" className="section">
      <h2 className="section-title">Try it</h2>
      <div className="demo-grid">
        <div className="demo-left">
          <JobComposer busy={busy} apiOk={apiOk} onSubmit={start} onReplay={replay} />
          {mode === 'replay' && run.status !== 'idle' && (
            <p className="hint">Replaying the recorded 1,000-review run (sped up; no API or keys used).</p>
          )}
          {run.errors.map((e, i) => (
            <p key={i} className={`card error${e.fatal ? ' fatal' : ''}`} role="alert">
              {e.node && <strong>{e.node}: </strong>}
              {e.message}
            </p>
          ))}
        </div>
        <div className="demo-right">
          <Meter stats={run.stats} />
          {run.plan ? (
            <PlanSteps plan={run.plan} nodes={run.nodes} />
          ) : (
            <p className="card placeholder">
              {run.status === 'starting' ? 'Planning…' : 'The plan appears here once you press Run.'}
            </p>
          )}
          {run.recent.length > 0 && (
            <ul className="card ticker" aria-label="Recent decisions">
              {run.recent.map((d) => (
                <li key={`${d.item_id}-${d.question_id}-${d.ts}`}>
                  <span className={`dot by-${d.decided_by}`} />
                  <span className="mono">{d.item_id}</span>
                  <span className="muted">{d.question_id}</span>
                  <span title={fmtValue(d.value)}>{fmtValue(d.value)}</span>
                  {d.confidence !== null && <span className="muted">{d.confidence.toFixed(2)}</span>}
                </li>
              ))}
            </ul>
          )}
          {run.status === 'done' && (
            <p className="hint">
              Run complete. {results ? <a href="#results">See the results ↓</a> : resultsError ?? 'Loading results…'}
            </p>
          )}
        </div>
      </div>
    </section>
    {results && <Results results={results} />}
    </>
  )
}
