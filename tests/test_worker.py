from pathlib import Path

import pytest

from src.codex_runner import CodexAuthError
from src.database import Database
from src.models import AgentConfig, IssueContext, JobRequest, JobStatus
from src.validation import ValidationResult
from src.worker import Worker


class FakeGit:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.cleaned = False

    def workspace_path(self, repo: str, issue_number: int) -> Path:
        return self.root / f"owner-repo-{issue_number}-fake"

    async def clone(
        self, repo: str, destination: Path, base_branch: str | None = None
    ) -> None:
        assert base_branch == "Stateless+Gracefull"
        destination.mkdir(parents=True)

    async def create_branch(self, workspace: Path, issue_number: int) -> str:
        return f"agent/issue-{issue_number}"

    async def has_changes(self, workspace: Path) -> bool:
        return True

    async def has_committed_changes(self, workspace: Path, base_ref: str) -> bool:
        return False

    async def committed_change_summary(self, workspace: Path, base_ref: str) -> str:
        return "2 files changed"

    async def commit_changes(
        self, workspace: Path, branch: str, issue_number: int
    ) -> str:
        return "2 files changed"

    def cleanup(self, workspace: Path) -> None:
        self.cleaned = True


class ExistingBranchGit(FakeGit):
    async def has_changes(self, workspace: Path) -> bool:
        return False

    async def has_committed_changes(self, workspace: Path, base_ref: str) -> bool:
        assert base_ref == "origin/Stateless+Gracefull"
        return True

    async def commit_changes(
        self, workspace: Path, branch: str, issue_number: int
    ) -> str:
        raise AssertionError("an existing remote branch must not be recommitted")


class FakeGitHub:
    async def get_issue(self, repo: str, issue_number: int) -> IssueContext:
        return IssueContext(number=issue_number, title="Fix the bug")

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        branch: str,
        base_branch: str | None = None,
    ) -> str:
        assert f"Closes #{branch.rsplit('-', 1)[-1]}" in body
        assert "- Added safe mock behavior." in body
        assert "### Configuration and rollout" in body
        assert "Set `LOAD_TEST_MODE=true`." in body
        assert "## Changed files" in body
        assert "```text\n2 files changed\n```" in body
        assert "## Validation" in body
        assert "- `pytest`" in body
        assert base_branch == "Stateless+Gracefull"
        return "https://github.com/owner/repo/pull/1"


class FakeCodex:
    async def run(self, workspace: Path, repo: str, issue: IssueContext) -> str:
        return (
            "- Added safe mock behavior.\n\n"
            "### Configuration and rollout\nSet `LOAD_TEST_MODE=true`."
        )


class AuthRequiredCodex:
    async def run(self, workspace: Path, repo: str, issue: IssueContext) -> None:
        raise CodexAuthError("codex login is required")


class PassingValidator:
    async def validate(
        self, workspace: Path, configured: list[str] | None
    ) -> ValidationResult:
        return ValidationResult(True, ("pytest",))


def make_config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        repos=[
            {
                "repo": "owner/repo",
                "base_branch": "Stateless+Gracefull",
                "validation_commands": ["pytest"],
            }
        ],
        workspace_root=tmp_path / "workspaces",
        database_path=tmp_path / "agent.db",
    )


@pytest.mark.asyncio
async def test_worker_completes_validated_job_and_records_pr(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = Database(config.database_path)
    await database.initialize()
    git = FakeGit(config.workspace_root)
    worker = Worker(
        config,
        database,
        FakeGitHub(),  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
        FakeCodex(),  # type: ignore[arg-type]
        PassingValidator(),  # type: ignore[arg-type]
    )
    await database.create_job("owner/repo", 42)

    await worker._process(JobRequest(repo="owner/repo", issue_number=42))

    job = await database.get_job("owner/repo", 42)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.branch == "agent/issue-42"
    assert job.pr_url == "https://github.com/owner/repo/pull/1"
    assert git.cleaned is True
    await database.close()


@pytest.mark.asyncio
async def test_worker_marks_codex_auth_failure_without_retrying(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = Database(config.database_path)
    await database.initialize()
    git = FakeGit(config.workspace_root)
    worker = Worker(
        config,
        database,
        FakeGitHub(),  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
        AuthRequiredCodex(),  # type: ignore[arg-type]
        PassingValidator(),  # type: ignore[arg-type]
    )
    await database.create_job("owner/repo", 7)

    await worker._process(JobRequest(repo="owner/repo", issue_number=7))

    job = await database.get_job("owner/repo", 7)
    assert job is not None
    assert job.status == JobStatus.AUTH_REQUIRED
    assert job.attempts == 1
    assert git.cleaned is True
    await database.close()


@pytest.mark.asyncio
async def test_worker_recovers_pushed_branch_when_pr_is_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    database = Database(config.database_path)
    await database.initialize()
    git = ExistingBranchGit(config.workspace_root)
    worker = Worker(
        config,
        database,
        FakeGitHub(),  # type: ignore[arg-type]
        git,  # type: ignore[arg-type]
        FakeCodex(),  # type: ignore[arg-type]
        PassingValidator(),  # type: ignore[arg-type]
    )
    await database.create_job("owner/repo", 99)

    await worker._process(JobRequest(repo="owner/repo", issue_number=99))

    job = await database.get_job("owner/repo", 99)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.pr_url == "https://github.com/owner/repo/pull/1"
    assert git.cleaned is True
    await database.close()
