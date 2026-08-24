from pathlib import Path

import pytest

from src.database import Database
from src.models import JobStatus


@pytest.mark.asyncio
async def test_state_transitions_and_retry_limit(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    await database.initialize()
    job = await database.create_job("owner/repo", 42)

    first = await database.claim_job(job.id, max_attempts=2)
    assert first is not None
    assert first.status == JobStatus.RUNNING
    assert first.attempts == 1

    await database.finish_job(first.id, JobStatus.FAILED, error="tests failed")
    second = await database.claim_job(job.id, max_attempts=2)
    assert second is not None
    assert second.attempts == 2
    await database.finish_job(second.id, JobStatus.FAILED, error="tests failed again")

    assert await database.claim_job(job.id, max_attempts=2) is None
    final = await database.get_job("owner/repo", 42)
    assert final is not None
    assert final.status == JobStatus.FAILED
    assert final.attempts == 2
    await database.close()


@pytest.mark.asyncio
async def test_job_creation_is_deduplicated(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    await database.initialize()

    first = await database.create_job("owner/repo", 7)
    second = await database.create_job("owner/repo", 7)

    assert first.id == second.id
    assert len(await database.list_jobs()) == 1
    await database.close()


@pytest.mark.asyncio
async def test_recovers_interrupted_jobs(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    await database.initialize()
    job = await database.create_job("owner/repo", 9)
    assert await database.claim_job(job.id, max_attempts=2)

    assert await database.recover_interrupted_jobs() == 1
    recovered = await database.get_job("owner/repo", 9)
    assert recovered is not None
    assert recovered.status == JobStatus.FAILED
    await database.close()


@pytest.mark.asyncio
async def test_auth_job_requires_explicit_requeue(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    await database.initialize()
    job = await database.create_job("owner/repo", 10)
    claimed = await database.claim_job(job.id, max_attempts=2)
    assert claimed is not None
    await database.finish_job(claimed.id, JobStatus.AUTH_REQUIRED)

    assert await database.claim_job(job.id, max_attempts=2) is None
    requeued = await database.requeue_auth_job(job.id)
    assert requeued is not None
    assert requeued.status == JobStatus.PENDING
    assert requeued.attempts == 0
    await database.close()
