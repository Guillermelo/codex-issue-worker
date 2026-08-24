from __future__ import annotations

import json
import re

from .command import CommandError, run_command
from .models import IssueContext, IssueSummary


AUTH_MARKERS = (
    "gh auth login",
    "not logged into any github hosts",
    "authentication failed",
    "bad credentials",
    "http 401",
)
URL_PATTERN = re.compile(r"https://github\.com/[^\s]+/pull/\d+")


class GitHubAuthError(RuntimeError):
    pass


def is_auth_error(error: CommandError) -> bool:
    message = f"{error.result.stderr}\n{error.result.stdout}".lower()
    return any(marker in message for marker in AUTH_MARKERS)


class GitHubClient:
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds

    async def _gh(self, args: list[str]) -> str:
        try:
            result = await run_command(["gh", *args], timeout=self.timeout_seconds)
        except CommandError as exc:
            if is_auth_error(exc):
                raise GitHubAuthError(
                    "GitHub CLI authentication is required; run gh auth login"
                ) from exc
            raise
        return result.stdout.strip()

    async def list_eligible_issues(self, repo: str, label: str) -> list[IssueSummary]:
        output = await self._gh(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--label",
                label,
                "--limit",
                "100",
                "--json",
                "number,title",
            ]
        )
        return [
            IssueSummary.model_validate(item) for item in json.loads(output or "[]")
        ]

    async def get_issue(self, repo: str, issue_number: int) -> IssueContext:
        output = await self._gh(
            [
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "number,title,body,comments",
            ]
        )
        payload = json.loads(output)
        comments = [comment.get("body", "") for comment in payload.get("comments", [])]
        return IssueContext(
            number=payload["number"],
            title=payload["title"],
            body=payload.get("body") or "",
            comments=comments,
        )

    async def find_pull_request(self, repo: str, branch: str) -> str | None:
        output = await self._gh(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch,
                "--state",
                "all",
                "--limit",
                "1",
                "--json",
                "url",
            ]
        )
        pulls = json.loads(output or "[]")
        return pulls[0]["url"] if pulls else None

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        branch: str,
        base_branch: str | None = None,
    ) -> str:
        arguments = [
            "pr",
            "create",
            "--repo",
            repo,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ]
        if base_branch:
            arguments.extend(["--base", base_branch])
        output = await self._gh(arguments)
        urls = URL_PATTERN.findall(output)
        if not urls:
            raise RuntimeError("GitHub CLI did not return a Pull Request URL")
        return urls[-1]

    async def comment_on_issue(self, repo: str, issue_number: int, body: str) -> None:
        await self._gh(
            ["issue", "comment", str(issue_number), "--repo", repo, "--body", body]
        )
