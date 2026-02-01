from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class CheckpointStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Checkpoint(BaseModel):
    id: str = Field(default_factory=lambda: f"cp_{uuid4().hex[:8]}")
    task_id: str
    title: str
    description: str
    status: CheckpointStatus = CheckpointStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    rejection_reason: str | None = None
    source_role: str | None = None
