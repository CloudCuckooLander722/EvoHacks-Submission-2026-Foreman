import { useRef, useState, type DragEvent, type FormEvent } from 'react'
import { USE_MOCKS } from '../../config'

export const DEMO_JOB =
  'Turn these reviews into a backlog: drop spam, split bugs from feature requests, ' +
  'group into themes, merge duplicates, rank by urgency.'

interface Props {
  busy: boolean
  onSubmit: (file: File | null, job: string, threshold: number) => void
}

/** The job textarea, a drag-and-drop file target and the confidence bar (MINH_PLAN §C1). */
export function JobComposer({ busy, onSubmit }: Props) {
  const [job, setJob] = useState(DEMO_JOB)
  const [file, setFile] = useState<File | null>(null)
  const [threshold, setThreshold] = useState(0.7)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const needsFile = !USE_MOCKS && !file
  const canSubmit = !busy && job.trim().length > 0 && !needsFile

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) setFile(dropped)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (canSubmit) onSubmit(file, job.trim(), threshold)
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
      {USE_MOCKS && (
        <p className="hint">Mock mode: runs replay a recorded run; the file is not uploaded.</p>
      )}

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

      <button className="primary" type="submit" disabled={!canSubmit}>
        {busy ? 'Running…' : 'Run'}
      </button>
    </form>
  )
}
