"""In-memory GitHub provider used by demo mode and the test suite.

It implements the exact same interface as :class:`GitHubAppProvider` and serves
fixture repositories with realistic bugs, so the entire review pipeline can be
exercised without network access or credentials. Published reviews are captured
in memory and exposed through :attr:`published_reviews` for assertions and for
the dashboard's demo view.
"""

from __future__ import annotations

import itertools
from typing import Any

from app.core.errors import GitHubError
from app.demo import fixtures
from app.integrations.github.base import (
    ChangedFile,
    GitHubProvider,
    InlineComment,
    InstallationInfo,
    PublishedReview,
    PullRequestInfo,
    RepositoryInfo,
)


class MockGitHubProvider(GitHubProvider):
    name = "mock-github"
    is_mock = True

    #: Shared across instances so a worker and the API observe the same state.
    published_reviews: list[dict[str, Any]] = []

    _id_counter = itertools.count(9000001)

    def __init__(self) -> None:
        self._repositories = {repo.full_name: repo for repo in fixtures.DEMO_REPOSITORIES}

    # ------------------------------------------------------------- helpers
    def _repo(self, full_name: str) -> fixtures.DemoRepository:
        repo = self._repositories.get(full_name)
        if repo is None:
            raise GitHubError(
                f"Repository '{full_name}' is not available in demo mode",
                code="REPOSITORY_NOT_FOUND",
                status_code=404,
            )
        return repo

    def _pr(self, full_name: str, number: int) -> fixtures.DemoPullRequest:
        pr = fixtures.find_pull_request(full_name, number)
        if pr is None:
            raise GitHubError(
                f"Pull request {full_name}#{number} is not available in demo mode",
                code="PULL_REQUEST_NOT_FOUND",
                status_code=404,
            )
        return pr

    @staticmethod
    def _to_repository_info(repo: fixtures.DemoRepository) -> RepositoryInfo:
        return RepositoryInfo(
            github_repo_id=repo.github_repo_id,
            owner=repo.owner,
            name=repo.name,
            full_name=repo.full_name,
            default_branch=repo.default_branch,
            private=True,
            description=repo.description,
            language=repo.language,
            html_url=f"https://github.com/{repo.full_name}",
        )

    # ---------------------------------------------------------- read: meta
    async def get_installation(self, installation_id: int) -> InstallationInfo:
        return InstallationInfo(
            installation_id=installation_id,
            account_login=fixtures.DEMO_ACCOUNT,
            account_id=555001,
            account_type="Organization",
            avatar_url="https://avatars.githubusercontent.com/u/9919?v=4",
            permissions={
                "metadata": "read",
                "contents": "read",
                "pull_requests": "write",
            },
            repositories=[self._to_repository_info(r) for r in fixtures.DEMO_REPOSITORIES],
        )

    async def list_installation_repositories(
        self, installation_id: int
    ) -> list[RepositoryInfo]:
        return [self._to_repository_info(r) for r in fixtures.DEMO_REPOSITORIES]

    async def get_repository(self, installation_id: int, full_name: str) -> RepositoryInfo:
        return self._to_repository_info(self._repo(full_name))

    # ------------------------------------------------------------ read: pr
    async def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> PullRequestInfo:
        repo = self._repo(full_name)
        pr = self._pr(full_name, number)
        files = fixtures.changed_files_for(repo, pr)
        return PullRequestInfo(
            number=pr.number,
            github_pr_id=900000 + pr.number,
            title=pr.title,
            body=pr.body,
            author=pr.author,
            author_avatar_url=f"https://avatars.githubusercontent.com/u/{pr.number}?v=4",
            base_branch=pr.base_branch,
            head_branch=pr.head_branch,
            base_sha=pr.base_sha,
            head_sha=pr.head_sha,
            state=pr.state,
            draft=False,
            html_url=f"https://github.com/{full_name}/pull/{pr.number}",
            additions=sum(f["additions"] for f in files),
            deletions=sum(f["deletions"] for f in files),
            changed_files=len(files),
        )

    async def get_pull_request_diff(
        self, installation_id: int, full_name: str, number: int
    ) -> str:
        repo = self._repo(full_name)
        return fixtures.unified_diff_for(repo, self._pr(full_name, number))

    async def get_changed_files(
        self, installation_id: int, full_name: str, number: int
    ) -> list[ChangedFile]:
        repo = self._repo(full_name)
        return [
            ChangedFile(
                filename=entry["filename"],
                status=entry["status"],
                additions=entry["additions"],
                deletions=entry["deletions"],
                changes=entry["changes"],
                patch=entry["patch"],
            )
            for entry in fixtures.changed_files_for(repo, self._pr(full_name, number))
        ]

    # -------------------------------------------------------- read: content
    async def get_file(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None:
        repo = self._repo(full_name)
        return repo.tree_for(ref).get(path)

    async def list_files(
        self, installation_id: int, full_name: str, ref: str
    ) -> list[dict[str, Any]]:
        repo = self._repo(full_name)
        tree = repo.tree_for(ref)
        return [
            {"path": path, "size": len(content.encode("utf-8")), "sha": None}
            for path, content in sorted(tree.items())
        ]

    # ------------------------------------------------------------ write api
    async def create_review(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        body: str,
        event: str = "COMMENT",
        comments: list[InlineComment] | None = None,
    ) -> PublishedReview:
        repo = self._repo(full_name)
        pr = self._pr(full_name, number)
        inline = comments or []

        # Reproduce GitHub's constraint: inline comments must anchor to a line
        # that appears in the diff, otherwise the whole review is rejected.
        commentable = _commentable_lines(repo, pr)
        accepted: list[InlineComment] = []
        rejected: list[dict[str, Any]] = []
        for comment in inline:
            allowed = commentable.get(comment.path, set())
            if comment.line is not None and comment.line not in allowed:
                rejected.append({**comment.to_api(), "reason": "line not present in diff"})
                continue
            accepted.append(comment)

        review_id = next(self._id_counter)
        record = {
            "review_id": review_id,
            "repository": full_name,
            "pr_number": number,
            "commit_sha": commit_sha,
            "event": event,
            "body": body,
            "comments": [c.to_api() for c in accepted],
            "rejected": rejected,
        }
        MockGitHubProvider.published_reviews.append(record)
        return PublishedReview(
            review_id=review_id,
            html_url=(
                f"https://github.com/{full_name}/pull/{number}#pullrequestreview-{review_id}"
            ),
            submitted_comments=len(accepted),
            rejected_comments=rejected,
        )

    async def create_inline_comment(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        comment: InlineComment,
    ) -> dict[str, Any]:
        comment_id = next(self._id_counter)
        record = {
            "comment_id": comment_id,
            "repository": full_name,
            "pr_number": number,
            "commit_sha": commit_sha,
            **comment.to_api(),
        }
        MockGitHubProvider.published_reviews.append(record)
        return record

    @classmethod
    def reset(cls) -> None:
        cls.published_reviews = []


def _commentable_lines(
    repo: fixtures.DemoRepository, pr: fixtures.DemoPullRequest
) -> dict[str, set[int]]:
    from app.analyzers.diff import parse_patch

    result: dict[str, set[int]] = {}
    for entry in fixtures.changed_files_for(repo, pr):
        file_diff = parse_patch(entry["filename"], entry["patch"])
        result[entry["filename"]] = {
            line.new_lineno
            for hunk in file_diff.hunks
            for line in hunk.lines
            if line.new_lineno is not None and line.kind in ("add", "ctx")
        }
    return result
