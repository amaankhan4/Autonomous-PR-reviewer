/**
 * React Query hooks, one per API resource.
 *
 * Query keys are structured so that a mutation can invalidate exactly the
 * slices it affects (`['reviews']` vs `['reviews', id]`).
 */

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'

import { api } from '../lib/api'
import { ACTIVE_REVIEW_STATUSES } from '../lib/severity'
import type {
  Analytics,
  Finding,
  FindingWithContext,
  HealthResponse,
  Installation,
  Page,
  PublicConfig,
  PullRequestSummary,
  QueueStats,
  Repository,
  RepositoryDetail,
  RepositoryIndex,
  RepositorySettings,
  ReviewDiff,
  ReviewEnqueued,
  ReviewRunDetail,
  ReviewRunSummary,
} from '../lib/types'

export type QueryParams = Record<string, string | number | boolean | null | undefined>

export const keys = {
  installations: ['installations'] as const,
  repositories: (params?: QueryParams) => ['repositories', params ?? {}] as const,
  repository: (id: string) => ['repositories', id] as const,
  repositoryIndexes: (id: string) => ['repositories', id, 'indexes'] as const,
  pullRequests: (params?: QueryParams) => ['pull-requests', params ?? {}] as const,
  pullRequest: (id: string) => ['pull-requests', id] as const,
  reviews: (params?: QueryParams) => ['reviews', params ?? {}] as const,
  review: (id: string) => ['reviews', id] as const,
  reviewDiff: (id: string) => ['reviews', id, 'diff'] as const,
  findings: (params?: QueryParams) => ['findings', params ?? {}] as const,
  analytics: (params?: QueryParams) => ['analytics', params ?? {}] as const,
  queueStats: ['analytics', 'queue'] as const,
  health: ['health'] as const,
  readiness: ['readiness'] as const,
  config: ['config'] as const,
}

// ------------------------------------------------------------------ installations
export function useInstallations() {
  return useQuery({
    queryKey: keys.installations,
    queryFn: () => api.get<Installation[]>('/api/v1/installations'),
  })
}

export function useConnectInstallation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (installationId?: number) =>
      api.post('/api/v1/installations/connect', {
        installation_id: installationId ?? null,
      }),
    onSuccess: () => queryClient.invalidateQueries(),
  })
}

