#!/usr/bin/env python
"""End-to-end smoke test for a running Autonomous PR Reviewer deployment.

Unlike the unit suite, this script talks to a *real* API process, a *real*
Celery worker and a *real* broker. It drives the whole product the way the
dashboard does: log in, enqueue a review, wait for the worker to run the
analyzers and the LLM, then assert the published result is coherent.

The most important assertion is the last one about findings: every finding must
anchor to a file the pull request actually touched. That is the property the
validation layer exists to guarantee, and it is the one that would silently
regress if the LLM started hallucinating.

Usage::

    python scripts/e2e_smoke.py                                  # localhost:8000
    python scripts/e2e_smoke.py --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class Checker:
    """Collects pass/fail results so the whole run reports at once."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.total = 0

    def __call__(self, label: str, condition: bool, detail: str = "") -> bool:
        self.total += 1
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {label}{(' -> ' + detail) if detail else ''}", flush=True)
        if not condition:
            self.failures.append(label)
        return condition


def run(base_url: str, review_timeout: float) -> int:
    check = Checker()

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        # -------------------------------------------------- public surface
        config = client.get("/api/v1/config").json()
        creds = config.get("demo_credentials")
        check("public config exposes demo credentials", bool(creds))
        if not creds:
            print("\nCannot continue without demo credentials (SEED_DEMO_DATA=false?)")
            return 1

        health = client.get("/api/v1/health").json()
        check("liveness probe reports ok", health["status"] == "ok")

        # /ready is the probe that actually touches dependencies; /health is
        # deliberately dependency-free so it can never flap.
        ready = client.get("/api/v1/ready").json()
        unhealthy = sorted(
            name for name, entry in (ready.get("checks") or {}).items() if not entry.get("ok")
        )
        check("readiness probe reports all dependencies healthy", not unhealthy, ", ".join(unhealthy))

        # ------------------------------------------------------------ auth
        tokens = client.post(
            "/api/v1/auth/login",
            json={"email": creds["email"], "password": creds["password"]},
        ).json()
        if not check("demo login returns a bearer token", "access_token" in tokens, str(tokens)[:200]):
            return 1
        client.headers["Authorization"] = f"Bearer {tokens['access_token']}"

        me = client.get("/api/v1/auth/me").json()
        check("auth context returns user", bool(me["user"]["email"]), me["user"]["email"])
        check("auth context returns installations", len(me["installations"]) > 0)

        # ---------------------------------------------------------- domain
        repos = client.get("/api/v1/repositories", params={"page_size": 50}).json()
        if not check("repositories seeded", repos["total"] > 0, f"{repos['total']} repos"):
            return 1
        repo = repos["items"][0]

        detail = client.get(f"/api/v1/repositories/{repo['id']}").json()
        check("repository detail has settings", detail["settings"] is not None)

        prs = client.get("/api/v1/pull-requests", params={"page_size": 50}).json()
        if not check("pull requests seeded", prs["total"] > 0, f"{prs['total']} PRs"):
            return 1
        pr = prs["items"][0]

        # ------------------------------------------- the review round trip
        enqueued = client.post(
            "/api/v1/reviews",
            json={"pull_request_id": pr["id"], "force": True},
        ).json()
        review_id = enqueued.get("review_run_id")
        if not check("review enqueued", bool(review_id), json.dumps(enqueued)[:300]):
            return 1

        terminal = {"completed", "failed", "cancelled"}
        review: dict = {}
        deadline = time.time() + review_timeout
        while time.time() < deadline:
            review = client.get(f"/api/v1/reviews/{review_id}").json()
            if review["status"] in terminal:
                break
            time.sleep(2)

        check(
            "review reached a terminal state",
            review.get("status") in terminal,
            f"status={review.get('status')} stage={review.get('stage')} "
            f"progress={review.get('progress')}",
        )
        check(
            "review completed successfully",
            review.get("status") == "completed",
            review.get("error_message") or "",
        )
        check("review analyzed files", review.get("files_analyzed", 0) > 0, str(review.get("files_analyzed")))
        check("review produced findings", review.get("findings_count", 0) > 0, str(review.get("findings_count")))
        check(
            "review scored risk",
            review.get("risk_band") is not None,
            f"{review.get('risk_score')} / {review.get('risk_band')}",
        )
        check("review has risk factors", bool(review.get("risk_factors")), str(len(review.get("risk_factors") or [])))
        check("review wrote a summary", bool(review.get("summary")))
        check("review published to github", bool(review.get("published")), str(review.get("github_review_url")))
        check("review recorded llm usage", len(review.get("llm_usage") or []) > 0)
        check("review recorded comments", len(review.get("comments") or []) > 0)

        # ------------------------------------------------------------ diff
        diff = client.get(f"/api/v1/reviews/{review_id}/diff").json()
        files = diff.get("files") or []
        check("diff endpoint returns files", len(files) > 0, str(len(files)))
        patched = [f for f in files if f.get("patch")]
        check("diff files carry patches", len(patched) > 0, f"{len(patched)} with patch")

        # -------------------------------------------------------- findings
        findings = client.get("/api/v1/findings", params={"page_size": 100}).json()
        check("findings list populated", findings["total"] > 0, f"{findings['total']} findings")

        # The anti-hallucination guarantee: a finding may only reference a file
        # that appears in the pull request's own diff.
        changed = {f["filename"] for f in files}
        review_findings = [f for f in findings["items"] if f["review_run_id"] == review_id]
        stray = sorted({f["file_path"] for f in review_findings} - changed)
        check("all findings anchor to changed files", not stray, ", ".join(stray[:5]))

        if review_findings:
            target = review_findings[0]
            updated = client.patch(
                f"/api/v1/findings/{target['id']}/status",
                json={"status": "resolved", "note": "verified by e2e"},
            ).json()
            check(
                "finding status update persists",
                updated.get("status") == "resolved",
                str(updated)[:200],
            )

        # ------------------------------------------------------- analytics
        analytics = client.get("/api/v1/analytics", params={"window_days": 30}).json()
        check("analytics overview populated", analytics["overview"]["reviews_total"] > 0)
        check("analytics severity breakdown populated", len(analytics["severity_breakdown"]) > 0)
        check("analytics trend populated", len(analytics["trend"]) > 0, f"{len(analytics['trend'])} points")
        check("analytics top files populated", len(analytics["top_files"]) > 0)

        queue = client.get("/api/v1/analytics/queue").json()
        check("queue stats reachable", queue.get("broker_reachable") is True, json.dumps(queue))

        # --------------------------------------------------- authorization
        # A resource outside the caller's installation must 404 rather than 403,
        # so identifiers cannot be probed for existence.
        foreign = client.get("/api/v1/repositories/00000000-0000-0000-0000-000000000000")
        check("unknown repository returns 404", foreign.status_code == 404, str(foreign.status_code))

    print()
    if check.failures:
        print(f"{len(check.failures)} of {check.total} CHECK(S) FAILED: {check.failures}")
        return 1
    print(f"ALL {check.total} E2E CHECKS PASSED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--review-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for a review to reach a terminal state (default: 180)",
    )
    args = parser.parse_args()

    try:
        return run(args.base_url.rstrip("/"), args.review_timeout)
    except httpx.HTTPError as exc:
        print(f"\nTransport error talking to {args.base_url}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
