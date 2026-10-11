import decisionsJson from '../mocks/decisions.json'
import resultsJson from '../mocks/results.json'
import { buildReplay } from './replay'
import type { Decision, ForemanApi, ResultsFile } from './types'

interface Recording {
  results: ResultsFile
  decisions: Decision[]
}

/** The live API run on 1,000 real reviews (docs/RESULTS.md, run C), served from public/. */
const REPLAY_DIR = `${import.meta.env.BASE_URL}replays/reviews-1k/`

const bundled: Recording = {
  results: resultsJson as unknown as ResultsFile,
  decisions: decisionsJson as unknown as Decision[],
}

let recording: Promise<Recording> | null = null

async function fetchRecording(): Promise<Recording> {
  const [res, dec] = await Promise.all([
    fetch(`${REPLAY_DIR}results.json`),
    fetch(`${REPLAY_DIR}decisions.jsonl`),
  ])
  if (!res.ok || !dec.ok) throw new Error('replay bundle missing')
  const results = (await res.json()) as ResultsFile
  const decisions = (await dec.text())
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as Decision)
  return { results, decisions }
}

/** The real recorded run when it is deployed with the site, else the small bundled mock. */
function load(): Promise<Recording> {
  recording ??= fetchRecording().catch(() => bundled)
  return recording
}

/**
 * Same interface as the real client, backed by a recorded run.
 * Uploads are ignored: every run replays the same recording in about 20 seconds.
 */
export function createMockApi(durationMs = 20_000): ForemanApi {
  return {
    async startRun() {
      const [{ results }] = await Promise.all([load(), new Promise((r) => setTimeout(r, 300))])
      return results.run_id
    },

    subscribe(_runId, onEvent) {
      let timers: ReturnType<typeof setTimeout>[] = []
      let cancelled = false
      void load().then(({ results, decisions }) => {
        if (cancelled) return
        timers = buildReplay(results, decisions, durationMs).map(({ at, event }) =>
          setTimeout(() => onEvent(event), at),
        )
      })
      return () => {
        cancelled = true
        timers.forEach(clearTimeout)
      }
    },

    getResults: async () => structuredClone((await load()).results),

    // Pairwise checks (dedupe, rank) are logged under `a|b`; they belong to both items.
    getDecisions: async (_runId, itemId) =>
      (await load()).decisions.filter((d) => d.item_id === itemId || d.item_id.split('|').includes(itemId)),

    // Exports of the recorded run, made by foreman.format.export (see public/samples/).
    exportUrl: (_runId, format) => `${import.meta.env.BASE_URL}samples/foreman-results.${format}`,
  }
}
