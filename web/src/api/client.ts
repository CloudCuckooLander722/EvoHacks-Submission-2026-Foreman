import { API_BASE } from '../config'
import type { Decision, ExportFormat, ForemanApi, ResultsFile, RunEvent } from './types'

const EVENT_NAMES = ['plan', 'node', 'decision', 'stats', 'done'] as const

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status} ${await res.text()}`)
  return (await res.json()) as T
}

/** The real backend: Nathan's FastAPI app, per MINH_PLAN §D. */
export const realApi: ForemanApi = {
  async startRun(file, job, threshold) {
    if (!file) throw new Error('Choose a file to run on.')
    const body = new FormData()
    body.append('file', file)
    body.append('job', job)
    body.append('threshold', String(threshold))
    const res = await fetch(`${API_BASE}/runs`, { method: 'POST', body })
    if (!res.ok) throw new Error(`Could not start the run: ${res.status} ${await res.text()}`)
    const { run_id } = (await res.json()) as { run_id: string }
    return run_id
  },

  subscribe(runId, onEvent) {
    const es = new EventSource(`${API_BASE}/runs/${encodeURIComponent(runId)}/events`)
    let finished = false
    const close = () => {
      finished = true
      es.close()
    }

    for (const name of EVENT_NAMES) {
      es.addEventListener(name, (e) => {
        const data = JSON.parse((e as MessageEvent<string>).data)
        onEvent({ type: name, data } as RunEvent)
        if (name === 'done') close()
      })
    }

    // §D's `error` event shares its name with EventSource's own connection error.
    // A server event carries data; a dropped connection does not.
    es.addEventListener('error', (e) => {
      if (finished) return
      if (e instanceof MessageEvent && e.data) {
        const data = JSON.parse(e.data)
        onEvent({ type: 'error', data })
        if (data.fatal) close()
        return
      }
      if (es.readyState === EventSource.CLOSED) {
        onEvent({
          type: 'error',
          data: { message: 'Lost the connection to the server.', node: null, fatal: true },
        })
        close()
      }
      // Otherwise EventSource is reconnecting on its own.
    })

    return close
  },

  getResults: (runId) =>
    getJson<ResultsFile>(`/runs/${encodeURIComponent(runId)}/results`),

  getDecisions: (runId, itemId) =>
    getJson<Decision[]>(
      `/runs/${encodeURIComponent(runId)}/decisions?item_id=${encodeURIComponent(itemId)}`,
    ),

  exportUrl: (runId, format: ExportFormat) =>
    `${API_BASE}/runs/${encodeURIComponent(runId)}/export?format=${format}`,
}
