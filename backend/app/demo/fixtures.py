"""Demo repository fixtures.

These power ``MOCK_GITHUB=true`` so the whole product -- webhook, queue, worker,
analyzers, retrieval, review engine, dashboard -- can be exercised end to end
without a GitHub App or a paid LLM key.

The demo pull request deliberately contains the six classes of bug listed in the
product specification so the reviewer has something real to find:

1. unbounded retry loop
2. SQL built by string interpolation
3. missing authorization check on a state-changing endpoint
4. new behaviour shipped without regression tests
5. file handle never closed
6. read-modify-write race on shared state
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

DEMO_INSTALLATION_ID = 40404040
DEMO_ACCOUNT = "acme-corp"

BASE_SHA = "9f1c4d2b7a5e3f8c6d0b1a2e4f7c9d3b6a8e5f10"
HEAD_SHA = "3c7e9a1f5d8b2c4e6a0f9d7b3e1c5a8f2d4b6e90"
WEB_BASE_SHA = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
WEB_HEAD_SHA = "11aa22bb33cc44dd55ee66ff778899aabbccddee"


# --------------------------------------------------------------------- files
_RETRY_POLICY = '''"""Shared retry helpers used across the payments service."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff.

    Every outbound call in this service is expected to go through a
    RetryPolicy so that a failing dependency can never wedge a worker.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 5.0

    def delay_for(self, attempt: int) -> float:
        delay = self.base_delay_seconds * (2 ** max(0, attempt - 1))
        return min(delay, self.max_delay_seconds)

    def sleep(self, attempt: int) -> None:
        time.sleep(self.delay_for(attempt))


DEFAULT_RETRY_POLICY = RetryPolicy(max_attempts=3)
'''

_PAYMENT_REPOSITORY = '''"""Database access for payments."""

from __future__ import annotations

from typing import Any


class PaymentRepository:
    """All payment persistence goes through parameterised SQL."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def get_by_reference(self, reference: str) -> dict[str, Any] | None:
        cursor = self._connection.cursor()
        cursor.execute(
            "SELECT id, user_id, amount, status FROM payments WHERE reference = %s",
            (reference,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def insert(self, user_id: str, amount: int, reference: str) -> str:
        cursor = self._connection.cursor()
        cursor.execute(
            "INSERT INTO payments (user_id, amount, reference, status) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, amount, reference, "pending"),
        )
        return cursor.fetchone()[0]

    def mark_settled(self, payment_id: str) -> None:
        cursor = self._connection.cursor()
        cursor.execute(
            "UPDATE payments SET status = %s WHERE id = %s", ("settled", payment_id)
        )
'''

_PAYMENT_SERVICE_BASE = '''"""Payment orchestration."""

from __future__ import annotations

import logging

from app.services.retry_policy import DEFAULT_RETRY_POLICY

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, gateway, repository):
        self._gateway = gateway
        self._repository = repository

    def process_payment(self, user_id: str, amount: int, reference: str) -> dict:
        """Charge the customer via the upstream gateway."""
        existing = self._repository.get_by_reference(reference)
        if existing is not None:
            return existing

        policy = DEFAULT_RETRY_POLICY
        last_error: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return self._gateway.charge(user_id=user_id, amount=amount)
            except TimeoutError as exc:
                last_error = exc
                logger.warning("gateway timeout, attempt %s", attempt)
                policy.sleep(attempt)

        raise RuntimeError("payment gateway unavailable") from last_error
'''

_PAYMENT_SERVICE_HEAD = '''"""Payment orchestration."""

from __future__ import annotations

import hashlib
import logging
import threading

from app.services.retry_policy import DEFAULT_RETRY_POLICY

logger = logging.getLogger(__name__)

GATEWAY_API_KEY = "gw_9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"

_inflight_payments = 0
_audit_handle = None


class PaymentService:
    def __init__(self, gateway, repository, connection=None):
        self._gateway = gateway
        self._repository = repository
        self._connection = connection

    def process_payment(self, user_id: str, amount: int, reference: str) -> dict:
        """Charge the customer via the upstream gateway."""
        global _inflight_payments

        existing = self._repository.get_by_reference(reference)
        if existing is not None:
            return existing

        _inflight_payments = _inflight_payments + 1

        attempt = 0
        while True:
            attempt += 1
            try:
                result = self._gateway.charge(user_id=user_id, amount=amount)
                self._write_audit_line(reference, result)
                return result
            except TimeoutError:
                logger.warning("gateway timeout, retrying attempt %s", attempt)
                DEFAULT_RETRY_POLICY.sleep(attempt)

    def find_payments_for_user(self, username: str) -> list[dict]:
        """Look up every payment belonging to a username."""
        cursor = self._connection.cursor()
        cursor.execute(
            f"SELECT id, amount, status FROM payments WHERE username = '{username}'"
        )
        return [dict(row) for row in cursor.fetchall()]

    def _write_audit_line(self, reference: str, result: dict) -> None:
        global _audit_handle
        if _audit_handle is None:
            _audit_handle = open("/var/log/payments/audit.log", "a")
        _audit_handle.write(f"{reference}:{result.get('id')}\\n")

    def signature_for(self, payload: str) -> str:
        return hashlib.md5(payload.encode()).hexdigest()
'''

_PAYMENT_ROUTES_BASE = '''"""Payment HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, get_payment_service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/{reference}")
async def read_payment(reference: str, user=Depends(get_current_user), service=Depends(get_payment_service)):
    return service.get_payment(reference, owner_id=user.id)
