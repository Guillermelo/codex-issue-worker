from pathlib import Path

import pytest

from src import git_service
from src.command import CommandResult
from src.git_service import GitService


def test_workspace_name_and_cleanup(tmp_path: Path) -> None:
    service = GitService(tmp_path, timeout_seconds=30)
    workspace = service.workspace_path("owner/repo", 42)
    workspace.mkdir()
    (workspace / "temporary.txt").touch()

    assert workspace.name.startswith("owner-repo-42-")
    service.cleanup(workspace)
    assert not workspace.exists()


def test_cleanup_rejects_outside_path(tmp_path: Path) -> None:
    service = GitService(tmp_path / "workspaces", timeout_seconds=30)

    with pytest.raises(ValueError, match="outside"):
        service.cleanup(tmp_path / "unrelated")


@pytest.mark.asyncio
async def test_clone_configures_git_auth_and_uses_base_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    async def fake_run_command(args: list[str], **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(tuple(args), 0, "", "")

    monkeypatch.setattr(git_service, "run_command", fake_run_command)
    service = GitService(tmp_path, timeout_seconds=30)

    await service.clone(
        "owner/repo",
        tmp_path / "clone",
        "Stateless+Gracefull",
    )

    assert calls[0] == ["gh", "auth", "setup-git"]
    assert calls[1][-3:] == [
        "--branch",
        "Stateless+Gracefull",
        "--single-branch",
    ]
