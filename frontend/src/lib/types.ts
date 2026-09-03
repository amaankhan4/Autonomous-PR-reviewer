/**
 * API response types.
 *
 * These mirror the Pydantic schemas in `backend/app/schemas`. They are written
 * by hand rather than generated so the dashboard stays readable, but the names
 * match the backend exactly to make drift obvious.
 */

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface User {
  id: string
  email: string
  username: string
  full_name: string | null
  github_login: string | null
  avatar_url: string | null
  is_active: boolean
  is_superuser: boolean
  created_at: string
}

export interface Installation {
  id: string
  installation_id: number
  account_login: string
  account_type: string
  avatar_url: string | null
  is_active: boolean
  suspended: boolean
  repository_count: number
  created_at: string
}

export interface AuthContextPayload {
  user: User
  installations: Installation[]
  demo_mode: boolean
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface Repository {
  id: string
  github_repo_id: number
  installation_id: string
  owner: string
  name: string
  full_name: string
  description: string | null
  default_branch: string
  private: boolean
  language: string | null
  html_url: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface RepositorySettings {
  id: string
  repository_id: string
  auto_review_enabled: boolean
  review_on_open: boolean
  review_on_synchronize: boolean
  publish_to_github: boolean
  min_severity: string
  max_files_per_review: number
  min_confidence: number
  excluded_paths: string[]
  enabled_analyzers: string[]
  llm_model: string | null
  review_event: string
  updated_at: string
}

export interface RepositoryDetail extends Repository {
  settings: RepositorySettings | null
  open_pull_requests: number
  total_reviews: number
  open_findings: number
  last_indexed_at: string | null
  index_status: string | null
  indexed_files: number
}

export interface RepositoryIndex {
  id: string
  repository_id: string
  commit_sha: string
  status: string
  files_indexed: number
  files_skipped: number
  chunks_indexed: number
  chunks_deleted: number
  incremental: boolean
  duration_ms: number | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface PullRequest {
  id: string
  repository_id: string
  github_pr_number: number
  title: string
  author: string
  author_avatar_url: string | null
  base_branch: string | null
  head_branch: string | null
  base_sha: string
  head_sha: string
  state: string
  draft: boolean
  html_url: string | null
  additions: number
  deletions: number
  changed_files: number
  opened_at: string | null
  created_at: string
  updated_at: string
}

export interface PullRequestSummary extends PullRequest {
  repository_full_name: string | null
  body: string | null
  latest_review_id: string | null
  latest_review_status: string | null
  latest_review_risk_score: number | null
  latest_review_risk_band: string | null
  findings_count: number
  review_count: number
}

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'
export type FindingStatus = 'open' | 'resolved' | 'dismissed' | 'acknowledged'
export type RiskBand = 'low' | 'moderate' | 'elevated' | 'high' | 'critical'

export interface Finding {
  id: string
  review_run_id: string
  fingerprint: string
  file_path: string
  line_number: number | null
  start_line: number | null
  end_line: number | null
  severity: Severity
  category: string
  title: string
  description: string
  evidence: string | null
  code_snippet: string | null
  suggested_fix: string | null
  confidence: number
  confidence_label: string
  source: string
  rule_id: string | null
  references: unknown[] | null
  related_symbols: unknown[] | null
  historical_context: Record<string, unknown> | null
  status: FindingStatus
  published: boolean
  resolution_note: string | null
  resolved_at: string | null
  created_at: string
}

export interface FindingWithContext extends Finding {
  repository_full_name: string | null
  pull_request_number: number | null
  commit_sha: string | null
}

export interface ReviewComment {
  id: string
  kind: string
  file_path: string | null
  line_number: number | null
  body: string
  github_comment_id: number | null
  github_url: string | null
  delivered: boolean
  error_message: string | null
  created_at: string
}

export interface LLMUsage {
  id: string
  provider: string
  model: string
  operation: string
  input_tokens: number
  output_tokens: number
  latency_ms: number
  estimated_cost_usd: number
  success: boolean
  error_message: string | null
  created_at: string
}

export interface ReviewRun {
  id: string
  pull_request_id: string
  commit_sha: string
  trigger: string
  status: string
  stage: string | null
  progress: number
  risk_score: number
  risk_band: RiskBand | null
  files_analyzed: number
  files_skipped: number
  findings_count: number
  suppressed_count: number
  summary: string | null
  published: boolean
  github_review_url: string | null
  attempts: number
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  created_at: string
}

export interface ReviewRunSummary extends ReviewRun {
  repository_id: string | null
  repository_full_name: string | null
  pull_request_number: number | null
  pull_request_title: string | null
  pull_request_author: string | null
}

export interface RiskFactor {
  key: string
  label: string
  points: number
  detail: string
  evidence: string[]
}

export interface ReviewRunDetail extends ReviewRunSummary {
  risk_factors: RiskFactor[] | null
  analysis: Record<string, unknown> | null
  static_findings: unknown[] | null
  context_stats: Record<string, unknown> | null
  findings: Finding[]
  comments: ReviewComment[]
  llm_usage: LLMUsage[]
}

export interface ReviewProgress {
  review_run_id: string
  status: string
  stage: string | null
  progress: number
  findings_count: number
  error_message: string | null
  updated_at: string
}

export interface ReviewDiffFile {
  filename: string
  status: string
  additions: number
  deletions: number
  changes: number
  patch: string | null
  language: string | null
  findings: Finding[]
}

export interface ReviewDiff {
  review_run_id: string
  commit_sha: string
  files: ReviewDiffFile[]
  truncated: boolean
}

export interface ReviewEnqueued {
  review_run_id: string
  status: string
  created: boolean
  task_id: string | null
  detail: string
}

export interface CountPoint {
  label: string
  count: number
}

export interface TrendPoint {
  date: string
  reviews: number
  findings: number
  avg_risk: number
}

export interface OverviewStats {
  repositories: number
  active_repositories: number
  pull_requests: number
  open_pull_requests: number
  reviews_total: number
  reviews_completed: number
  reviews_failed: number
  reviews_in_progress: number
  findings_total: number
  findings_open: number
  findings_resolved: number
  critical_open: number
  high_open: number
  avg_risk_score: number
  avg_review_duration_ms: number
  published_reviews: number
  llm_cost_usd: number
  llm_tokens: number
  demo_mode: boolean
}

export interface Analytics {
  overview: OverviewStats
  severity_breakdown: CountPoint[]
  category_breakdown: CountPoint[]
  status_breakdown: CountPoint[]
  source_breakdown: CountPoint[]
  top_repositories: CountPoint[]
  top_files: CountPoint[]
  trend: TrendPoint[]
  generated_at: string
  window_days: number
}

export interface QueueStats {
  queued: number
  processing: number
  failed_last_24h: number
  completed_last_24h: number
  eager_mode: boolean
  broker_reachable: boolean
}

export interface HealthResponse {
  status: string
  version: string
  environment: string
  demo_mode: boolean
  checks: Record<string, unknown>
  timestamp: string
}

export interface PublicConfig {
  app_name: string
  version: string
  environment: string
  demo_mode: boolean
  mock_github: boolean
  llm_provider: string
  llm_model: string
  vector_store: string
  embedding_provider: string
  publish_reviews: boolean
  default_review_event: string
  enabled_analyzers: string[]
  github_app_slug: string | null
  demo_credentials: { email: string; password: string } | null
}
