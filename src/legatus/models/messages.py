from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(StrEnum):
    # Orchestrator -> Agent
    TASK_ASSIGNMENT = "task_assignment"
    TASK_CANCEL = "task_cancel"

    # Agent -> Orchestrator
    TASK_UPDATE = "task_update"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    CHECKPOINT_REQUEST = "checkpoint_request"
    LOG_ENTRY = "log_entry"

    # Orchestrator -> CLI (via WebSocket)
    STATUS_UPDATE = "status_update"
    CHECKPOINT_NOTIFICATION = "checkpoint_notification"
    AGENT_EVENT = "agent_event"


class Message(BaseModel):
    type: MessageType
    task_id: str | None = None
    agent_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)
