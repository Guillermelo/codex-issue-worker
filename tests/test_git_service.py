from pathlib import Path

import pytest

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
