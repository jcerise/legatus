from legatus.models.messages import Message, MessageType
from legatus.redis_client.pubsub import PubSubManager


class Reporter:
    """Reports agent status to the orchestrator via Redis pub/sub."""

    def __init__(self, pubsub: PubSubManager, agent_id: str, task_id: str):
        self.pubsub = pubsub
        self.agent_id = agent_id
        self.task_id = task_id

    async def report_log(self, message: str) -> None:
        await self.pubsub.publish(
            PubSubManager.CHANNEL_AGENT,
            Message(
                type=MessageType.LOG_ENTRY,
                task_id=self.task_id,
                agent_id=self.agent_id,
                data={"message": message},
            ),
        )

    async def report_complete(self, result: dict) -> None:
        await self.pubsub.publish(
            PubSubManager.CHANNEL_AGENT,
            Message(
                type=MessageType.TASK_COMPLETE,
                task_id=self.task_id,
                agent_id=self.agent_id,
                data=result,
            ),
        )

    async def report_failed(self, error: str) -> None:
        await self.pubsub.publish(
            PubSubManager.CHANNEL_AGENT,
            Message(
                type=MessageType.TASK_FAILED,
                task_id=self.task_id,
                agent_id=self.agent_id,
                data={"error": error},
            ),
        )
