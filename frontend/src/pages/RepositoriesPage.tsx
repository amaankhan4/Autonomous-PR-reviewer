import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Pagination,
  SearchInput,
  Select,
  Spinner,
} from '../components/ui'
import {
  useConnectInstallation,
  useInstallations,
  useRepositories,
  useSetRepositoryActive,
} from '../hooks/queries'
import { formatRelative } from '../lib/format'

function ActiveToggleCell({ id, active }: { id: string; active: boolean }) {
  const mutation = useSetRepositoryActive(id)
  return (
    <button
      type="button"
      className={active ? 'btn-secondary px-2.5 py-1 text-xs' : 'btn-primary px-2.5 py-1 text-xs'}
      disabled={mutation.isPending}
      onClick={() => mutation.mutate(!active)}
    >
      {mutation.isPending ? <Spinner className="h-3 w-3" /> : null}
      {active ? 'Deactivate' : 'Activate'}
    </button>
  )
}

export default function RepositoriesPage() {
  const [search, setSearch] = useState('')
  const [activeFilter, setActiveFilter] = useState('')
  const [page, setPage] = useState(1)

  const params = {
    search: search || undefined,
    is_active: activeFilter === '' ? undefined : activeFilter === 'true',
    page,
    page_size: 20,
  }
  const { data, isLoading, isError, error, refetch } = useRepositories(params)
  const installations = useInstallations()
  const connect = useConnectInstallation()

  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setPage(1)
  }

  return (
    <div>
      <PageHeader
        title="Repositories"
        description="Repositories reachable through your GitHub App installations."
        action={
          <button
            type="button"
            className="btn-primary"
            disabled={connect.isPending}
            onClick={() => connect.mutate(undefined)}
          >
            {connect.isPending ? <Spinner /> : null}
            {installations.data && installations.data.length > 0
              ? 'Sync repositories'
              : 'Connect installation'}
          </button>
        }
      />

      {connect.isError ? (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {connect.error instanceof Error ? connect.error.message : 'Could not connect.'}
        </div>
      ) : null}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SearchInput
          label="Search"
          value={search}
          onChange={resetPage(setSearch)}
          placeholder="owner/name"
        />
        <Select
          label="State"
          value={activeFilter}
          onChange={resetPage(setActiveFilter)}
          options={[
            { value: '', label: 'All' },
            { value: 'true', label: 'Active' },
            { value: 'false', label: 'Inactive' },
          ]}
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
                    <th>Repository</th>
                    <th>Language</th>
                    <th>Visibility</th>
                    <th>Default branch</th>
                    <th>Updated</th>
                    <th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((repo) => (
                    <tr key={repo.id}>
                      <td className="max-w-[24rem]">
                        <Link to={`/repositories/${repo.id}`} className="link block truncate">
                          {repo.full_name}
                        </Link>
                        {repo.description ? (
                          <span className="block truncate text-xs text-slate-500">
                            {repo.description}
                          </span>
                        ) : null}
                      </td>
                      <td className="text-xs">{repo.language ?? '—'}</td>
                      <td className="text-xs">{repo.private ? 'Private' : 'Public'}</td>
                      <td className="font-mono text-xs">{repo.default_branch}</td>
                      <td className="text-xs text-slate-500">{formatRelative(repo.updated_at)}</td>
                      <td className="text-right">
                        <ActiveToggleCell id={repo.id} active={repo.is_active} />
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
            title="No repositories yet"
            description="Connect a GitHub App installation to pull your repositories in. In demo mode this seeds a sample repository instead."
            action={
              <button
                type="button"
                className="btn-primary"
                disabled={connect.isPending}
                onClick={() => connect.mutate(undefined)}
              >
                Connect installation
              </button>
            }
          />
        )}
      </Card>
    </div>
  )
}
