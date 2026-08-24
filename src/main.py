from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .codex_runner import CodexRunner
from .database import Database
from .git_service import GitService
from .github_client import GitHubClient
from .models import AgentConfig, load_config
from .scheduler import Scanner, Scheduler
from .validation import Validator
from .worker import Worker


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_app(config: AgentConfig | None = None) -> FastAPI:
    resolved_config = config or load_config(os.getenv("CONFIG_FILE", "config.yaml"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved_config.database_path)
        await database.initialize()
        await database.recover_interrupted_jobs()
        github = GitHubClient(resolved_config.command_timeout_seconds)
        git = GitService(
            resolved_config.workspace_root,
            resolved_config.command_timeout_seconds,
        )
        codex = CodexRunner(resolved_config.codex_timeout_seconds)
        validator = Validator(resolved_config.command_timeout_seconds)
        worker = Worker(resolved_config, database, github, git, codex, validator)
        scanner = Scanner(resolved_config, database, github, worker)
        scheduler = Scheduler(scanner, resolved_config.poll_interval_seconds)

        app.state.config = resolved_config
        app.state.database = database
        app.state.worker = worker
        app.state.scanner = scanner
        app.state.scheduler = scheduler

        worker_task = asyncio.create_task(worker.run(), name="issue-worker")
        scheduler_task = asyncio.create_task(scheduler.run(), name="issue-scanner")
        try:
            yield
        finally:
            scheduler.stop()
            scheduler_task.cancel()
            worker_task.cancel()
            await asyncio.gather(scheduler_task, worker_task, return_exceptions=True)
            await database.close()

    app = FastAPI(title="GitHub Issue Agent", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
