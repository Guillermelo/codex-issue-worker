from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import Job, JobStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Small SQLite state store serialized by an asyncio lock."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                branch TEXT,
                pr_url TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(repo, issue_number)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
        )
        self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("database is not initialized")
        return self._connection

    @staticmethod
    def _job(row: sqlite3.Row | None) -> Job | None:
        return Job.model_validate(dict(row)) if row is not None else None

    async def create_job(self, repo: str, issue_number: int) -> Job:
        now = _now()
        async with self._lock:
            connection = self._conn()
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (repo, issue_number, status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (repo, issue_number, JobStatus.PENDING.value, now, now),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM jobs WHERE repo = ? AND issue_number = ?",
                (repo, issue_number),
            ).fetchone()
        job = self._job(row)
        assert job is not None
        return job

    async def get_job(self, repo: str, issue_number: int) -> Job | None:
        async with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT * FROM jobs WHERE repo = ? AND issue_number = ?",
                    (repo, issue_number),
                )
                .fetchone()
            )
        return self._job(row)

    async def list_jobs(self, limit: int = 100) -> list[Job]:
        async with self._lock:
            rows = (
                self._conn()
                .execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
                .fetchall()
            )
        return [Job.model_validate(dict(row)) for row in rows]

    async def claim_job(self, job_id: int, max_attempts: int) -> Job | None:
        now = _now()
        async with self._lock:
            connection = self._conn()
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, attempts = attempts + 1, started_at = ?,
                    finished_at = NULL, error = NULL, updated_at = ?
                WHERE id = ? AND status IN (?, ?) AND attempts < ?
                """,
                (
                    JobStatus.RUNNING.value,
                    now,
                    now,
                    job_id,
                    JobStatus.PENDING.value,
                    JobStatus.FAILED.value,
                    max_attempts,
                ),
            )
            connection.commit()
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job(row)

    async def finish_job(
        self,
        job_id: int,
        status: JobStatus,
        *,
        branch: str | None = None,
        pr_url: str | None = None,
        error: str | None = None,
    ) -> None:
        if status in {JobStatus.PENDING, JobStatus.RUNNING}:
            raise ValueError("finish status must be terminal")
        now = _now()
        safe_error = error[:4000] if error else None
        async with self._lock:
            self._conn().execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?, branch = COALESCE(?, branch),
                    pr_url = COALESCE(?, pr_url), error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, now, branch, pr_url, safe_error, now, job_id),
            )
            self._conn().commit()

    async def recover_interrupted_jobs(self) -> int:
        now = _now()
        async with self._lock:
            cursor = self._conn().execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?,
                    error = 'Agent restarted while the job was running', updated_at = ?
                WHERE status = ?
                """,
                (
                    JobStatus.FAILED.value,
                    now,
                    now,
                    JobStatus.RUNNING.value,
                ),
            )
            self._conn().commit()
            return cursor.rowcount

    async def requeue_auth_job(self, job_id: int) -> Job | None:
        """Resume an auth-blocked job only after an explicit manual request."""
        now = _now()
        async with self._lock:
            connection = self._conn()
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, attempts = MAX(attempts - 1, 0),
                    finished_at = NULL, error = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.PENDING.value,
                    now,
                    job_id,
                    JobStatus.AUTH_REQUIRED.value,
                ),
            )
            connection.commit()
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job(row)

    async def record_existing_pr(
        self, repo: str, issue_number: int, branch: str, pr_url: str
    ) -> Job:
        job = await self.create_job(repo, issue_number)
        await self.finish_job(
            job.id,
            JobStatus.COMPLETED,
            branch=branch,
            pr_url=pr_url,
        )
        result = await self.get_job(repo, issue_number)
        assert result is not None
        return result
