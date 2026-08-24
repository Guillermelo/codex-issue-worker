from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from .database import Database
from .models import AgentConfig, Job, ScanResult, SolveRequest
from .scheduler import Scanner
from .worker import Worker


router = APIRouter()


def _services(request: Request) -> tuple[AgentConfig, Database, Scanner, Worker]:
    state = request.app.state
    return state.config, state.database, state.scanner, state.worker


@router.get("/health")
async def health(request: Request) -> dict[str, str | int]:
    _, _, _, worker = _services(request)
    return {"status": "ok", "queued_jobs": worker.queued_count}


@router.get("/jobs", response_model=list[Job])
async def jobs(request: Request) -> list[Job]:
    _, database, _, _ = _services(request)
    return await database.list_jobs()


@router.post("/scan", response_model=ScanResult)
async def scan(request: Request) -> ScanResult:
    _, _, scanner, _ = _services(request)
    return await scanner.scan()


@router.post("/solve", status_code=status.HTTP_202_ACCEPTED)
async def solve(payload: SolveRequest, request: Request) -> dict[str, object]:
    config, _, scanner, _ = _services(request)
    if payload.repo not in config.allowed_repositories:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="repository is not configured",
        )
    job, enqueued = await scanner.solve(payload.repo, payload.issue)
    return {"job": job.model_dump(mode="json"), "enqueued": enqueued}
