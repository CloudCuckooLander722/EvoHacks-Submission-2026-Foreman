import { api } from '../../api'
import type { ExportFormat } from '../../api/types'

const FORMATS: { format: ExportFormat; label: string; note: string }[] = [
  { format: 'xlsx', label: 'Excel', note: 'every row, coloured by who decided' },
  { format: 'pdf', label: 'PDF', note: 'summary, charts, top 50 rows' },
  { format: 'docx', label: 'Word', note: 'summary, charts, tables' },
]

/** GET /runs/{id}/export?format=… (in mock mode, the recorded run's exports in public/samples/). */
export function Downloads({ runId }: { runId: string }) {
  return (
    <div className="downloads">
      {FORMATS.map((f) => (
        <a key={f.format} className="download" href={api.exportUrl(runId, f.format)} download={`foreman-${runId}.${f.format}`}>
          <strong>{f.label}</strong>
          <span className="muted">{f.note}</span>
        </a>
      ))}
    </div>
  )
}
