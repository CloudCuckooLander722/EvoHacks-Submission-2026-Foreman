import { API_BASE, USE_MOCKS } from '../config'
import { realApi } from './client'
import { createMockApi } from './mock'
import type { ForemanApi } from './types'

/** `live` calls the API on :8000 (through the Vite proxy); `replay` plays the recorded 1k run. */
export type Mode = 'live' | 'replay'

const replayApi = createMockApi()
let current: Mode = USE_MOCKS ? 'replay' : 'live'

export const getMode = (): Mode => current
export function setMode(mode: Mode) {
  current = mode
}

const backend = () => (current === 'replay' ? replayApi : realApi)

/** The backend the whole site uses; delegates to whichever mode is current. */
export const api: ForemanApi = {
  startRun: (file, job, threshold, plan) => backend().startRun(file, job, threshold, plan),
  subscribe: (runId, onEvent) => backend().subscribe(runId, onEvent),
  getResults: (runId) => backend().getResults(runId),
  getDecisions: (runId, itemId) => backend().getDecisions(runId, itemId),
  exportUrl: (runId, format) => backend().exportUrl(runId, format),
}

export interface Health {
  ok: boolean
  keys?: { jev: boolean; llm: boolean }
}

/** GET /health with a short timeout; never throws, so the page can always fall back to replay. */
export async function checkApi(): Promise<Health> {
  if (USE_MOCKS) return { ok: false }
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2500) })
    if (!res.ok) return { ok: false }
    return (await res.json()) as Health
  } catch {
    return { ok: false }
  }
}
