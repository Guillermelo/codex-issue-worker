from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models import AgentConfig, load_config


def test_loads_string_and_detailed_repositories(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
poll_interval_seconds: 60
max_concurrent_jobs: 1
issue_label: agent-ready
workspace_root: /tmp/workspaces
repositories:
  - owner/simple
  - repo: owner/configured
    validation_commands:
      - pytest
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.allowed_repositories == {"owner/simple", "owner/configured"}
    assert config.repository("owner/configured").validation_commands == ["pytest"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_concurrent_jobs": 2},
        {"repos": ["not-a-repository"]},
        {"repos": ["owner/repo", "owner/repo"]},
    ],
)
def test_rejects_unsafe_configuration(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {"repos": ["owner/repo"]}
    values.update(overrides)

    with pytest.raises(ValidationError):
        AgentConfig.model_validate(values)
