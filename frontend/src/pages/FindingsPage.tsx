import { useState } from 'react'
import { Link } from 'react-router-dom'

import FindingCard from '../components/FindingCard'
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Pagination,
  SearchInput,
  Select,
} from '../components/ui'
import { useFindings, useRepositories } from '../hooks/queries'
import { SEVERITIES } from '../lib/severity'

const STATUSES = ['', 'open', 'acknowledged', 'resolved', 'dismissed']
const SOURCES = ['', 'llm', 'static', 'analyzer', 'security', 'dependency', 'test']

export default function FindingsPage() {
  const [repositoryId, setRepositoryId] = useState('')
  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('open')
  const [source, setSource] = useState('')
  const [search, setSearch] = useState('')
  const [minConfidence, setMinConfidence] = useState('')
  const [page, setPage] = useState(1)

  const repositories = useRepositories({ page_size: 100 })
  const { data, isLoading, isError, error, refetch } = useFindings({
    repository_id: repositoryId || undefined,
    severity: severity || undefined,
    status: status || undefined,
    source: source || undefined,
    search: search || undefined,
    min_confidence: minConfidence || undefined,
    page,
    page_size: 25,
  })

  const change = (setter: (value: string) => void) => (value: string) => {
    setter(value)
    setPage(1)
  }

  return (
    <div>
      <PageHeader
        title="Findings"
        description="Every validated finding across your repositories. Findings that failed diff validation never get here."
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Select
          label="Repository"
          className="xl:col-span-2"
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
          label="Severity"
          value={severity}
          onChange={change(setSeverity)}
          options={[
            { value: '', label: 'All' },
            ...SEVERITIES.map((value) => ({ value, label: value })),
          ]}
        />
        <Select
          label="Status"
          value={status}
          onChange={change(setStatus)}
          options={STATUSES.map((value) => ({ value, label: value === '' ? 'All' : value }))}
        />
        <Select
          label="Source"
          value={source}
          onChange={change(setSource)}
          options={SOURCES.map((value) => ({ value, label: value === '' ? 'All' : value }))}
        />
        <Select
          label="Min confidence"
          value={minConfidence}
          onChange={change(setMinConfidence)}
          options={[
            { value: '', label: 'Any' },
            { value: '0.5', label: '50%+' },
            { value: '0.7', label: '70%+' },
            { value: '0.9', label: '90%+' },
          ]}
        />
        <SearchInput
          label="Search"
          className="xl:col-span-2"
          value={search}
          onChange={change(setSearch)}
          placeholder="Title, description or file path"
        />
      </div>

      <Card>
        {isLoading ? (
          <LoadingBlock />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data && data.items.length > 0 ? (
          <>
            <CardHeader
              title={`${data.total} finding${data.total === 1 ? '' : 's'}`}
              subtitle="Ordered by severity, then confidence"
            />
            {data.items.map((finding) => (
              <FindingCard
                key={finding.id}
                finding={finding}
                context={
                  <>
                    <Link to={`/reviews/${finding.review_run_id}`} className="link">
                      {finding.repository_full_name ?? 'review'}
                      {finding.pull_request_number ? ` #${finding.pull_request_number}` : ''}
                    </Link>
                  </>
                }
              />
            ))}
            <Pagination
              page={data.page}
              pages={data.pages}
              total={data.total}
              onChange={setPage}
            />
          </>
        ) : (
          <EmptyState
            title="No findings match these filters"
            description="Try widening the severity or status filters."
          />
        )}
      </Card>
    </div>
  )
}
