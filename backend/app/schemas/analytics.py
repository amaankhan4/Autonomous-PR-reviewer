"""Analytics / dashboard schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CountPoint(BaseModel):
    label: str
    count: int


class TrendPoint(BaseModel):
    date: str
    reviews: int = 0
    findings: int = 0
    avg_risk: float = 0.0


class OverviewStats(BaseModel):
    repositories: int = 0
    active_repositories: int = 0
    pull_requests: int = 0
    open_pull_requests: int = 0
    reviews_total: int = 0
    reviews_completed: int = 0
    reviews_failed: int = 0
    reviews_in_progress: int = 0
    findings_total: int = 0
    findings_open: int = 0
    findings_resolved: int = 0
    critical_open: int = 0
    high_open: int = 0
    avg_risk_score: float = 0.0
    avg_review_duration_ms: int = 0
    published_reviews: int = 0
    llm_cost_usd: float = 0.0
    llm_tokens: int = 0
    demo_mode: bool = False


class AnalyticsResponse(BaseModel):
    overview: OverviewStats
    severity_breakdown: list[CountPoint] = Field(default_factory=list)
    category_breakdown: list[CountPoint] = Field(default_factory=list)
    status_breakdown: list[CountPoint] = Field(default_factory=list)
    source_breakdown: list[CountPoint] = Field(default_factory=list)
    top_repositories: list[CountPoint] = Field(default_factory=list)
    top_files: list[CountPoint] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list)
    generated_at: datetime
    window_days: int = 30


class QueueStats(BaseModel):
    queued: int = 0
    processing: int = 0
    failed_last_24h: int = 0
    completed_last_24h: int = 0
    eager_mode: bool = False
    broker_reachable: bool = False
