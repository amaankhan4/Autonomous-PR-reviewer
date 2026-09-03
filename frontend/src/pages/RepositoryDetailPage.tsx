import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingBlock,
  PageHeader,
  RiskBadge,
  Spinner,
  StatusBadge,
  Stat,
  Toggle,
} from '../components/ui'
import {
  useReindexRepository,
  useRepository,
  useRepositoryIndexes,
  usePullRequests,
  useSetRepositoryActive,
  useUpdateRepositorySettings,
} from '../hooks/queries'
import { formatDateTime, formatDuration, formatNumber, shortSha } from '../lib/format'
import { SEVERITIES } from '../lib/severity'
import type { RepositorySettings } from '../lib/types'

const ANALYZERS = ['diff', 'ast', 'static', 'dependency', 'test', 'security']
const REVIEW_EVENTS = ['COMMENT', 'REQUEST_CHANGES', 'APPROVE']

function SettingsForm({
  repositoryId,
  settings,
}: {
  repositoryId: string
  settings: RepositorySettings
}) {
  const mutation = useUpdateRepositorySettings(repositoryId)
  const [draft, setDraft] = useState(settings)
  const [excluded, setExcluded] = useState(settings.excluded_paths.join('\n'))

  // The server is the source of truth: re-seed the form whenever it changes.
  useEffect(() => {
    setDraft(settings)
    setExcluded(settings.excluded_paths.join('\n'))
  }, [settings])

  const patch = <K extends keyof RepositorySettings>(key: K, value: RepositorySettings[K]) =>
    setDraft((current) => ({ ...current, [key]: value }))

  const toggleAnalyzer = (name: string) =>
    patch(
      'enabled_analyzers',
      draft.enabled_analyzers.includes(name)
        ? draft.enabled_analyzers.filter((item) => item !== name)
        : [...draft.enabled_analyzers, name],
    )

  const save = (event: React.FormEvent) => {
    event.preventDefault()
    mutation.mutate({
      auto_review_enabled: draft.auto_review_enabled,
      review_on_open: draft.review_on_open,
      review_on_synchronize: draft.review_on_synchronize,
      publish_to_github: draft.publish_to_github,
      min_severity: draft.min_severity,
      max_files_per_review: draft.max_files_per_review,
      min_confidence: draft.min_confidence,
      excluded_paths: excluded
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
      enabled_analyzers: draft.enabled_analyzers,
      llm_model: draft.llm_model || null,
      review_event: draft.review_event,
    })
  }

  return (
    <Card>
      <CardHeader title="Review settings" subtitle="Applied to every review of this repository" />
      <form onSubmit={save} className="space-y-5 px-5 py-4">
        <div className="divide-y divide-surface-border/60">
          <Toggle
            label="Automatic reviews"
            description="Review pull requests as webhooks arrive."
            checked={draft.auto_review_enabled}
            onChange={(value) => patch('auto_review_enabled', value)}
          />
          <Toggle
            label="Review when a PR opens"
            checked={draft.review_on_open}
            onChange={(value) => patch('review_on_open', value)}
            disabled={!draft.auto_review_enabled}
          />
          <Toggle
            label="Review on new commits"
            description="Re-run when the head commit changes."
            checked={draft.review_on_synchronize}
            onChange={(value) => patch('review_on_synchronize', value)}
            disabled={!draft.auto_review_enabled}
          />
          <Toggle
            label="Publish to GitHub"
            description="Post the review and inline comments back to the pull request."
            checked={draft.publish_to_github}
            onChange={(value) => patch('publish_to_github', value)}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="label">Minimum severity</span>
            <select
              className="input"
              value={draft.min_severity}
              onChange={(e) => patch('min_severity', e.target.value)}
            >
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {severity}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="label">Review event</span>
            <select
              className="input"
              value={draft.review_event}
              onChange={(e) => patch('review_event', e.target.value)}
            >
              {REVIEW_EVENTS.map((event) => (
                <option key={event} value={event}>
                  {event}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="label">Max files per review</span>
            <input
              type="number"
              min={1}
              max={300}
              className="input"
              value={draft.max_files_per_review}
              onChange={(e) => patch('max_files_per_review', Number(e.target.value))}
            />
          </label>

          <label className="block">
            <span className="label">
              Minimum confidence · {Math.round(draft.min_confidence * 100)}%
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              className="w-full accent-accent"
              value={draft.min_confidence}
              onChange={(e) => patch('min_confidence', Number(e.target.value))}
            />
          </label>

          <label className="block sm:col-span-2">
            <span className="label">
              LLM model <span className="normal-case text-slate-600">(blank = server default)</span>
            </span>
            <input
              className="input"
              value={draft.llm_model ?? ''}
              placeholder="gpt-4o-mini"
              onChange={(e) => patch('llm_model', e.target.value || null)}
            />
          </label>
        </div>

        <div>
          <span className="label">Analyzers</span>
          <div className="flex flex-wrap gap-2">
            {ANALYZERS.map((name) => {
              const on = draft.enabled_analyzers.includes(name)
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => toggleAnalyzer(name)}
                  className={
                    on
                      ? 'chip bg-accent/15 text-accent ring-1 ring-inset ring-accent/40'
                      : 'chip bg-surface-overlay text-slate-500 ring-1 ring-inset ring-surface-border'
                  }
                >
                  {name}
                </button>
              )
            })}
          </div>
        </div>

        <label className="block">
          <span className="label">
            Excluded paths <span className="normal-case text-slate-600">(one glob per line)</span>
          </span>
          <textarea
            className="input h-28 font-mono text-xs"
            value={excluded}
            placeholder={'**/migrations/**\n**/*.lock'}
            onChange={(e) => setExcluded(e.target.value)}
          />
        </label>

        {mutation.isError ? (
          <p className="text-xs text-red-400">
            {mutation.error instanceof Error ? mutation.error.message : 'Could not save.'}
          </p>
        ) : null}

        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary" disabled={mutation.isPending}>
            {mutation.isPending ? <Spinner /> : null}
            Save settings
          </button>
          {mutation.isSuccess ? (
            <span className="text-xs text-emerald-300">Saved.</span>
          ) : null}
        </div>
      </form>
    </Card>
  )
}

export default function RepositoryDetailPage() {
  const { repositoryId = '' } = useParams()
  const { data, isLoading, isError, error, refetch } = useRepository(repositoryId)
  const indexes = useRepositoryIndexes(repositoryId)
  const pullRequests = usePullRequests({ repository_id: repositoryId, page_size: 10 })
  const reindex = useReindexRepository(repositoryId)
  const setActive = useSetRepositoryActive(repositoryId)

  if (isLoading) return <LoadingBlock label="Loading repository…" />
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return <EmptyState title="Repository not found" />

  return (
    <div className="space-y-6">
      <PageHeader
        title={data.full_name}
        description={data.description ?? 'No description on GitHub.'}
        action={
          <>
            {data.html_url ? (
              <a
                className="btn-secondary"
                href={data.html_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                Open on GitHub
              </a>
            ) : null}
            <button
              type="button"
              className="btn-secondary"
              disabled={reindex.isPending}
              onClick={() => reindex.mutate(false)}
            >
              {reindex.isPending ? <Spinner /> : null}
              Re-index
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={reindex.isPending}
              onClick={() => reindex.mutate(true)}
            >
              Full re-index
            </button>
            <button
              type="button"
              className={data.is_active ? 'btn-danger' : 'btn-primary'}
              disabled={setActive.isPending}
              onClick={() => setActive.mutate(!data.is_active)}
            >
              {data.is_active ? 'Deactivate' : 'Activate'}
            </button>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Open pull requests" value={formatNumber(data.open_pull_requests)} />
        <Stat label="Reviews" value={formatNumber(data.total_reviews)} />
        <Stat
          label="Open findings"
          value={formatNumber(data.open_findings)}
          tone={data.open_findings > 0 ? 'warning' : 'success'}
        />
        <Stat
          label="Indexed files"
          value={formatNumber(data.indexed_files)}
          hint={
            data.last_indexed_at
              ? `Last indexed ${formatDateTime(data.last_indexed_at)}`
              : 'Never indexed'
          }
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          {data.settings ? (
            <SettingsForm repositoryId={repositoryId} settings={data.settings} />
          ) : (
            <Card>
              <EmptyState
                title="No settings row yet"
                description="Settings are created the first time this repository is reviewed."
              />
            </Card>
          )}

          <Card>
            <CardHeader
              title="Recent pull requests"
              action={
                <Link to={`/pull-requests?repository_id=${repositoryId}`} className="link text-xs">
                  View all
                </Link>
              }
            />
            {pullRequests.isLoading ? (
              <LoadingBlock />
            ) : pullRequests.data && pullRequests.data.items.length > 0 ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Title</th>
                      <th>State</th>
                      <th>Latest review</th>
                      <th className="text-right">Findings</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pullRequests.data.items.map((pr) => (
                      <tr key={pr.id}>
                        <td className="tabular-nums text-slate-500">{pr.github_pr_number}</td>
                        <td className="max-w-[20rem] truncate">{pr.title}</td>
                        <td>
                          <StatusBadge status={pr.state} />
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
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No pull requests tracked yet" />
            )}
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Repository" />
            <dl className="divide-y divide-surface-border/60 px-5 py-2">
              <KeyValue label="Owner">{data.owner}</KeyValue>
              <KeyValue label="Default branch">
                <span className="font-mono text-xs">{data.default_branch}</span>
              </KeyValue>
              <KeyValue label="Language">{data.language ?? '—'}</KeyValue>
              <KeyValue label="Visibility">{data.private ? 'Private' : 'Public'}</KeyValue>
              <KeyValue label="Index status">
                <StatusBadge status={data.index_status} />
              </KeyValue>
              <KeyValue label="GitHub id">
                <span className="font-mono text-xs">{data.github_repo_id}</span>
              </KeyValue>
            </dl>
          </Card>

          <Card>
            <CardHeader title="Index history" subtitle="Most recent runs" />
            {indexes.isLoading ? (
              <LoadingBlock />
            ) : indexes.data && indexes.data.items.length > 0 ? (
              <ul className="divide-y divide-surface-border/60">
                {indexes.data.items.slice(0, 8).map((run) => (
                  <li key={run.id} className="px-5 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-xs text-slate-400">
                        {shortSha(run.commit_sha)}
                      </span>
                      <StatusBadge status={run.status} />
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      {run.incremental ? 'Incremental' : 'Full'} · {run.files_indexed} files ·{' '}
                      {run.chunks_indexed} chunks · {formatDuration(run.duration_ms)}
                    </p>
                    {run.error_message ? (
                      <p className="mt-1 text-xs text-red-400">{run.error_message}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Never indexed" description="Run an index to enable retrieval." />
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
