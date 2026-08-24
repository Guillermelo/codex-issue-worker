from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from .command import CommandError, run_command
from .github_client import GitHubAuthError, is_auth_error


class GitService:
    def __init__(self, workspace_root: Path, timeout_seconds: int):
        self.workspace_root = workspace_root
        self.timeout_seconds = timeout_seconds

    def workspace_path(self, repo: str, issue_number: int) -> Path:
        safe_repo = re.sub(r"[^A-Za-z0-9_.-]", "-", repo)
        suffix = uuid.uuid4().hex[:8]
        return self.workspace_root / f"{safe_repo}-{issue_number}-{suffix}"

    async def clone(self, repo: str, destination: Path) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        try:
            await run_command(
                [
                    "gh",
                    "repo",
                    "clone",
                    repo,
                    str(destination),
                    "--",
                    "--depth=1",
                ],
                timeout=self.timeout_seconds,
            )
        except CommandError as exc:
            if is_auth_error(exc):
                raise GitHubAuthError(
                    "GitHub CLI authentication is required; run gh auth login"
                ) from exc
            raise

    async def create_branch(self, workspace: Path, issue_number: int) -> str:
        branch = f"agent/issue-{issue_number}"
        remote = await run_command(
            ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
            cwd=workspace,
            timeout=self.timeout_seconds,
            check=False,
        )
        if remote.returncode == 0:
            await run_command(
                ["git", "fetch", "origin", branch, "--depth=1"],
                cwd=workspace,
                timeout=self.timeout_seconds,
            )
            await run_command(
                ["git", "checkout", "-b", branch, "FETCH_HEAD"],
                cwd=workspace,
                timeout=self.timeout_seconds,
            )
            return branch
        if remote.returncode != 2:
            raise CommandError(remote)
        await run_command(
            ["git", "checkout", "-b", branch],
            cwd=workspace,
            timeout=self.timeout_seconds,
        )
        return branch

    async def has_changes(self, workspace: Path) -> bool:
        result = await run_command(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            timeout=self.timeout_seconds,
        )
        return bool(result.stdout.strip())

    async def has_committed_changes(self, workspace: Path) -> bool:
        result = await run_command(
            ["git", "diff", "--quiet", "origin/HEAD", "HEAD"],
            cwd=workspace,
            timeout=self.timeout_seconds,
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise CommandError(result)
        return result.returncode == 1

    async def committed_change_summary(self, workspace: Path) -> str:
        result = await run_command(
            ["git", "diff", "--stat", "origin/HEAD", "HEAD"],
            cwd=workspace,
            timeout=self.timeout_seconds,
        )
        return result.stdout.strip() or "Repository changes committed"

    async def commit_changes(
        self, workspace: Path, branch: str, issue_number: int
    ) -> str:
        expected = f"agent/issue-{issue_number}"
        if branch != expected or branch in {"main", "master"}:
            raise ValueError("refusing to commit or push an unexpected branch")
        await run_command(
            ["git", "add", "--all"], cwd=workspace, timeout=self.timeout_seconds
        )
        stat = await run_command(
            ["git", "diff", "--cached", "--stat"],
            cwd=workspace,
            timeout=self.timeout_seconds,
        )
        await run_command(
            ["git", "commit", "-m", f"fix: resolve issue #{issue_number}"],
            cwd=workspace,
            timeout=self.timeout_seconds,
        )
        await run_command(
            ["git", "push", "--set-upstream", "origin", branch],
            cwd=workspace,
            timeout=self.timeout_seconds,
        )
        return stat.stdout.strip() or "Repository changes committed"

    def cleanup(self, workspace: Path) -> None:
        root = self.workspace_root.resolve()
        resolved = workspace.resolve()
        if resolved.parent != root:
            raise ValueError("refusing to clean a path outside the workspace root")
        shutil.rmtree(resolved, ignore_errors=True)
