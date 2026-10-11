/**
 * Real numbers from our live runs (docs/RESULTS.md, latest plan: runs D2/D3 and C3, Sun Oct 11, 2026).
 * Never invent or round these up; change them only from a new recorded run.
 */

export interface Stat {
  value: string
  label: string
  note?: string
}

/** 10,000 Medium app reviews, saved reviews_backlog plan, threshold 0.7 (runs D2 and D3). */
export const HERO_RUN: Stat[] = [
  { value: '10,000', label: 'app reviews', note: 'one plain-English request' },
  { value: '13–22 min', label: 'wall time', note: 'varies with LLM provider load' },
  { value: 'about $1.20', label: 'total cost', note: 'about 35,000 decisions' },
  { value: '76%', label: 'decided by Jev', note: 'the rest by one LLM fallback call' },
]

/** The 1,000-review API run that the replay mode plays back (run C3). */
export const DEMO_RUN: Stat[] = [
  { value: '1,000', label: 'reviews' },
  { value: 'about 1.5–2 min', label: 'wall time' },
  { value: 'about 10¢', label: 'total cost' },
  { value: '79%', label: 'decided by Jev' },
]

/** Share of bugs and feature requests no theme fits, shown as "Other" (run D3, 548 of 2,610). */
export const UNTHEMED_10K = '21%'
