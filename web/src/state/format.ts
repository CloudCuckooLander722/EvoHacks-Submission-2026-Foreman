/** Display helpers shared by the meter and the plan steps. */

export const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US')

/** Costs are tiny; show enough digits that a real run never reads as $0.00. */
export function fmtUsd(n: number): string {
  if (n === 0) return '$0.00'
  if (n < 0.01) return `$${n.toFixed(5)}`
  return `$${n.toFixed(2)}`
}

export const fmtSeconds = (s: number) => (s < 10 ? `${s.toFixed(2)} s` : `${s.toFixed(1)} s`)