export function useSyncInstallation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/installations/${id}/sync`),
    onSuccess: () => queryClient.invalidateQueries(),
  })
}

// -------------------------------------------------------------------- repositories
export function useRepositories(params: QueryParams = {}) {
  return useQuery({
    queryKey: keys.repositories(params),
    queryFn: () => api.get<Page<Repository>>('/api/v1/repositories', params),
    placeholderData: keepPreviousData,
  })
}

export function useRepository(id: string | undefined) {
  return useQuery({
    queryKey: keys.repository(id ?? ''),
    queryFn: () => api.get<RepositoryDetail>(`/api/v1/repositories/${id}`),
    enabled: Boolean(id),
  })
}

export function useRepositoryIndexes(id: string | undefined) {
  return useQuery({
    queryKey: keys.repositoryIndexes(id ?? ''),
    queryFn: () => api.get<Page<RepositoryIndex>>(`/api/v1/repositories/${id}/indexes`),
    enabled: Boolean(id),
  })
}

export function useUpdateRepositorySettings(repositoryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: Partial<RepositorySettings>) =>
      api.patch<RepositorySettings>(`/api/v1/repositories/${repositoryId}/settings`, patch),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: keys.repository(repositoryId) }),
  })
}

export function useReindexRepository(repositoryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (full: boolean) =>
      api.post(`/api/v1/repositories/${repositoryId}/reindex`, { full }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.repository(repositoryId) })
      void queryClient.invalidateQueries({ queryKey: keys.repositoryIndexes(repositoryId) })
    },
  })
}

export function useSetRepositoryActive(repositoryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (active: boolean) =>
      api.post(`/api/v1/repositories/${repositoryId}/activate`, undefined, { active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['repositories'] }),
  })
}

// ------------------------------------------------------------------ pull requests
export function usePullRequests(params: QueryParams = {}) {
  return useQuery({
    queryKey: keys.pullRequests(params),
    queryFn: () => api.get<Page<PullRequestSummary>>('/api/v1/pull-requests', params),
    placeholderData: keepPreviousData,
  })
}

export function usePullRequest(id: string | undefined) {
  return useQuery({
    queryKey: keys.pullRequest(id ?? ''),
    queryFn: () => api.get<PullRequestSummary>(`/api/v1/pull-requests/${id}`),
    enabled: Boolean(id),
  })
}

export function useImportPullRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { repository_id: string; number: number; review?: boolean }) =>
      api.post<ReviewEnqueued>('/api/v1/pull-requests/import', {
        review: true,
        ...input,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['pull-requests'] })
      void queryClient.invalidateQueries({ queryKey: ['reviews'] })
    },
  })
}

// ----------------------------------------------------------------------- reviews
export function useReviews(params: QueryParams = {}) {
  return useQuery({
    queryKey: keys.reviews(params),
    queryFn: () => api.get<Page<ReviewRunSummary>>('/api/v1/reviews', params),
    placeholderData: keepPreviousData,
  })
}

/**
 * A review detail that polls itself while the run is still in flight, then
 * settles into a normal cached query once it reaches a terminal state.
 */
export function useReview(id: string | undefined) {
  const options: Omit<UseQueryOptions<ReviewRunDetail>, 'queryKey' | 'queryFn'> = {
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && ACTIVE_REVIEW_STATUSES.has(status) ? 2000 : false
    },
  }
  return useQuery({
    queryKey: keys.review(id ?? ''),
    queryFn: () => api.get<ReviewRunDetail>(`/api/v1/reviews/${id}`),
    enabled: Boolean(id),
    ...options,
  })
}

export function useReviewDiff(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.reviewDiff(id ?? ''),
    queryFn: () => api.get<ReviewDiff>(`/api/v1/reviews/${id}/diff`),
    enabled: Boolean(id) && enabled,
    staleTime: 5 * 60 * 1000,
  })
}

export function useTriggerReview() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      pull_request_id?: string
      repository_id?: string
      pr_number?: number
      force?: boolean
    }) => api.post<ReviewEnqueued>('/api/v1/reviews', input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reviews'] })
      void queryClient.invalidateQueries({ queryKey: ['pull-requests'] })
    },
  })
}

export function useCancelReview() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/reviews/${id}/cancel`),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: keys.review(id) })
      void queryClient.invalidateQueries({ queryKey: ['reviews'] })
    },
  })
}

// ---------------------------------------------------------------------- findings
export function useFindings(params: QueryParams = {}) {
  return useQuery({
    queryKey: keys.findings(params),
    queryFn: () => api.get<Page<FindingWithContext>>('/api/v1/findings', params),
    placeholderData: keepPreviousData,
  })
}

export function useUpdateFindingStatus() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status, note }: { id: string; status: string; note?: string }) =>
      api.patch<Finding>(`/api/v1/findings/${id}/status`, { status, note: note ?? null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['findings'] })
      void queryClient.invalidateQueries({ queryKey: ['reviews'] })
      void queryClient.invalidateQueries({ queryKey: ['analytics'] })
    },
  })
}

// --------------------------------------------------------------------- analytics
export function useAnalytics(params: QueryParams = {}) {
  return useQuery({
    queryKey: keys.analytics(params),
    queryFn: () => api.get<Analytics>('/api/v1/analytics', params),
    placeholderData: keepPreviousData,
  })
}

export function useQueueStats() {
  return useQuery({
    queryKey: keys.queueStats,
    queryFn: () => api.get<QueueStats>('/api/v1/analytics/queue'),
    refetchInterval: 10000,
  })
}

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => api.get<HealthResponse>('/api/v1/health'),
    staleTime: 60000,
  })
}

/**
 * Readiness, unlike `/health`, actually probes each dependency, so it is what
 * the settings screen reports. It is deliberately not cached for long.
 */
export function useReadiness() {
  return useQuery({
    queryKey: keys.readiness,
    queryFn: () => api.get<HealthResponse>('/api/v1/ready'),
    refetchInterval: 30000,
  })
}

/** Public, unauthenticated configuration — safe to fetch from the login screen. */
export function useConfig() {
  return useQuery({
    queryKey: keys.config,
    queryFn: () => api.get<PublicConfig>('/api/v1/config'),
    staleTime: Infinity,
    retry: false,
  })
}
