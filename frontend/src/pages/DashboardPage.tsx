import { Link } from 'react-router-dom'

import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  ProgressBar,
  RiskBadge,
  SeverityBadge,
  Stat,
  StatusBadge,
} from '../components/ui'
import { useAnalytics, useFindings, useQueueStats, useReviews } from '../hooks/queries'
import { formatCost, formatDuration, formatNumber, formatRelative } from '../lib/format'
import { ACTIVE_REVIEW_STATUSES } from '../lib/severity'

function QueueCard() {
  const { data } = useQueueStats()
  if (!data) return null
  return (
    <Card className="px-5 py-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Queue</p>
      <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
        <span className="text-slate-500">Queued</span>
        <span className="text-right tabular-nums text-slate-200">{data.queued}</span>
        <span className="text-slate-500">Processing</span>
        <span className="text-right tabular-nums text-slate-200">{data.processing}</span>
        <span className="text-slate-500">Done (24h)</span>
        <span className="text-right tabular-nums text-emerald-300">
          {data.completed_last_24h}
        </span>
        <span className="text-slate-500">Failed (24h)</span>
        <span className="text-right tabular-nums text-red-300">{data.failed_last_24h}</span>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        {data.eager_mode
          ? 'Running inline (eager mode) — no broker required.'
          : data.broker_reachable
            ? 'Broker reachable.'
            : 'Broker unreachable — reviews will not start.'}
      </p>
    </Card>
  )
}

export default function DashboardPage() {
  const analytics = useAnalytics({ window_days: 30 })
  const reviews = useReviews({ page_size: 8 })
  const findings = useFindings({ status: 'open', page_size: 6 })

  if (analytics.isLoading) return <LoadingBlock label="Loading your dashboard…" />
  if (analytics.isError) {
    return <ErrorState error={analytics.error} onRetry={() => void analytics.refetch()} />
  }

  const overview = analytics.data?.overview

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <h1 className="text-xl font-semibold text-slate-100">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">
          Activity across every repository you can access, over the last 30 days.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Repositories"
          value={formatNumber(overview?.repositories)}
          hint={`${formatNumber(overview?.active_repositories)} active`}
        />
        <Stat
          label="Open pull requests"
          value={formatNumber(overview?.open_pull_requests)}
          hint={`${formatNumber(overview?.pull_requests)} tracked in total`}
        />
        <Stat
          label="Reviews completed"
          value={formatNumber(overview?.reviews_completed)}
          hint={`${formatNumber(overview?.reviews_failed)} failed · ${formatNumber(
            overview?.reviews_in_progress,
          )} in flight`}
          tone={overview && overview.reviews_failed > 0 ? 'warning' : 'default'}
        />
        <Stat
          label="Open findings"
          value={formatNumber(overview?.findings_open)}
          hint={`${formatNumber(overview?.critical_open)} critical · ${formatNumber(
            overview?.high_open,
          )} high`}
          tone={overview && overview.critical_open > 0 ? 'danger' : 'default'}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Average risk score"
          value={overview ? Math.round(overview.avg_risk_score) : '—'}
          hint="0–100, computed from named risk factors"
        />
        <Stat
          label="Average review time"
          value={formatDuration(overview?.avg_review_duration_ms)}
          hint="End to end, per completed review"
        />
        <Stat
          label="LLM spend"
          value={formatCost(overview?.llm_cost_usd)}
          hint={`${formatNumber(overview?.llm_tokens)} tokens`}
        />
        <QueueCard />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Recent reviews"
            subtitle="Newest first"
            action={
              <Link to="/reviews" className="link text-xs">
                View all
              </Link>
            }
          />
          {reviews.isLoading ? (
            <LoadingBlock />
          ) : reviews.data && reviews.data.items.length > 0 ? (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Pull request</th>
                    <th>Status</th>
                    <th>Risk</th>
                    <th className="text-right">Findings</th>
                    <th className="text-right">When</th>
                  </tr>
                </thead>
                <tbody>
                  {reviews.data.items.map((review) => (
                    <tr key={review.id}>
                      <td className="max-w-[22rem]">
                        <Link to={`/reviews/${review.id}`} className="link block truncate">
                          {review.pull_request_title ?? review.commit_sha.slice(0, 7)}
                        </Link>
                        <span className="block truncate text-xs text-slate-500">
                          {review.repository_full_name ?? '—'}
                          {review.pull_request_number ? ` #${review.pull_request_number}` : ''}
                        </span>
                      </td>
                      <td>
                        <StatusBadge status={review.status} />
                        {ACTIVE_REVIEW_STATUSES.has(review.status) ? (
                          <ProgressBar className="mt-1.5" value={review.progress} />
                        ) : null}
                      </td>
                      <td>
                        <RiskBadge score={review.risk_score} band={review.risk_band} />
                      </td>
                      <td className="text-right tabular-nums">{review.findings_count}</td>
                      <td className="text-right text-xs text-slate-500">
                        {formatRelative(review.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title="No reviews yet"
              description="Trigger one from a pull request to see it here."
              action={
                <Link to="/pull-requests" className="btn-primary">
                  Browse pull requests
                </Link>
              }
            />
          )}
        </Card>

        <Card>
          <CardHeader
            title="Open findings"
            subtitle="Highest severity first"
            action={
              <Link to="/findings" className="link text-xs">
                View all
              </Link>
            }
          />
          {findings.isLoading ? (
            <LoadingBlock />
          ) : findings.data && findings.data.items.length > 0 ? (
            <ul className="divide-y divide-surface-border/60">
              {findings.data.items.map((finding) => (
                <li key={finding.id} className="px-5 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 text-sm font-medium text-slate-200">{finding.title}</p>
                    <SeverityBadge severity={finding.severity} />
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-slate-500">
                    {finding.file_path}
                    {finding.line_number ? `:${finding.line_number}` : ''}
                  </p>
                  <Link
                    to={`/reviews/${finding.review_run_id}`}
                    className="mt-1 inline-block text-xs text-slate-500 hover:text-accent"
                  >
                    {finding.repository_full_name ?? 'Open review'}
                    {finding.pull_request_number ? ` #${finding.pull_request_number}` : ''}
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Nothing open" description="Every finding has been dealt with." />
          )}
        </Card>
      </div>
    </div>
  )
}
