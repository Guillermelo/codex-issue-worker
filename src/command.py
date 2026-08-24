from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


MAX_CAPTURE_CHARS = 20_000


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult):
        self.result = result
        detail = (result.stderr or result.stdout or "command failed").strip()
        super().__init__(f"{result.args[0]} exited with {result.returncode}: {detail}")


class CommandTimeoutError(RuntimeError):
    pass


async def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int,
    input_text: str | None = None,
    check: bool = True,
) -> CommandResult:
    if not args:
        raise ValueError("command cannot be empty")
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(
                input_text.encode("utf-8") if input_text is not None else None
            ),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise CommandTimeoutError(
            f"{args[0]} timed out after {timeout} seconds"
        ) from exc

    result = CommandResult(
        args=tuple(args),
        returncode=process.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace")[-MAX_CAPTURE_CHARS:],
        stderr=stderr_bytes.decode("utf-8", errors="replace")[-MAX_CAPTURE_CHARS:],
    )
    if check and result.returncode != 0:
        raise CommandError(result)
    return result
