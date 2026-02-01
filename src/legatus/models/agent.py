from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class AgentRole(StrEnum):
    DEV = "dev"
    PM = "pm"
    ARCHITECT = "architect"


class AgentStatus(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    FAILED = "failed"


class AgentInfo(BaseModel):
    id: str
    role: AgentRole
    status: AgentStatus = AgentStatus.IDLE
    container_id: str | None = None
    task_id: str | None = None
    started_at: datetime | None = None
    error: str | None = None
