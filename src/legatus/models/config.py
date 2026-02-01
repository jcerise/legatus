from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ReviewMode(StrEnum):
    PER_SUBTASK = "per_subtask"
    PER_CAMPAIGN = "per_campaign"


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379"


class Mem0Config(BaseModel):
    url: str = "http://localhost:8000"


class OrchestratorConfig(BaseModel):
    host: str = "0.0.0.0"
    rest_port: int = 8420


class AgentConfig(BaseModel):
    image: str = "legatus-agent:latest"
    timeout: int = 600
    max_turns: int = 50
    network: str = "legatus_default"
    host_workspace_path: str = ""
    architect_review: bool = True
    reviewer_enabled: bool = False
    review_mode: ReviewMode = ReviewMode.PER_SUBTASK
    reviewer_max_retries: int = 1


class LegatusSettings(BaseSettings):
    redis: RedisConfig = Field(default_factory=RedisConfig)
    mem0: Mem0Config = Field(default_factory=Mem0Config)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    workspace_path: str = "/workspace"

    model_config = {"env_prefix": "LEGATUS_", "env_nested_delimiter": "__"}
