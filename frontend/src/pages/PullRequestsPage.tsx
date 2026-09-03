import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Pagination,
  RiskBadge,
  SearchInput,
  Select,
  Spinner,
  StatusBadge,
} from '../components/ui'
import {
  useImportPullRequest,
  usePullRequests,
  useRepositories,
  useTriggerReview,
} from '../hooks/queries'
import { formatRelative } from '../lib/format'

function ReviewButton({ pullRequestId }: { pullRequestId: string }) {
  const mutation = useTriggerReview()
  return (
    <button
      type="button"
      className="btn-secondary px-2.5 py-1 text-xs"
      disabled={mutation.isPending}
      onClick={() => mutation.mutate({ pull_request_id: pullRequestId, force: true })}
      title="Queue a fresh review of the current head commit"
    >
      {mutation.isPending ? <Spinner className="h-3 w-3" /> : null}
      Review
    </button>
  )
}

function ImportCard({ repositoryOptions }: { repositoryOptions: { value: string; label: string }[] }) {
  const mutation = useImportPullRequest()
  const [repositoryId, setRepositoryId] = useState('')
  const [number, setNumber] = useState('')

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!repositoryId || !number) return
    mutation.mutate({ repository_id: repositoryId, number: Number(number), review: true })
  }

  return (
    <Card className="mb-6">
      <CardHeader
        title="Import a pull request"
        subtitle="Pull a PR in by number and review it immediately"
      />
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3 px-5 py-4">
        <Select
          label="Repository"
          className="min-w-[16rem] flex-1"
          value={repositoryId}
          onChange={setRepositoryId}
          options={[{ value: '', label: 'Select a repository…' }, ...repositoryOptions]}
        />
        <label className="block w-32">
          <span className="label">PR number</span>
          <input
            type="number"
            min={1}
            className="input"
            value={number}
            onChange={(e) => setNumber(e.target.value)}
          />
        </label>
        <button
          type="submit"
          className="btn-primary"
          disabled={mutation.isPending || !repositoryId || !number}
        >
          {mutation.isPending ? <Spinner /> : null}
          Import &amp; review
        </button>
        {mutation.isError ? (
          <p className="w-full text-xs text-red-400">
            {mutation.error instanceof Error ? mutation.error.message : 'Import failed.'}
          </p>
        ) : null}
        {mutation.isSuccess ? (
          <p className="w-full text-xs text-emerald-300">
            {mutation.data.detail}{' '}
            <Link to={`/reviews/${mutation.data.review_run_id}`} className="link">
              Open review
            </Link>
          </p>
        ) : null}
      </form>
    </Card>
  )
}

export default function PullRequestsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [state, setState] = useState('open')
  const [author, setAuthor] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  const repositoryId = searchParams.get('repository_id') ?? ''
  const repositories = useRepositories({ page_size: 100 })
  const repositoryOptions =
    repositories.data?.items.map((repo) => ({ value: repo.id, label: repo.full_name })) ?? []

  const { data, isLoading, isError, error, refetch } = usePullRequests({
    repository_id: repositoryId || undefined,
    state: state || undefined,
    author: author || undefined,
    search: search || undefined,
    page,
    page_size: 20,
  })

  const setRepository = (value: string) => {
    setSearchParams(value ? { repository_id: value } : {}, { replace: true })
    setPage(1)
  }

  return (
    <div>
      <PageHeader
        title="Pull requests"
        description="Every pull request the app has seen, with its most recent review."
      />

      <ImportCard repositoryOptions={repositoryOptions} />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          label="Repository"
          value={repositoryId}
          onChange={setRepository}
          options={[{ value: '', label: 'All repositories' }, ...repositoryOptions]}
        />
        <Select
          label="State"
          value={state}
          onChange={(value) => {
            setState(value)
            setPage(1)
          }}
          options={[
            { value: '', label: 'All' },
            { value: 'open', label: 'Open' },
            { value: 'closed', label: 'Closed' },
            { value: 'merged', label: 'Merged' },
          ]}
        />
        <SearchInput
          label="Author"
          value={author}
          onChange={(value) => {
            setAuthor(value)
            setPage(1)
          }}
          placeholder="github-login"
        />
        <SearchInput
          label="Search"
          value={search}
          onChange={(value) => {
            setSearch(value)
            setPage(1)
          }}
          placeholder="Title contains…"
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
                    <th>Pull request</th>
                    <th>Author</th>
                    <th>State</th>
                    <th>Latest review</th>
                    <th className="text-right">Findings</th>
                    <th className="text-right">Updated</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((pr) => (
                    <tr key={pr.id}>
                      <td className="max-w-[26rem]">
                        <span className="block truncate text-slate-200">{pr.title}</span>
                        <span className="block truncate text-xs text-slate-500">
                          {pr.repository_full_name ?? '—'} #{pr.github_pr_number} ·{' '}
                          <span className="text-emerald-400">+{pr.additions}</span>{' '}
                          <span className="text-red-400">−{pr.deletions}</span> ·{' '}
                          {pr.changed_files} files
                        </span>
                      </td>
                      <td className="text-xs">{pr.author}</td>
                      <td>
                        <StatusBadge status={pr.draft ? 'draft' : pr.state} />
                      </td>
                      <td>
                        {pr.latest_review_id ? (
                          <Link to={`/reviews/${pr.latest_review_id}`} className="link">
                            <RiskBadge
                              score={pr.latest_review_risk_score}
                              band={pr.latest_review_risk_band}
                            />
                          </Link>
                        ) : (
                          <span className="text-xs text-slate-500">Not reviewed</span>
                        )}
                      </td>
                      <td className="text-right tabular-nums">{pr.findings_count}</td>
                      <td className="text-right text-xs text-slate-500">
                        {formatRelative(pr.updated_at)}
                      </td>
                      <td className="text-right">
                        <ReviewButton pullRequestId={pr.id} />
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
            title="No pull requests match these filters"
            description="Import one above, or widen the filters."
          />
        )}
      </Card>
    </div>
  )
}
