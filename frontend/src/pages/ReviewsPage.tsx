import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Pagination,
  ProgressBar,
  RiskBadge,
  Select,
  StatusBadge,
} from '../components/ui'
import { useRepositories, useReviews } from '../hooks/queries'
import { formatDuration, formatRelative, shortSha } from '../lib/format'
import { ACTIVE_REVIEW_STATUSES } from '../lib/severity'

const STATUSES = [
  '',
  'queued',
  'processing',
  'analyzing',
  'reviewing',
  'publishing',
  'completed',
  'failed',
  'cancelled',
]

const RISK_BANDS = ['', 'low', 'moderate', 'elevated', 'high', 'critical']

export default function ReviewsPage() {
  const [repositoryId, setRepositoryId] = useState('')
  const [status, setStatus] = useState('')
  const [riskBand, setRiskBand] = useState('')
  const [page, setPage] = useState(1)

  const repositories = useRepositories({ page_size: 100 })
  const { data, isLoading, isError, error, refetch, isFetching } = useReviews({
    repository_id: repositoryId || undefined,
    status: status || undefined,
    risk_band: riskBand || undefined,
    page,
    page_size: 20,
  })

  const change = (setter: (value: string) => void) => (value: string) => {
    setter(value)
    setPage(1)
  }

  return (
    <div>
      <PageHeader
        title="Reviews"
        description="Every review run, including the ones still in flight."
        action={
          <button
            type="button"
            className="btn-secondary"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            Refresh
          </button>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Select
          label="Repository"
          value={repositoryId}
          onChange={change(setRepositoryId)}
          options={[
            { value: '', label: 'All repositories' },
            ...(repositories.data?.items.map((repo) => ({
              value: repo.id,
              label: repo.full_name,
            })) ?? []),
          ]}
        />
        <Select
          label="Status"
          value={status}
          onChange={change(setStatus)}
          options={STATUSES.map((value) => ({
            value,
            label: value === '' ? 'All statuses' : value,
          }))}
        />
        <Select
          label="Risk band"
          value={riskBand}
          onChange={change(setRiskBand)}
          options={RISK_BANDS.map((value) => ({
            value,
            label: value === '' ? 'All bands' : value,
          }))}
        />
      </div>

      <Card>
        {isLoading ? (
          <LoadingBlock />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data && data.items.length > 0 ? (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Review</th>
                    <th>Status</th>
                    <th>Risk</th>
                    <th className="text-right">Findings</th>
                    <th className="text-right">Files</th>
                    <th className="text-right">Duration</th>
                    <th className="text-right">Started</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((review) => (
                    <tr key={review.id}>
                      <td className="max-w-[26rem]">
                        <Link to={`/reviews/${review.id}`} className="link block truncate">
                          {review.pull_request_title ?? `Review ${shortSha(review.commit_sha)}`}
                        </Link>
                        <span className="block truncate text-xs text-slate-500">
                          {review.repository_full_name ?? '—'}
                          {review.pull_request_number ? ` #${review.pull_request_number}` : ''} ·{' '}
                          <span className="font-mono">{shortSha(review.commit_sha)}</span> ·{' '}
                          {review.trigger}
                        </span>
                      </td>
                      <td className="min-w-[9rem]">
                        <StatusBadge status={review.status} />
                        {ACTIVE_REVIEW_STATUSES.has(review.status) ? (
                          <>
                            <ProgressBar className="mt-1.5" value={review.progress} />
                            <span className="mt-1 block text-[0.6875rem] text-slate-500">
                              {review.stage ?? 'starting'}
                            </span>
                          </>
                        ) : null}
                        {review.error_message ? (
                          <span className="mt-1 block max-w-[14rem] truncate text-[0.6875rem] text-red-400">
                            {review.error_message}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <RiskBadge score={review.risk_score} band={review.risk_band} />
                      </td>
                      <td className="text-right tabular-nums">{review.findings_count}</td>
                      <td className="text-right tabular-nums">{review.files_analyzed}</td>
                      <td className="text-right text-xs text-slate-500">
                        {formatDuration(review.duration_ms)}
                      </td>
                      <td className="text-right text-xs text-slate-500">
                        {formatRelative(review.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={data.page}
              pages={data.pages}
              total={data.total}
              onChange={setPage}
            />
          </>
        ) : (
          <EmptyState
            title="No reviews match these filters"
            description="Trigger a review from the pull requests page."
            action={
              <Link to="/pull-requests" className="btn-primary">
                Go to pull requests
              </Link>
            }
          />
        )}
      </Card>
    </div>
  )
}
