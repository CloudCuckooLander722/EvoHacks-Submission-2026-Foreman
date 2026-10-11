/**
 * Real numbers from our live runs (docs/RESULTS.md, Sat Oct 10 – Sun Oct 11, 2026).
 * Never invent or round these up; change them only from a new recorded run.
 */

export interface Stat {
  value: string
  label: string
  note?: string
}

/** Run D: 10,000 Medium app reviews, hand-written plan, threshold 0.7. */
export const HERO_RUN: Stat[] = [
  { value: '10,000', label: 'app reviews', note: 'one plain-English request' },
  { value: '27.6 min', label: 'wall time', note: '7 steps, end to end' },
  { value: '$2.25', label: 'total cost', note: 'Jev $0.83 + LLM $1.41' },
  { value: '72.6%', label: 'decided by Jev', note: 'of 64,888 model decisions' },
]

/** Run C: the 1,000-review API run that the live demo replays. */
export const DEMO_RUN: Stat[] = [
  { value: '1,000', label: 'reviews' },
  { value: '117.1 s', label: 'wall time' },
  { value: '$0.1069', label: 'total cost' },
  { value: '76.5%', label: 'decided by Jev' },
]
