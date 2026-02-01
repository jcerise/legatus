from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    CREATED = "created"
    PLANNED = "planned"
    ACTIVE = "active"
    REVIEW = "review"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    DONE = "done"


VALID_TRANSITIONS: dict[TaskStatus, list[TaskStatus]] = {
    TaskStatus.CREATED: [TaskStatus.PLANNED],
    TaskStatus.PLANNED: [TaskStatus.ACTIVE],
    TaskStatus.ACTIVE: [TaskStatus.REVIEW, TaskStatus.BLOCKED],
    TaskStatus.BLOCKED: [TaskStatus.ACTIVE],
    TaskStatus.REVIEW: [TaskStatus.DONE, TaskStatus.REJECTED],
    TaskStatus.REJECTED: [TaskStatus.PLANNED],
}


class TaskType(StrEnum):
    FEATURE = "feature"
    BUG_FIX = "bug_fix"
    REFACTOR = "refactor"
    DOCS = "docs"
    TEST = "test"


class TaskEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event: str
    by: str | None = None
    detail: str | None = None


class CheckpointRef(BaseModel):
    required: bool = False
    checkpoint_id: str | None = None
    status: str | None = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:8]}")
    title: str
    description: str
    type: TaskType = TaskType.FEATURE
    status: TaskStatus = TaskStatus.CREATED
    priority: int = Field(default=3, ge=1, le=5)
    created_by: str = "user"
    assigned_to: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    checkpoint: CheckpointRef = Field(default_factory=CheckpointRef)
    artifacts: list[dict] = Field(default_factory=list)
    history: list[TaskEvent] = Field(default_factory=list)
    parent_id: str | None = None
    subtask_ids: list[str] = Field(default_factory=list)
    project: str | None = None
    prompt: str | None = None
    agent_outputs: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
