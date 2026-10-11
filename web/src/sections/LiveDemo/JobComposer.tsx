import { useRef, useState, type DragEvent, type FormEvent } from 'react'
export const DEMO_JOB = 'Turn these app reviews into a ranked bug backlog'

/** Saved plans in data/plans/; '' lets the LLM planner write a plan from the job text. */
const PLANS = [
  { value: 'reviews_backlog', label: 'Saved plan: reviews_backlog (fast, tuned)' },
  { value: '', label: 'Let the LLM plan it from the job text' },
]

interface Props {
  busy: boolean
  /** null while /health is being checked. */
  apiOk: boolean | null
  onSubmit: (file: File | null, job: string, threshold: number, plan: string) => void
  onReplay: (threshold: number) => void
}

/** The job textarea, a drag-and-drop file target and the confidence bar (MINH_PLAN §C1). */
export function JobComposer({ busy, apiOk, onSubmit, onReplay }: Props) {
  const [job, setJob] = useState(DEMO_JOB)
  const [file, setFile] = useState<File | null>(null)
  const [threshold, setThreshold] = useState(0.7)
  const [plan, setPlan] = useState(PLANS[0].value)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const canSubmit = !busy && apiOk === true && job.trim().length > 0 && !!file

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) setFile(dropped)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (canSubmit) onSubmit(file, job.trim(), threshold, plan)
  }

  return (
    <form className="card composer" onSubmit={submit}>
      <label className="field-label" htmlFor="job">What should Foreman do?</label>
      <textarea id="job" rows={3} value={job} onChange={(e) => setJob(e.target.value)} disabled={busy} />

      <div
        className={`dropzone${dragging ? ' dragging' : ''}${file ? ' has-file' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
      >
        <input
          ref={input}
          type="file"
          hidden
          accept=".csv,.xlsx,.json,.txt,.pdf,.docx"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <span><strong>{file.name}</strong> · {(file.size / 1024).toFixed(0)} KB</span>
        ) : (
          <span>Drop a csv, xlsx, json, txt, pdf or docx file here, or click to choose</span>
        )}
      </div>
      <p className="hint">
        Try <a href={`${import.meta.env.BASE_URL}samples/demo_300.csv`} download onClick={(e) => e.stopPropagation()}>demo_300.csv</a>{' '}
        (300 real Medium app reviews) or any csv / items.json.
      </p>

      <label className="field-label" htmlFor="plan">Plan</label>
      <select id="plan" value={plan} onChange={(e) => setPlan(e.target.value)} disabled={busy}>
        {PLANS.map((p) => (
          <option key={p.value} value={p.value}>{p.label}</option>
        ))}
      </select>

      <div className="slider-row">
        <label className="field-label" htmlFor="threshold">Confidence bar</label>
        <input
          id="threshold"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          disabled={busy}
        />
        <output htmlFor="threshold" className="slider-value">{threshold.toFixed(2)}</output>
      </div>
      <p className="hint">Jev answers below this confidence get a second opinion from the LLM.</p>

      {apiOk === false && (
        <p className="card error" role="alert">
          The Foreman API isn't reachable on localhost:8000, so live runs are off. Start it with{' '}
          <code>scripts/demo.sh</code>, or replay a recorded run below (no keys needed).
        </p>
      )}
      <div className="hero-actions">
        <button className="primary" type="submit" disabled={!canSubmit}>
          {busy ? 'Running…' : apiOk === null ? 'Checking API…' : 'Run'}
        </button>
        <button className="ghost" type="button" disabled={busy} onClick={() => onReplay(threshold)}>
          Replay a recorded run
        </button>
      </div>
    </form>
  )
}
