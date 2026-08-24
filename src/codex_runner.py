from __future__ import annotations

from pathlib import Path

from .command import CommandError, run_command
from .models import IssueContext


AUTH_MARKERS = (
    "codex login",
    "not logged in",
    "authentication required",
    "unauthorized",
    "http 401",
    "missing bearer",
)


class CodexAuthError(RuntimeError):
    pass


class CodexRunner:
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def build_prompt(repo: str, issue: IssueContext) -> str:
        comments = (
            "\n\n".join(
                f"Comment {index}:\n{body}"
                for index, body in enumerate(issue.comments, start=1)
            )
            or "No comments."
        )
        return f"""You are working in the repository {repo}.

Read AGENTS.md first if it exists and follow its instructions.

Solve GitHub issue #{issue.number}.

Title:
{issue.title}

Body:
{issue.body or "No issue body provided."}

Relevant issue comments:
{comments}

Requirements:
- Understand the root cause before editing.
- Make the smallest safe change that resolves the issue.
- Add regression tests when appropriate.
- Respect repository validation instructions.
- Do not modify unrelated code.
- Never expose or commit secrets.
- Do not commit or push; the wrapper handles Git operations.
"""

    async def run(self, workspace: Path, repo: str, issue: IssueContext) -> None:
        prompt = self.build_prompt(repo, issue)
        try:
            await run_command(
                ["codex", "exec", "--full-auto", "-C", str(workspace), "-"],
                timeout=self.timeout_seconds,
                input_text=prompt,
            )
        except CommandError as exc:
            message = f"{exc.result.stderr}\n{exc.result.stdout}".lower()
            if any(marker in message for marker in AUTH_MARKERS):
                raise CodexAuthError(
                    "Codex authentication is required; run codex login"
                ) from exc
            raise
