from __future__ import annotations

import asyncio
import logging

from .database import Database
from .github_client import GitHubClient
from .models import AgentConfig, Job, JobStatus, ScanResult
from .worker import Worker


logger = logging.getLogger(__name__)


class Scanner:
    def __init__(
        self,
        config: AgentConfig,
        database: Database,
        github: GitHubClient,
        worker: Worker,
    ):
        self.config = config
        self.database = database
        self.github = github
        self.worker = worker
        self._scan_lock = asyncio.Lock()

    async def scan(self) -> ScanResult:
        result = ScanResult()
        async with self._scan_lock:
            for repository in self.config.repos:
                issues = await self.github.list_eligible_issues(
                    repository.repo, self.config.issue_label
                )
                result.discovered += len(issues)
                for issue in issues:
                    job = await self.database.create_job(repository.repo, issue.number)
                    if not self._eligible(job):
                        result.skipped += 1
                        continue
                    branch = f"agent/issue-{issue.number}"
                    pr_url = await self.github.find_pull_request(
                        repository.repo, branch
                    )
                    if pr_url:
                        await self.database.record_existing_pr(
                            repository.repo, issue.number, branch, pr_url
                        )
                        result.skipped += 1
                        continue
                    _, enqueued = await self.worker.enqueue(
                        repository.repo, issue.number
                    )
                    if enqueued:
                        result.enqueued += 1
                    else:
                        result.skipped += 1
        return result

    async def solve(self, repo: str, issue_number: int) -> tuple[Job, bool]:
        existing = await self.database.get_job(repo, issue_number)
        branch = f"agent/issue-{issue_number}"
        pr_url = await self.github.find_pull_request(repo, branch)
        if pr_url:
            job = await self.database.record_existing_pr(
                repo, issue_number, branch, pr_url
            )
            return job, False
        if existing is not None and existing.status == JobStatus.AUTH_REQUIRED:
            await self.database.requeue_auth_job(existing.id)
        return await self.worker.enqueue(repo, issue_number)

    def _eligible(self, job: Job) -> bool:
        return (
            job.status in {JobStatus.PENDING, JobStatus.FAILED}
            and job.attempts < self.config.max_attempts
        )


class Scheduler:
    def __init__(self, scanner: Scanner, interval_seconds: int):
        self.scanner = scanner
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.scanner.scan()
                logger.info(
                    "Scan complete: discovered=%s enqueued=%s skipped=%s",
                    result.discovered,
                    result.enqueued,
                    result.skipped,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Repository scan failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
