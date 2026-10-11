import { useEffect, useState } from 'react'
import { api } from '../../api'

/**
 * GET /runs/{id}/export?format=xlsx (in replay mode, the recorded run's export).
 * The button stays hidden until the export answers, and for good if it says 501 (not built).
 */
export function Downloads({ runId }: { runId: string }) {
  const url = api.exportUrl(runId, 'xlsx')
  const [ok, setOk] = useState(false)

  useEffect(() => {
    let live = true
    setOk(false)
    fetch(url).then(
      (res) => live && setOk(res.ok),
      () => live && setOk(false),
    )
    return () => {
      live = false
    }
  }, [url])

  if (!ok) return null
  return (
    <div className="downloads">
      <a className="download" href={url} download={`foreman-${runId}.xlsx`}>
        <strong>Download Excel</strong>
        <span className="muted">every row, coloured by who decided (Jev / LLM / code)</span>
      </a>
    </div>
  )
}
