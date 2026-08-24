from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import router
from src.models import AgentConfig, Job, JobStatus


class FakeScanner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def solve(self, repo: str, issue: int) -> tuple[Job, bool]:
        self.calls.append((repo, issue))
        now = datetime.now(UTC)
        return (
            Job(
                id=1,
                repo=repo,
                issue_number=issue,
                status=JobStatus.PENDING,
                attempts=0,
                created_at=now,
                updated_at=now,
            ),
            True,
        )


def make_client() -> tuple[TestClient, FakeScanner]:
    app = FastAPI()
    app.include_router(router)
    scanner = FakeScanner()
    app.state.config = AgentConfig(repos=["owner/allowed"])
    app.state.database = SimpleNamespace()
    app.state.scanner = scanner
    app.state.worker = SimpleNamespace(queued_count=0)
    return TestClient(app), scanner


def test_solve_rejects_repository_outside_allowlist() -> None:
    client, scanner = make_client()

    response = client.post("/solve", json={"repo": "other/repo", "issue": 42})

    assert response.status_code == 403
    assert scanner.calls == []


def test_solve_enqueues_allowed_repository() -> None:
    client, scanner = make_client()

    response = client.post("/solve", json={"repo": "owner/allowed", "issue": 42})

    assert response.status_code == 202
    assert response.json()["enqueued"] is True
    assert scanner.calls == [("owner/allowed", 42)]


def test_solve_validates_positive_issue_number() -> None:
    client, _ = make_client()

    response = client.post("/solve", json={"repo": "owner/allowed", "issue": 0})

    assert response.status_code == 422
