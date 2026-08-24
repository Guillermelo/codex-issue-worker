from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    IGNORED = "ignored"
    AUTH_REQUIRED = "auth_required"


class RepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    validation_commands: list[str] | None = None

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        if not REPOSITORY_PATTERN.fullmatch(value):
            raise ValueError("repository must use the owner/name format")
        return value

    @field_validator("validation_commands")
    @classmethod
    def validate_commands(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not command.strip() for command in value):
            raise ValueError("validation commands cannot be empty")
        return value


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_seconds: int = Field(default=300, ge=10)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=1)
    issue_label: str = Field(default="agent-ready", min_length=1)
    workspace_root: Path = Path("/workspace")
    repos: list[RepositoryConfig]
    database_path: Path = Path("/data/agent.db")
    max_attempts: int = Field(default=2, ge=1, le=10)
    command_timeout_seconds: int = Field(default=1800, ge=10)
    codex_timeout_seconds: int = Field(default=3600, ge=10)
    comment_on_failure: bool = False
    keep_workspace_on_failure: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_repositories(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "repositories" in data:
            if "repos" in data:
                raise ValueError("use either repos or repositories, not both")
            data["repos"] = data.pop("repositories")
        repos = data.get("repos")
        if isinstance(repos, list):
            data["repos"] = [
                {"repo": item} if isinstance(item, str) else item for item in repos
            ]
        return data

    @field_validator("issue_label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("issue_label cannot be blank")
        return value

    @field_validator("repos")
    @classmethod
    def unique_repositories(
        cls, value: list[RepositoryConfig]
    ) -> list[RepositoryConfig]:
        if not value:
            raise ValueError("at least one repository must be configured")
        names = [repository.repo for repository in value]
        if len(names) != len(set(names)):
            raise ValueError("repositories must be unique")
        return value

    @property
    def allowed_repositories(self) -> set[str]:
        return {repository.repo for repository in self.repos}

    def repository(self, name: str) -> RepositoryConfig:
        for repository in self.repos:
            if repository.repo == name:
                return repository
        raise KeyError(name)


class IssueSummary(BaseModel):
    number: int
    title: str


class IssueContext(BaseModel):
    number: int
    title: str
    body: str = ""
    comments: list[str] = Field(default_factory=list)


class Job(BaseModel):
    id: int
    repo: str
    issue_number: int
    status: JobStatus
    attempts: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    branch: str | None = None
    pr_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobRequest(BaseModel):
    repo: str
    issue_number: int


class SolveRequest(BaseModel):
    repo: str
    issue: int = Field(gt=0)

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        if not REPOSITORY_PATTERN.fullmatch(value):
            raise ValueError("repository must use the owner/name format")
        return value


class ScanResult(BaseModel):
    discovered: int = 0
    enqueued: int = 0
    skipped: int = 0


def load_config(path: str | Path) -> AgentConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a YAML mapping")
    return AgentConfig.model_validate(raw)
