import {
  Card,
  CardHeader,
  EmptyState,
  KeyValue,
  LoadingBlock,
  PageHeader,
  Spinner,
  StatusBadge,
} from '../components/ui'
import {
  useConfig,
  useConnectInstallation,
  useInstallations,
  useQueueStats,
  useReadiness,
  useSyncInstallation,
} from '../hooks/queries'
import { useAuth } from '../hooks/useAuth'
import { formatDateTime, titleCase } from '../lib/format'

function InstallationRow({ id, login, repos }: { id: string; login: string; repos: number }) {
  const sync = useSyncInstallation()
  return (
    <li className="flex items-center justify-between gap-4 px-5 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm text-slate-200">{login}</p>
        <p className="text-xs text-slate-500">
          {repos} repositor{repos === 1 ? 'y' : 'ies'}
        </p>
      </div>
      <button
        type="button"
        className="btn-secondary px-2.5 py-1 text-xs"
        disabled={sync.isPending}
        onClick={() => sync.mutate(id)}
      >
        {sync.isPending ? <Spinner className="h-3 w-3" /> : null}
        Sync
      </button>
    </li>
  )
}

function HealthChecks({ checks }: { checks: Record<string, unknown> }) {
  const entries = Object.entries(checks)
  if (entries.length === 0) return null
  return (
    <ul className="divide-y divide-surface-border/60 px-5 py-2">
      {entries.map(([name, value]) => {
        const record = (value ?? {}) as Record<string, unknown>
        const ok = Boolean(record.ok)
        // Everything except the verdict itself is useful detail (provider,
        // backend, model, url scheme, error), so render whatever is present.
        const detail = Object.entries(record)
          .filter(([key, item]) => key !== 'ok' && item !== null && item !== '')
          .map(([, item]) => String(item))
          .join(' · ')
        return (
          <li key={name} className="flex items-center justify-between gap-4 py-2">
            <span className="text-sm text-slate-300">{titleCase(name)}</span>
            <span className="flex min-w-0 items-center gap-2 text-xs">
              {detail ? <span className="truncate text-slate-500">{detail}</span> : null}
              <span className={ok ? 'shrink-0 text-emerald-400' : 'shrink-0 text-red-400'}>
                {ok ? 'ok' : 'unavailable'}
              </span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export default function SettingsPage() {
  const { user, demoMode } = useAuth()
  const config = useConfig()
  const health = useReadiness()
  const installations = useInstallations()
  const queue = useQueueStats()
  const connect = useConnectInstallation()

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="How this deployment is configured, and which GitHub installations you are connected to."
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Account" />
          <dl className="divide-y divide-surface-border/60 px-5 py-2">
            <KeyValue label="Name">{user?.full_name || user?.username || '—'}</KeyValue>
            <KeyValue label="Email">{user?.email ?? '—'}</KeyValue>
            <KeyValue label="Username">{user?.username ?? '—'}</KeyValue>
            <KeyValue label="GitHub login">{user?.github_login ?? 'Not linked'}</KeyValue>
            <KeyValue label="Role">{user?.is_superuser ? 'Superuser' : 'Member'}</KeyValue>
            <KeyValue label="Member since">{formatDateTime(user?.created_at)}</KeyValue>
          </dl>
        </Card>

        <Card>
          <CardHeader
            title="GitHub installations"
            action={
              <button
                type="button"
                className="btn-secondary px-2.5 py-1 text-xs"
                disabled={connect.isPending}
                onClick={() => connect.mutate(undefined)}
              >
                {connect.isPending ? <Spinner className="h-3 w-3" /> : null}
                Connect
              </button>
            }
          />
          {installations.isLoading ? (
            <LoadingBlock />
          ) : installations.data && installations.data.length > 0 ? (
            <ul className="divide-y divide-surface-border/60">
              {installations.data.map((installation) => (
                <InstallationRow
                  key={installation.id}
                  id={installation.id}
                  login={installation.account_login}
                  repos={installation.repository_count}
                />
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No installations connected"
              description={
                demoMode
                  ? 'Connect to seed the demo organisation and its sample repositories.'
                  : 'Install the GitHub App on an organisation, then connect it here.'
              }
            />
          )}
        </Card>

        <Card>
          <CardHeader title="Deployment" subtitle="Read-only, from the server" />
          {config.isLoading ? (
            <LoadingBlock />
          ) : config.data ? (
            <dl className="divide-y divide-surface-border/60 px-5 py-2">
              <KeyValue label="Version">{config.data.version}</KeyValue>
              <KeyValue label="Environment">{config.data.environment}</KeyValue>
              <KeyValue label="Demo mode">{config.data.demo_mode ? 'On' : 'Off'}</KeyValue>
              <KeyValue label="GitHub">
                {config.data.mock_github ? 'Mock provider' : 'Real GitHub App'}
              </KeyValue>
              <KeyValue label="LLM">
                {config.data.llm_provider} · {config.data.llm_model}
              </KeyValue>
              <KeyValue label="Vector store">{config.data.vector_store}</KeyValue>
              <KeyValue label="Embeddings">{config.data.embedding_provider}</KeyValue>
              <KeyValue label="Publishing">
                {config.data.publish_reviews
                  ? `Enabled (${config.data.default_review_event})`
                  : 'Disabled'}
              </KeyValue>
              <KeyValue label="Analyzers">
                {config.data.enabled_analyzers.join(', ')}
              </KeyValue>
            </dl>
          ) : (
            <EmptyState title="Configuration unavailable" />
          )}
        </Card>

        <Card>
          <CardHeader
            title="System health"
            action={
              health.data ? <StatusBadge status={health.data.status === 'ok' ? 'completed' : 'failed'} /> : null
            }
          />
          {health.isLoading ? (
            <LoadingBlock />
          ) : health.data ? (
            <>
              <HealthChecks checks={health.data.checks} />
              {queue.data ? (
                <dl className="divide-y divide-surface-border/60 border-t border-surface-border px-5 py-2">
                  <KeyValue label="Queued">{queue.data.queued}</KeyValue>
                  <KeyValue label="Processing">{queue.data.processing}</KeyValue>
                  <KeyValue label="Completed (24h)">{queue.data.completed_last_24h}</KeyValue>
                  <KeyValue label="Failed (24h)">{queue.data.failed_last_24h}</KeyValue>
                  <KeyValue label="Execution">
                    {queue.data.eager_mode ? 'Inline (eager)' : 'Celery workers'}
                  </KeyValue>
                </dl>
              ) : null}
            </>
          ) : (
            <EmptyState title="Health unavailable" />
          )}
        </Card>
      </div>

      <Card>
        <CardHeader title="About automated review" />
        <div className="space-y-3 px-5 py-4 text-sm leading-relaxed text-slate-400">
          <p>
            Findings are produced by deterministic analyzers and an LLM, then validated
            against the real diff. Anything that references a file or line the pull request
            did not touch is discarded before it reaches this dashboard.
          </p>
          <p>
            The risk score is the sum of named factors — it is computed in code, not chosen by
            the model, so the same diff always scores the same.
          </p>
          <p className="text-slate-500">
            This tool is advisory. It does not replace human review, and it will not block a
            merge on its own.
          </p>
        </div>
      </Card>
    </div>
  )
}