'''

_PAYMENT_ROUTES_HEAD = '''"""Payment HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, get_payment_service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/{reference}")
async def read_payment(reference: str, user=Depends(get_current_user), service=Depends(get_payment_service)):
    return service.get_payment(reference, owner_id=user.id)


@router.post("/{reference}/refund")
async def refund_payment(reference: str, service=Depends(get_payment_service)):
    """Refund a payment."""
    return service.refund(reference)
'''

_TEST_PAYMENT_SERVICE = '''"""Tests for PaymentService."""

from __future__ import annotations

import pytest

from app.services.payment_service import PaymentService


class StubGateway:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    def charge(self, user_id: str, amount: int) -> dict:
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("gateway timeout")
        return {"id": "pay_123", "status": "settled"}


class StubRepository:
    def __init__(self, existing=None) -> None:
        self.existing = existing

    def get_by_reference(self, reference: str):
        return self.existing


def test_process_payment_is_idempotent():
    repository = StubRepository(existing={"id": "pay_123"})
    service = PaymentService(StubGateway(), repository)
    assert service.process_payment("user_1", 500, "ref-1") == {"id": "pay_123"}


def test_process_payment_retries_then_succeeds():
    gateway = StubGateway(failures=2)
    service = PaymentService(gateway, StubRepository())
    assert service.process_payment("user_1", 500, "ref-2")["status"] == "settled"


def test_process_payment_gives_up_after_max_attempts():
    gateway = StubGateway(failures=99)
    service = PaymentService(gateway, StubRepository())
    with pytest.raises(RuntimeError):
        service.process_payment("user_1", 500, "ref-3")
'''

_ARCHITECTURE_MD = '''# Payments service architecture

## Conventions

* Every outbound network call MUST go through `RetryPolicy` (see
  `app/services/retry_policy.py`). Unbounded retry loops are not permitted --
  an earlier incident wedged all workers when the gateway was down.
* All SQL MUST use bind parameters. String interpolation into SQL is banned.
* Every state-changing HTTP route MUST declare `Depends(get_current_user)` and
  verify resource ownership.
* File handles and sockets MUST be opened with a context manager.
* Module-level mutable state MUST be guarded by a lock.

## Layers

```
routes -> services -> repositories -> database
```
'''

_README = '''# payments-api

Payment orchestration service for Acme Corp.

See `docs/architecture.md` for the engineering conventions that apply to
every change in this repository.
'''

_WEB_API_CLIENT_BASE = '''export interface Payment {
  id: string;
  amount: number;
  status: string;
}

export async function fetchPayment(reference: string): Promise<Payment> {
  const response = await fetch(`/api/payments/${reference}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Failed to load payment ${reference}`);
  }
  return response.json();
}
'''

_WEB_API_CLIENT_HEAD = '''export interface Payment {
  id: string;
  amount: number;
  status: string;
}

const ADMIN_TOKEN = "ghp_examplehardcodedtokenvalue1234567890";

export async function fetchPayment(reference: string): Promise<Payment> {
  const response = await fetch(`/api/payments/${reference}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Failed to load payment ${reference}`);
  }
  return response.json();
}

export function renderReceipt(container: HTMLElement, html: string): void {
  container.innerHTML = html;
}

export async function refundPayment(reference: string): Promise<void> {
  await fetch(`/api/payments/${reference}/refund`, {
    method: "POST",
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
}
'''


PAYMENTS_BASE_TREE: dict[str, str] = {
    "README.md": _README,
    "docs/architecture.md": _ARCHITECTURE_MD,
    "app/services/retry_policy.py": _RETRY_POLICY,
    "app/services/payment_service.py": _PAYMENT_SERVICE_BASE,
    "app/repositories/payment_repository.py": _PAYMENT_REPOSITORY,
    "app/api/routes/payments.py": _PAYMENT_ROUTES_BASE,
    "tests/test_payment_service.py": _TEST_PAYMENT_SERVICE,
}

PAYMENTS_HEAD_TREE: dict[str, str] = {
    **PAYMENTS_BASE_TREE,
    "app/services/payment_service.py": _PAYMENT_SERVICE_HEAD,
    "app/api/routes/payments.py": _PAYMENT_ROUTES_HEAD,
}

WEB_BASE_TREE: dict[str, str] = {
    "README.md": "# web-dashboard\n\nAcme customer dashboard.\n",
    "src/api/payments.ts": _WEB_API_CLIENT_BASE,
}

WEB_HEAD_TREE: dict[str, str] = {
    **WEB_BASE_TREE,
    "src/api/payments.ts": _WEB_API_CLIENT_HEAD,
}


# ----------------------------------------------------------------- entities
@dataclass(slots=True)
class DemoRepository:
    github_repo_id: int
    owner: str
    name: str
    default_branch: str
    description: str
    language: str
    base_tree: dict[str, str]
    head_tree: dict[str, str]
    base_sha: str
    head_sha: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def tree_for(self, ref: str) -> dict[str, str]:
        return self.head_tree if ref == self.head_sha else self.base_tree


@dataclass(slots=True)
class DemoPullRequest:
    repo_full_name: str
    number: int
    title: str
    body: str
    author: str
    base_sha: str
    head_sha: str
    base_branch: str = "main"
    head_branch: str = "feature"
    state: str = "open"
    changed_paths: list[str] = field(default_factory=list)


DEMO_REPOSITORIES: list[DemoRepository] = [
    DemoRepository(
        github_repo_id=901234501,
        owner=DEMO_ACCOUNT,
        name="payments-api",
        default_branch="main",
        description="Payment orchestration service",
        language="Python",
        base_tree=PAYMENTS_BASE_TREE,
        head_tree=PAYMENTS_HEAD_TREE,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    ),
    DemoRepository(
        github_repo_id=901234502,
        owner=DEMO_ACCOUNT,
        name="web-dashboard",
        default_branch="main",
        description="Customer facing dashboard",
        language="TypeScript",
        base_tree=WEB_BASE_TREE,
        head_tree=WEB_HEAD_TREE,
        base_sha=WEB_BASE_SHA,
        head_sha=WEB_HEAD_SHA,
    ),
]

DEMO_PULL_REQUESTS: list[DemoPullRequest] = [
    DemoPullRequest(
        repo_full_name=f"{DEMO_ACCOUNT}/payments-api",
        number=142,
        title="Add payment retry mechanism and user payment lookup",
        body=(
            "Retries gateway timeouts so transient upstream failures stop paging on-call, "
            "adds a username-based payment lookup for support, and writes an audit line "
            "for every settled charge.\n\n"
            "- retry on `TimeoutError`\n"
            "- `find_payments_for_user`\n"
            "- refund endpoint\n"
        ),
        author="dana-eng",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        head_branch="feat/payment-retries",
        changed_paths=[
            "app/services/payment_service.py",
            "app/api/routes/payments.py",
        ],
    ),
    DemoPullRequest(
        repo_full_name=f"{DEMO_ACCOUNT}/web-dashboard",
        number=87,
        title="Render payment receipts and add refund action",
        body="Adds receipt rendering plus a refund button to the payment detail drawer.",
        author="sam-frontend",
        base_sha=WEB_BASE_SHA,
        head_sha=WEB_HEAD_SHA,
        head_branch="feat/receipt-rendering",
        changed_paths=["src/api/payments.ts"],
    ),
]


def make_patch(path: str, before: str | None, after: str | None) -> str | None:
    """Build a GitHub-style patch (hunks only, no ``diff --git`` header)."""
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(before_lines, after_lines, fromfile=path, tofile=path, n=3)
    )
    if not diff:
        return None
    # Strip the ---/+++ file headers; GitHub's `patch` field starts at the first hunk.
    body = [line.rstrip("\n") for line in diff[2:]]
    return "\n".join(body)


def changed_files_for(repo: DemoRepository, pr: DemoPullRequest) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    paths = pr.changed_paths or sorted(
        set(repo.base_tree) | set(repo.head_tree)
    )
    for path in paths:
        before = repo.base_tree.get(path)
        after = repo.head_tree.get(path)
        if before == after:
            continue
        patch = make_patch(path, before, after)
        if patch is None:
            continue
        additions = sum(
            1 for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1 for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")
        )
        status = "added" if before is None else "removed" if after is None else "modified"
        entries.append(
            {
                "filename": path,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "changes": additions + deletions,
                "patch": patch,
            }
        )
    return entries


def unified_diff_for(repo: DemoRepository, pr: DemoPullRequest) -> str:
    chunks: list[str] = []
    for entry in changed_files_for(repo, pr):
        path = entry["filename"]
        chunks.append(f"diff --git a/{path} b/{path}")
        chunks.append(f"--- a/{path}")
        chunks.append(f"+++ b/{path}")
        chunks.append(entry["patch"])
    return "\n".join(chunks)


def find_repository(full_name: str) -> DemoRepository | None:
    for repo in DEMO_REPOSITORIES:
        if repo.full_name == full_name:
            return repo
    return None


def find_pull_request(full_name: str, number: int) -> DemoPullRequest | None:
    for pr in DEMO_PULL_REQUESTS:
        if pr.repo_full_name == full_name and pr.number == number:
            return pr
    return None
