import { useCallback, useMemo, useState } from 'react'
import type { ResultsFile } from '../../api/types'
import { Charts } from './Charts'
import { ConfidenceCurve } from './ConfidenceCurve'
import { Downloads } from './Downloads'
import { columnsOf, decidedFields, findCharts, orderedItems } from './model'
import { ResultsTable, type CellRef } from './ResultsTable'
import { Summary } from './Summary'
import { WhyPanel } from './WhyPanel'

/** Summary → confidence curve → charts → table with "why" panel → downloads (MINH_PLAN §C2). */
export function Results({ results }: { results: ResultsFile }) {
  const [selected, setSelected] = useState<CellRef | null>(null)
  const items = useMemo(() => orderedItems(results), [results])
  const columns = useMemo(() => columnsOf(results.items), [results])
  const decided = useMemo(() => decidedFields(results.items), [results])
  const charts = useMemo(() => findCharts(results), [results])
  const curve = results.aggregates.escalation_curve
  const close = useCallback(() => setSelected(null), [])

  return (
    <section id="results" className="section results">
      <h2 className="section-title">Results</h2>
      <Summary results={results} />
      {curve && curve.length > 0 && (
        <ConfidenceCurve curve={curve} stats={results.stats} runThreshold={results.threshold} />
      )}
      <Charts charts={charts} />
      <ResultsTable items={items} columns={columns} decided={decided} selected={selected} onSelect={setSelected} />
      <Downloads runId={results.run_id} />
      <WhyPanel runId={results.run_id} nodes={results.plan.nodes} cell={selected} onClose={close} />
    </section>
  )
}
