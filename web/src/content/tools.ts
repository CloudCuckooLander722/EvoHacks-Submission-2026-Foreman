/** The toolkit, from the README and `python -m foreman.dev catalog`. */

export interface Tool {
  name: string
  does: string
  uses: 'jev' | 'code'
  ready: boolean
}

export const TOOLS: Tool[] = [
  { name: 'filter', does: 'Keep only items that match a condition', uses: 'jev', ready: true },
  { name: 'classify', does: 'Put items into categories, including deep category trees', uses: 'jev', ready: true },
  { name: 'group', does: 'Discover themes and assign every item to one', uses: 'jev', ready: true },
  { name: 'dedupe', does: 'Find records that are the same thing', uses: 'jev', ready: true },
  { name: 'score', does: 'Rate items on several weighted dimensions', uses: 'jev', ready: true },
  { name: 'rank', does: 'Sort items by a fuzzy quality like urgency', uses: 'jev', ready: true },
  { name: 'verify', does: 'Check claims against a source', uses: 'jev', ready: true },
  { name: 'calculate', does: 'Totals, counts, group-bys, date math (pure code)', uses: 'code', ready: true },
  { name: 'extract', does: 'Pull structured fields out of messy text', uses: 'jev', ready: false },
  { name: 'find', does: 'Locate the passages in a long document that answer a question', uses: 'jev', ready: false },
  { name: 'guard', does: 'Check proposed actions: allow, review or block', uses: 'jev', ready: false },
]
