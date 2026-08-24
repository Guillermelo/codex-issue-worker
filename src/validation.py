from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandTimeoutError, run_command


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    commands: tuple[str, ...]
    error: str | None = None


def select_validation_commands(
    workspace: Path, configured: list[str] | None = None
) -> list[str]:
    if configured is not None:
        return list(configured)

    commands: list[str] = []
    package_json = workspace / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get(
                "scripts", {}
            )
        except (json.JSONDecodeError, OSError):
            scripts = {}
        for script in ("test", "lint", "build"):
            if script in scripts:
                commands.append(f"npm run {script}")

    has_python_tests = (workspace / "tests").is_dir() or any(
        (workspace / name).is_file()
        for name in ("pytest.ini", "tox.ini", "conftest.py")
    )
    if (workspace / "pyproject.toml").is_file() and has_python_tests:
        commands.append("pytest")
    if (workspace / "go.mod").is_file():
        commands.append("go test ./...")
    if any(workspace.glob("*.tf")):
        commands.extend(["terraform fmt -check -recursive", "terraform validate"])
    if any(workspace.glob("*.pkr.hcl")):
        commands.extend(["packer fmt -check .", "packer validate ."])
    return commands


class Validator:
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds

    async def validate(
        self, workspace: Path, configured: list[str] | None
    ) -> ValidationResult:
        commands = select_validation_commands(workspace, configured)
        if not commands:
            return ValidationResult(
                passed=False,
                commands=(),
                error=(
                    "No deterministic validation commands were configured or safely "
                    "detected"
                ),
            )
        for command in commands:
            args = shlex.split(command)
            if not args:
                return ValidationResult(
                    False, tuple(commands), "Empty validation command"
                )
            try:
                await run_command(args, cwd=workspace, timeout=self.timeout_seconds)
            except (CommandError, CommandTimeoutError, OSError) as exc:
                return ValidationResult(False, tuple(commands), str(exc))
        return ValidationResult(True, tuple(commands))
