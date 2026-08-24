from pathlib import Path

import pytest

from src.database import Database
from src.models import AgentConfig, IssueSummary
from src.scheduler import Scanner
from src.worker import Worker


class FakeGitHub:
    async def list_eligible_issues(self, repo: str, label: str) -> list[IssueSummary]:
        return [IssueSummary(number=42, title="Fix it")]

    async def find_pull_request(self, repo: str, branch: str) -> None:
        return None


@pytest.mark.asyncio
async def test_two_scans_enqueue_issue_only_once(tmp_path: Path) -> None:
    config = AgentConfig(
        repos=["owner/repo"],
        workspace_root=tmp_path / "workspaces",
        database_path=tmp_path / "agent.db",
    )
    database = Database(config.database_path)
    await database.initialize()
    github = FakeGitHub()
    worker = Worker(config, database, github, object(), object(), object())  # type: ignore[arg-type]
    scanner = Scanner(config, database, github, worker)  # type: ignore[arg-type]

    first = await scanner.scan()
    second = await scanner.scan()

    assert first.enqueued == 1
    assert second.enqueued == 0
    assert second.skipped == 1
    assert worker.queued_count == 1
    await database.close()


@pytest.mark.asyncio
async def test_worker_enforces_repository_allowlist(tmp_path: Path) -> None:
    config = AgentConfig(repos=["owner/repo"], database_path=tmp_path / "agent.db")
    database = Database(config.database_path)
    await database.initialize()
    worker = Worker(
        config,
        database,
        FakeGitHub(),
        object(),
        object(),
        object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="not configured"):
        await worker.enqueue("other/repo", 42)
    await database.close()
