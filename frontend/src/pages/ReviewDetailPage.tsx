import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import DiffViewer from '../components/DiffViewer'
import FindingCard from '../components/FindingCard'
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingBlock,
  PageHeader,
  ProgressBar,
  RiskBadge,
  Spinner,
  StatusBadge,
} from '../components/ui'
import { useCancelReview, useReview, useReviewDiff, useTriggerReview } from '../hooks/queries'
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  shortSha,
  titleCase,
} from '../lib/format'
import { ACTIVE_REVIEW_STATUSES, riskStyle } from '../lib/severity'
import type { ReviewRunDetail } from '../lib/types'

type Tab = 'findings' | 'diff' | 'risk' | 'activity'

function RiskFactors({ review }: { review: ReviewRunDetail }) {
  const factors = review.risk_factors ?? []
  if (factors.length === 0) {
    return (
      <EmptyState
        title="No risk factors recorded"
        description="The risk score is only broken down for completed reviews."
      />
    )
  }
  const max = Math.max(...factors.map((factor) => factor.points), 1)
  return (
    <div className="space-y-4 px-5 py-4">
      <p className="text-xs text-slate-500">
        The score is the sum of these named factors. The model does not choose it.
      </p>
      {factors.map((factor) => (
        <div key={factor.key}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm text-slate-200">{factor.label}</span>
            <span className="font-mono text-xs tabular-nums text-slate-400">
              +{factor.points}
            </span>
          </div>
          <ProgressBar
            className="mt-1.5"
            value={(factor.points / max) * 100}
            colorClass={riskStyle(review.risk_band).bar}
          />
          {factor.detail ? (
            <p className="mt-1 text-xs text-slate-500">{factor.detail}</p>
          ) : null}
          {factor.evidence?.length ? (
            <ul className="mt-1 space-y-0.5">
              {factor.evidence.slice(0, 4).map((item) => (
                <li key={item} className="truncate font-mono text-[0.6875rem] text-slate-600">
                  {item}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function Activity({ review }: { review: ReviewRunDetail }) {
  const totalCost = review.llm_usage.reduce((sum, use) => sum + use.estimated_cost_usd, 0)
  const totalTokens = review.llm_usage.reduce(
    (sum, use) => sum + use.input_tokens + use.output_tokens,
    0,
  )

  return (
    <div className="space-y-6 px-5 py-4">
      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Model calls
        </h3>
        {review.llm_usage.length === 0 ? (
          <p className="text-sm text-slate-500">No model calls were recorded.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>Model</th>
                  <th className="text-right">In</th>
                  <th className="text-right">Out</th>
                  <th className="text-right">Latency</th>
                  <th className="text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                {review.llm_usage.map((use) => (
                  <tr key={use.id}>
                    <td>
                      {titleCase(use.operation)}
                      {!use.success ? (
                        <span className="ml-2 text-xs text-red-400">failed</span>
                      ) : null}
                    </td>
                    <td className="font-mono text-xs">{use.model}</td>
                    <td className="text-right tabular-nums">{formatNumber(use.input_tokens)}</td>
                    <td className="text-right tabular-nums">{formatNumber(use.output_tokens)}</td>
                    <td className="text-right tabular-nums">{formatDuration(use.latency_ms)}</td>
                    <td className="text-right tabular-nums">
                      {formatCost(use.estimated_cost_usd)}
                    </td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={2} className="font-medium text-slate-200">
                    Total
                  </td>
                  <td colSpan={2} className="text-right tabular-nums text-slate-200">
                    {formatNumber(totalTokens)} tokens
                  </td>
                  <td />
                  <td className="text-right tabular-nums text-slate-200">
                    {formatCost(totalCost)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Published comments
        </h3>
        {review.comments.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing was published to GitHub.</p>
        ) : (
          <ul className="space-y-2">
            {review.comments.map((comment) => (
              <li
                key={comment.id}
                className="rounded-lg border border-surface-border bg-surface px-3 py-2"
              >
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="text-slate-400">
                    {titleCase(comment.kind)}
                    {comment.file_path ? (
                      <span className="font-mono text-slate-500">
                        {' '}
                        · {comment.file_path}
                        {comment.line_number ? `:${comment.line_number}` : ''}
                      </span>
                    ) : null}
                  </span>
                  <span className={comment.delivered ? 'text-emerald-400' : 'text-amber-400'}>
                    {comment.delivered ? 'delivered' : 'not delivered'}
                  </span>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-xs text-slate-400">
                  {comment.body.slice(0, 600)}
                  {comment.body.length > 600 ? '…' : ''}
                </p>
                {comment.error_message ? (
                  <p className="mt-1 text-xs text-red-400">{comment.error_message}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {review.context_stats ? (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Context budget
          </h3>
          <pre className="overflow-x-auto rounded-lg bg-surface px-3 py-2 font-mono text-xs text-slate-400">
            {JSON.stringify(review.context_stats, null, 2)}
          </pre>
        </section>
      ) : null}
    </div>
  )
}

export default function ReviewDetailPage() {
  const { reviewId = '' } = useParams()
  const [tab, setTab] = useState<Tab>('findings')

  const { data, isLoading, isError, error, refetch } = useReview(reviewId)
  const active = data ? ACTIVE_REVIEW_STATUSES.has(data.status) : false
  const diff = useReviewDiff(reviewId, tab === 'diff' && !active)
  const cancel = useCancelReview()
  const rerun = useTriggerReview()

  if (isLoading) return <LoadingBlock label="Loading review…" />
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return <EmptyState title="Review not found" />

  const openFindings = data.findings.filter((finding) => finding.status === 'open').length

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: 'findings', label: 'Findings', badge: data.findings.length },
    { id: 'diff', label: 'Diff' },
    { id: 'risk', label: 'Risk' },
    { id: 'activity', label: 'Activity' },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title={data.pull_request_title ?? `Review ${shortSha(data.commit_sha)}`}
        description={
          <>
            {data.repository_id ? (
              <Link to={`/repositories/${data.repository_id}`} className="link">
                {data.repository_full_name}
              </Link>
            ) : (
              data.repository_full_name
            )}
            {data.pull_request_number ? ` · #${data.pull_request_number}` : ''} ·{' '}
            <span className="font-mono">{shortSha(data.commit_sha)}</span> · triggered by{' '}
            {data.trigger}
          </>
        }
        action={
          <>
            {data.github_review_url ? (
              <a
                className="btn-secondary"
                href={data.github_review_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                View on GitHub
              </a>
            ) : null}
            {active ? (
              <button
                type="button"
                className="btn-danger"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate(reviewId)}
              >
                {cancel.isPending ? <Spinner /> : null}
                Cancel
              </button>
            ) : (
              <button
                type="button"
                className="btn-primary"
                disabled={rerun.isPending}
                onClick={() =>
                  rerun.mutate({ pull_request_id: data.pull_request_id, force: true })
                }
              >
                {rerun.isPending ? <Spinner /> : null}
                Re-run review
              </button>
            )}
          </>
        }
      />

      {active ? (
        <Card className="px-5 py-4">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Spinner className="h-4 w-4 text-accent" />
              <span className="text-sm text-slate-200">
                {titleCase(data.stage ?? data.status)}…
              </span>
            </div>
            <span className="font-mono text-xs tabular-nums text-slate-500">
              {Math.round(data.progress)}%
            </span>
          </div>
          <ProgressBar className="mt-3" value={data.progress} />
          <p className="mt-2 text-xs text-slate-500">
            This page refreshes itself every two seconds while the review is running.
          </p>
        </Card>
      ) : null}

      {data.error_message ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <p className="font-medium">This review failed</p>
          <p className="mt-1 text-xs">{data.error_message}</p>
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-4">
        <div className="space-y-6 xl:col-span-3">
          {data.summary ? (
            <Card>
              <CardHeader title="Summary" subtitle="Written from the validated findings only" />
              <p className="prose-review whitespace-pre-wrap px-5 py-4">{data.summary}</p>
            </Card>
          ) : null}

          <Card>
            <div className="flex gap-1 border-b border-surface-border px-3 pt-3">
              {tabs.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  className={
                    tab === item.id
                      ? 'rounded-t-lg border-b-2 border-accent px-3 py-2 text-sm font-medium text-accent'
                      : 'rounded-t-lg border-b-2 border-transparent px-3 py-2 text-sm text-slate-400 hover:text-slate-200'
                  }
                >
                  {item.label}
                  {item.badge !== undefined ? (
                    <span className="ml-1.5 text-xs text-slate-500">{item.badge}</span>
                  ) : null}
                </button>
              ))}
            </div>

            {tab === 'findings' ? (
              data.findings.length > 0 ? (
                <div>
                  {data.findings.map((finding) => (
                    <FindingCard key={finding.id} finding={finding} />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No findings"
                  description={
                    data.status === 'completed'
                      ? 'Nothing in this diff met the confidence and severity thresholds.'
                      : 'Findings appear as the review progresses.'
                  }
                />
              )
            ) : null}

            {tab === 'diff' ? (
              active ? (
                <EmptyState
                  title="Diff unavailable while the review runs"
                  description="It will load as soon as the review finishes."
                />
              ) : diff.isLoading ? (
                <LoadingBlock label="Fetching the diff…" />
              ) : diff.isError ? (
                <ErrorState error={diff.error} onRetry={() => void diff.refetch()} />
              ) : diff.data ? (
                <DiffViewer files={diff.data.files} truncated={diff.data.truncated} />
              ) : null
            ) : null}

            {tab === 'risk' ? <RiskFactors review={data} /> : null}
            {tab === 'activity' ? <Activity review={data} /> : null}
          </Card>
        </div>

        <div className="space-y-6">
          <Card className="px-5 py-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Risk score
            </p>
            <div className="mt-2 flex items-baseline gap-3">
              <span
                className={`text-4xl font-semibold tabular-nums ${riskStyle(data.risk_band).text}`}
              >
                {Math.round(data.risk_score)}
              </span>
              <span className="text-sm uppercase tracking-wide text-slate-500">
                {data.risk_band ?? '—'}
              </span>
            </div>
            <ProgressBar
              className="mt-3"
              value={data.risk_score}
              colorClass={riskStyle(data.risk_band).bar}
            />
          </Card>

          <Card>
            <CardHeader title="Run details" />
            <dl className="divide-y divide-surface-border/60 px-5 py-2">
              <KeyValue label="Status">
                <StatusBadge status={data.status} />
              </KeyValue>
              <KeyValue label="Findings">
                {data.findings_count} ({openFindings} open)
              </KeyValue>
              <KeyValue label="Suppressed">{data.suppressed_count}</KeyValue>
              <KeyValue label="Files analyzed">
                {data.files_analyzed}
                {data.files_skipped ? ` (${data.files_skipped} skipped)` : ''}
              </KeyValue>
              <KeyValue label="Published">{data.published ? 'Yes' : 'No'}</KeyValue>
              <KeyValue label="Attempts">{data.attempts}</KeyValue>
              <KeyValue label="Duration">{formatDuration(data.duration_ms)}</KeyValue>
              <KeyValue label="Started">{formatDateTime(data.started_at)}</KeyValue>
              <KeyValue label="Finished">{formatDateTime(data.completed_at)}</KeyValue>
              <KeyValue label="Commit">
                <span className="font-mono text-xs">{shortSha(data.commit_sha)}</span>
              </KeyValue>
            </dl>
          </Card>

          <Card>
            <CardHeader title="Risk badge" />
            <div className="px-5 py-4">
              <RiskBadge score={data.risk_score} band={data.risk_band} />
              <p className="mt-2 text-xs text-slate-500">
                Automated review is advisory. A human still owns the merge decision.
              </p>
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
