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

Your final response will be used in the Pull Request description. It must:
- Start with 2-5 concise bullets explaining behavior changed and why.
- Include a `### Configuration and rollout` section with exact environment
  variable names, safe example values, required deployment/restart steps, and
  a verification step.
- Explicitly say when no operator action is required.
- Never include secret values or merely repeat a list of changed files.
"""

    async def run(self, workspace: Path, repo: str, issue: IssueContext) -> str:
        prompt = self.build_prompt(repo, issue)
        final_message_path = workspace / ".codex-final-message.md"
        try:
            await run_command(
                [
                    "codex",
                    "exec",
                    "--approve-for-me",
                    "--output-last-message",
                    str(final_message_path),
                    "-C",
                    str(workspace),
                    "-",
                ],
                timeout=self.timeout_seconds,
                input_text=prompt,
            )
            if not final_message_path.is_file():
                return ""
            return final_message_path.read_text(encoding="utf-8").strip()[:8000]
        except CommandError as exc:
            message = f"{exc.result.stderr}\n{exc.result.stdout}".lower()
            if any(marker in message for marker in AUTH_MARKERS):
                raise CodexAuthError(
                    "Codex authentication is required; run codex login"
                ) from exc
            raise
        finally:
            final_message_path.unlink(missing_ok=True)
