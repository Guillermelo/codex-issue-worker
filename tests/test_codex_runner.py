from pathlib import Path

import pytest

from src import codex_runner
from src.command import CommandResult
from src.codex_runner import CodexRunner
from src.models import IssueContext


@pytest.mark.asyncio
async def test_codex_uses_current_noninteractive_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_command(
        args: list[str],
        *,
        timeout: int,
        input_text: str,
    ) -> CommandResult:
        captured.update(args=args, timeout=timeout, input_text=input_text)
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text(
            "- Added safe behavior.\n\n### Configuration and rollout\nSet `MODE=test`.",
            encoding="utf-8",
        )
        return CommandResult(tuple(args), 0, "", "")

    monkeypatch.setattr(codex_runner, "run_command", fake_run_command)
    runner = CodexRunner(timeout_seconds=60)

    report = await runner.run(
        tmp_path,
        "owner/repo",
        IssueContext(number=9, title="Fix it"),
    )

    assert captured["args"] == [
        "codex",
        "exec",
        "--approve-for-me",
        "--output-last-message",
        str(tmp_path / ".codex-final-message.md"),
        "-C",
        str(tmp_path),
        "-",
    ]
    assert "issue #9" in str(captured["input_text"])
    assert "### Configuration and rollout" in report
    assert not (tmp_path / ".codex-final-message.md").exists()
