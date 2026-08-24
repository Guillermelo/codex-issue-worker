import json
from pathlib import Path

from src.validation import select_validation_commands


def test_selects_node_scripts_that_exist(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "vitest run",
                    "lint": "eslint .",
                    "untrusted-issue-command": "anything",
                }
            }
        ),
        encoding="utf-8",
    )

    assert select_validation_commands(tmp_path) == ["npm run test", "npm run lint"]


def test_selects_python_go_and_terraform_validation(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "tests").mkdir()
    (tmp_path / "go.mod").touch()
    (tmp_path / "main.tf").touch()

    assert select_validation_commands(tmp_path) == [
        "pytest",
        "go test ./...",
        "terraform fmt -check -recursive",
        "terraform validate",
    ]


def test_configured_validation_replaces_detection(tmp_path: Path) -> None:
    (tmp_path / "go.mod").touch()

    assert select_validation_commands(tmp_path, ["make verify"]) == ["make verify"]
