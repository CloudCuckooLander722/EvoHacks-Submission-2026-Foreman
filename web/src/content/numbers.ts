/**
 * Real numbers from our live runs (docs/RESULTS.md, after the dedupe fix, Sun Oct 11, 2026).
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
  { value: '12.9 min', label: 'wall time', note: '7 steps, end to end' },
  { value: '$1.16', label: 'total cost', note: '34,161 decisions' },
  { value: '76.4%', label: 'decided by Jev', note: 'of 34,156 model decisions' },
]

/** Run C: the 1,000-review API run that the live demo replays. */
export const DEMO_RUN: Stat[] = [
  { value: '1,000', label: 'reviews' },
  { value: '85.6 s', label: 'wall time' },
  { value: '$0.0942', label: 'total cost' },
  { value: '79.5%', label: 'decided by Jev' },
]
