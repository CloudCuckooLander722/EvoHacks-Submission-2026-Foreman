import { describe, expect, it } from 'vitest'
import resultsJson from '../../mocks/results.json'
import type { ResultsFile } from '../../api/types'
import { barsOf, columnsOf, escalatedAt, findCharts, nodeForField, orderedItems } from './model'

const results = resultsJson as unknown as ResultsFile

describe('chart detection (mirrors foreman/format/charts.py)', () => {
  it('charts dict[str, number] and {label, count} lists, nothing else', () => {
    expect(barsOf({ bug: 3, praise: 1 })).toEqual([
      { label: 'bug', value: 3 },
      { label: 'praise', value: 1 },
    ])
    expect(barsOf([{ theme: 'Camera', count: 2 }])).toEqual([{ label: 'Camera', value: 2 }])
    expect(barsOf({ n: 3, per_dimension_mean: { a: 1 } })).toBeNull()
    expect(barsOf(['row-2', 'row-3'])).toBeNull()
  })

  it('puts the split first and drops duplicate charts and the curve', () => {
    const charts = findCharts(results)
    const keys = charts.map((c) => c.key)
    expect(keys[0]).toBe('decided_by')
    expect(keys).toContain('type_counts')
    expect(keys).not.toContain('escalation_curve')
  })

  it('charts the same bars once', () => {
    const dup = {
      ...results,
      aggregates: {
        theme_groups: [{ theme: 'Camera', count: 2 }, { theme: 'Login', count: 1 }],
        theme_counts: { Login: 1, Camera: 2 },
      },
    }
    expect(findCharts(dup).map((c) => c.key)).toEqual(['decided_by', 'theme_groups'])
  })
})

describe('table helpers', () => {
  it('hides bookkeeping fields and orders by rank', () => {
    const cols = columnsOf(results.items)
    expect(cols).toContain('type')
    expect(cols.some((c) => c.includes('__'))).toBe(false)
    expect(orderedItems(results)[0].id).toBe((results.aggregates.rank_order as string[])[0])
  })

  it('maps a field to the step that wrote it', () => {
    const nodes = [
      { id: 'n1', tool: 'score', params: { output_field: 'urgency' } },
      { id: 'n2', tool: 'classify', params: { output_field: 'type' } },
    ]
    expect(nodeForField(nodes, 'urgency_severity')?.id).toBe('n1')
    expect(nodeForField(nodes, 'type')?.id).toBe('n2')
    expect(nodeForField(nodes, 'rating')).toBeNull()
  })

  it('reads the escalation curve exactly on its grid', () => {
    const curve: [number, number][] = [[0, 0], [0.05, 10], [0.1, 30]]
    expect(escalatedAt(curve, 0.05)).toBe(10)
    expect(escalatedAt(curve, 0.075)).toBe(20)
    expect(escalatedAt(curve, 1)).toBe(30)
  })
})
