from __future__ import annotations

import asyncio
import logging
import re

from .codex_runner import CodexAuthError, CodexRunner
from .database import Database
from .git_service import GitService
from .github_client import GitHubAuthError, GitHubClient
from .models import AgentConfig, Job, JobRequest, JobStatus
from .validation import Validator


logger = logging.getLogger(__name__)


TOKEN_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(authorization:\s*(?:bearer|token)\s+)\S+"),
)


def sanitize_error(error: BaseException) -> str:
    return sanitize_text(str(error), 4000)


def sanitize_text(message: str, limit: int) -> str:
    for pattern in TOKEN_PATTERNS:
        message = pattern.sub("[REDACTED]", message)
    return message[:limit]


class JobFailure(RuntimeError):
    pass


class Worker:
    def __init__(
        self,
        config: AgentConfig,
        database: Database,
        github: GitHubClient,
        git: GitService,
        codex: CodexRunner,
        validator: Validator,
    ):
        self.config = config
        self.database = database
        self.github = github
        self.git = git
        self.codex = codex
        self.validator = validator
        self.queue: asyncio.Queue[JobRequest] = asyncio.Queue()
        self._queued: set[tuple[str, int]] = set()
        self._queue_lock = asyncio.Lock()

    async def enqueue(self, repo: str, issue_number: int) -> tuple[Job, bool]:
        if repo not in self.config.allowed_repositories:
            raise ValueError("repository is not configured")
        job = await self.database.create_job(repo, issue_number)
        eligible = (
            job.status in {JobStatus.PENDING, JobStatus.FAILED}
            and job.attempts < self.config.max_attempts
        )
        key = (repo, issue_number)
        async with self._queue_lock:
            if not eligible or key in self._queued:
                return job, False
            self._queued.add(key)
            await self.queue.put(JobRequest(repo=repo, issue_number=issue_number))
        return job, True

    async def run(self) -> None:
        while True:
            request = await self.queue.get()
            key = (request.repo, request.issue_number)
            try:
                await self._process(request)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Unexpected worker failure for %s#%s",
                    request.repo,
                    request.issue_number,
                )
            finally:
                async with self._queue_lock:
                    self._queued.discard(key)
                self.queue.task_done()

    async def _process(self, request: JobRequest) -> None:
        existing = await self.database.get_job(request.repo, request.issue_number)
        if existing is None:
            return
        job = await self.database.claim_job(existing.id, self.config.max_attempts)
        if job is None:
            return

        workspace = self.git.workspace_path(request.repo, request.issue_number)
        branch = f"agent/issue-{request.issue_number}"
        failed = True
        try:
            logger.info("Processing %s#%s", request.repo, request.issue_number)
            repository = self.config.repository(request.repo)
            await self.git.clone(request.repo, workspace, repository.base_branch)
            branch = await self.git.create_branch(workspace, request.issue_number)
            issue = await self.github.get_issue(request.repo, request.issue_number)
            codex_report = await self.codex.run(workspace, request.repo, issue)

            has_working_changes = await self.git.has_changes(workspace)
            has_committed_changes = (
                not has_working_changes
                and await self.git.has_committed_changes(
                    workspace, self._base_ref(repository.base_branch)
                )
            )
            if not has_working_changes and not has_committed_changes:
                await self.database.finish_job(
                    job.id,
                    JobStatus.IGNORED,
                    branch=branch,
                    error="Codex completed without producing repository changes",
                )
                failed = False
                return

            validation = await self.validator.validate(
                workspace, repository.validation_commands
            )
            if not validation.passed:
                raise JobFailure(validation.error or "Validation failed")

            changed_files = (
                await self.git.commit_changes(workspace, branch, request.issue_number)
                if has_working_changes
                else await self.git.committed_change_summary(
                    workspace, self._base_ref(repository.base_branch)
                )
            )
            validation_summary = "\n".join(
                f"- `{command}`" for command in validation.commands
            )
            body = (
                f"Closes #{request.issue_number}\n\n"
                "## Summary\n\n"
                f"{sanitize_text(codex_report, 8000) or changed_files}\n\n"
                "## Changed files\n\n"
                f"```text\n{changed_files}\n```\n\n"
                "## Validation\n\n"
                f"{validation_summary}"
            )
            title = f"Fix #{request.issue_number}: {issue.title}"[:256]
            pr_url = await self.github.create_pull_request(
                request.repo,
                title,
                body,
                branch,
                repository.base_branch,
            )
            await self.database.finish_job(
                job.id,
                JobStatus.COMPLETED,
                branch=branch,
                pr_url=pr_url,
            )
            failed = False
            logger.info(
                "Completed %s#%s: %s", request.repo, request.issue_number, pr_url
            )
        except (CodexAuthError, GitHubAuthError) as exc:
            await self.database.finish_job(
                job.id,
                JobStatus.AUTH_REQUIRED,
                branch=branch,
                error=sanitize_error(exc),
            )
            logger.warning(
                "Authentication required while processing %s#%s",
                request.repo,
                request.issue_number,
            )
        except Exception as exc:
            error = sanitize_error(exc)
            await self.database.finish_job(
                job.id, JobStatus.FAILED, branch=branch, error=error
            )
            logger.error(
                "Job failed for %s#%s (%s)",
                request.repo,
                request.issue_number,
                type(exc).__name__,
            )
            if self.config.comment_on_failure:
                await self._comment_on_failure(request, job.attempts)
        finally:
            if not (failed and self.config.keep_workspace_on_failure):
                self.git.cleanup(workspace)

    async def _comment_on_failure(self, request: JobRequest, attempt: int) -> None:
        try:
            await self.github.comment_on_issue(
                request.repo,
                request.issue_number,
                f"The issue agent could not complete this job (attempt {attempt}). "
                "Check the self-hosted agent logs for details.",
            )
        except Exception:
            logger.exception(
                "Could not post failure comment for %s#%s",
                request.repo,
                request.issue_number,
            )

    @property
    def queued_count(self) -> int:
        return self.queue.qsize()

    @staticmethod
    def _base_ref(base_branch: str | None) -> str:
        return f"origin/{base_branch}" if base_branch else "origin/HEAD"
