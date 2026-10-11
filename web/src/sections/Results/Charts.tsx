import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, XAxis, YAxis } from 'recharts'
import type { Chart } from './model'

const SPLIT_LABEL: Record<string, string> = { jev: 'Jev', llm: 'LLM', code: 'Code' }

function ChartCard({ chart }: { chart: Chart }) {
  const data = chart.bars.map((b) => ({
    label: chart.split ? SPLIT_LABEL[b.label] ?? b.label : b.label,
    value: b.value,
    fill: chart.split ? `var(--${b.label})` : 'var(--accent)',
  }))
  const height = Math.max(120, data.length * 30 + 20)
  return (
    <figure className="card chart">
      <figcaption>{chart.title}</figcaption>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 0 }}>
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            width={210}
            tickLine={false}
            axisLine={false}
            fontSize={12}
            tickFormatter={(v: string) => (v.length > 30 ? `${v.slice(0, 29)}…` : v)}
          />
          <Bar dataKey="value" radius={[0, 6, 6, 0]} barSize={18} isAnimationActive={false}>
            {data.map((d) => <Cell key={d.label} fill={d.fill} />)}
            <LabelList dataKey="value" position="right" fontSize={12} fill="var(--muted)" formatter={(v: unknown) => Number(v).toLocaleString('en-US')} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </figure>
  )
}

/** Every chartable aggregate, found by shape (same rules as the exports). */
export function Charts({ charts }: { charts: Chart[] }) {
  return (
    <div className="charts">
      {charts.map((c) => <ChartCard key={c.key} chart={c} />)}
    </div>
  )
}
