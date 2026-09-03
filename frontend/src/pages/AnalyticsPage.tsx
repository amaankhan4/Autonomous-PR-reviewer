import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Select,
  Stat,
} from '../components/ui'
import { useAnalytics, useRepositories } from '../hooks/queries'
import { formatCost, formatDuration, formatNumber } from '../lib/format'
import { severityStyle } from '../lib/severity'

const AXIS = { stroke: '#64748b', fontSize: 11 }
const GRID = '#1e293b'
const TOOLTIP_STYLE = {
  backgroundColor: '#0f172a',
  border: '1px solid #1e293b',
  borderRadius: '0.5rem',
  fontSize: '0.75rem',
  color: '#e2e8f0',
}
const PALETTE = ['#2f81f7', '#3fb950', '#d29922', '#f85149', '#a371f7', '#39c5cf', '#db61a2']

function ChartCard({
  title,
  subtitle,
  children,
  empty,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
  empty: boolean
}) {
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />
      {empty ? (
        <EmptyState title="Not enough data yet" description="Run a few reviews to populate this." />
      ) : (
        <div className="px-3 py-4">{children}</div>
      )}
    </Card>
  )
}

export default function AnalyticsPage() {
  const [windowDays, setWindowDays] = useState('30')
  const [repositoryId, setRepositoryId] = useState('')

  const repositories = useRepositories({ page_size: 100 })
  const { data, isLoading, isError, error, refetch } = useAnalytics({
    window_days: Number(windowDays),
    repository_id: repositoryId || undefined,
  })

  if (isLoading) return <LoadingBlock label="Crunching the numbers…" />
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return <EmptyState title="No analytics available" />

  const { overview } = data

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        description={`Aggregated over the last ${data.window_days} days.`}
        action={
          <>
            <Select
              label="Repository"
              className="w-56"
              value={repositoryId}
              onChange={setRepositoryId}
              options={[
                { value: '', label: 'All repositories' },
                ...(repositories.data?.items.map((repo) => ({
                  value: repo.id,
                  label: repo.full_name,
                })) ?? []),
              ]}
            />
            <Select
              label="Window"
              className="w-36"
              value={windowDays}
              onChange={setWindowDays}
              options={[
                { value: '7', label: 'Last 7 days' },
                { value: '30', label: 'Last 30 days' },
                { value: '90', label: 'Last 90 days' },
              ]}
            />
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Reviews"
          value={formatNumber(overview.reviews_total)}
          hint={`${formatNumber(overview.reviews_completed)} completed · ${formatNumber(
            overview.reviews_failed,
          )} failed`}
        />
        <Stat
          label="Findings"
          value={formatNumber(overview.findings_total)}
          hint={`${formatNumber(overview.findings_open)} open · ${formatNumber(
            overview.findings_resolved,
          )} resolved`}
        />
        <Stat
          label="Average review time"
          value={formatDuration(overview.avg_review_duration_ms)}
          hint={`${formatNumber(overview.published_reviews)} published to GitHub`}
        />
        <Stat
          label="LLM spend"
          value={formatCost(overview.llm_cost_usd)}
          hint={`${formatNumber(overview.llm_tokens)} tokens`}
        />
      </div>

      <ChartCard
        title="Review activity"
        subtitle="Reviews, findings and average risk per day"
        empty={data.trend.length === 0}
      >
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data.trend}>
            <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
            <XAxis dataKey="date" {...AXIS} />
            <YAxis {...AXIS} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
            <Line
              type="monotone"
              dataKey="reviews"
              name="Reviews"
              stroke="#2f81f7"
              strokeWidth={2}
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="findings"
              name="Findings"
              stroke="#d29922"
              strokeWidth={2}
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="avg_risk"
              name="Avg risk"
              stroke="#f85149"
              strokeWidth={2}
              strokeDasharray="4 3"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Findings by severity"
          empty={data.severity_breakdown.length === 0}
        >
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie
                data={data.severity_breakdown}
                dataKey="count"
                nameKey="label"
                innerRadius={55}
                outerRadius={95}
                paddingAngle={2}
              >
                {data.severity_breakdown.map((entry) => (
                  <Cell key={entry.label} fill={severityStyle(entry.label).hex} />
                ))}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Findings by category" empty={data.category_breakdown.length === 0}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.category_breakdown} layout="vertical">
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" {...AXIS} allowDecimals={false} />
              <YAxis type="category" dataKey="label" width={110} {...AXIS} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: '#1e293b55' }} />
              <Bar dataKey="count" name="Findings" fill="#2f81f7" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Findings by source" empty={data.source_breakdown.length === 0}>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data.source_breakdown}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" {...AXIS} />
              <YAxis {...AXIS} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: '#1e293b55' }} />
              <Bar dataKey="count" name="Findings" radius={[4, 4, 0, 0]}>
                {data.source_breakdown.map((entry, index) => (
                  <Cell key={entry.label} fill={PALETTE[index % PALETTE.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Findings by status" empty={data.status_breakdown.length === 0}>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data.status_breakdown}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" {...AXIS} />
              <YAxis {...AXIS} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: '#1e293b55' }} />
              <Bar dataKey="count" name="Findings" radius={[4, 4, 0, 0]}>
                {data.status_breakdown.map((entry, index) => (
                  <Cell key={entry.label} fill={PALETTE[(index + 2) % PALETTE.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Riskiest repositories" subtitle="By finding count" />
          {data.top_repositories.length === 0 ? (
            <EmptyState title="No data yet" />
          ) : (
            <ul className="divide-y divide-surface-border/60">
              {data.top_repositories.map((entry) => (
                <li
                  key={entry.label}
                  className="flex items-center justify-between gap-4 px-5 py-2.5"
                >
                  <span className="min-w-0 truncate text-sm text-slate-300">{entry.label}</span>
                  <span className="shrink-0 tabular-nums text-sm text-slate-400">
                    {entry.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Hotspot files" subtitle="Files that attract the most findings" />
          {data.top_files.length === 0 ? (
            <EmptyState title="No data yet" />
          ) : (
            <ul className="divide-y divide-surface-border/60">
              {data.top_files.map((entry) => (
                <li
                  key={entry.label}
                  className="flex items-center justify-between gap-4 px-5 py-2.5"
                >
                  <span className="min-w-0 truncate font-mono text-xs text-slate-400">
                    {entry.label}
                  </span>
                  <span className="shrink-0 tabular-nums text-sm text-slate-400">
                    {entry.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
