import asyncio
import logging
import os

from legatus.agent.executor import Executor
from legatus.agent.memory_bridge import MemoryBridge
from legatus.agent.reporter import Reporter
from legatus.memory.client import Mem0Client
from legatus.models.task import Task
from legatus.redis_client.client import RedisClient
from legatus.redis_client.pubsub import PubSubManager
from legatus.redis_client.task_store import TaskStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def build_prompt(task: Task, memory_context: str) -> str:
    """Construct the full prompt sent to Claude Code."""
    parts = [
        f"# Task: {task.title}",
        "",
        task.description,
        "",
    ]

    if task.acceptance_criteria:
        parts.append("## Acceptance Criteria")
        for criterion in task.acceptance_criteria:
            parts.append(f"- {criterion}")
        parts.append("")

    if memory_context:
        parts.append("## Relevant Context from Memory")
        parts.append(memory_context)
        parts.append("")

    parts.append("## Instructions")
    parts.append("Complete this task by making the necessary code changes in /workspace.")
    parts.append("Focus on clean, well-structured code with appropriate error handling.")

    return "\n".join(parts)


async def run_agent() -> None:
    task_id = os.environ["TASK_ID"]
    agent_id = os.environ["AGENT_ID"]
    redis_url = os.environ["REDIS_URL"]
    mem0_url = os.environ["MEM0_URL"]
    workspace = os.environ.get("WORKSPACE_PATH", "/workspace")

    logger.info("Agent %s starting for task %s", agent_id, task_id)

    # Connect to services
    redis = RedisClient(redis_url)
    await redis.connect()
    task_store = TaskStore(redis)
    pubsub = PubSubManager(redis)
    reporter = Reporter(pubsub, agent_id, task_id)

    mem0 = Mem0Client(mem0_url)
    await mem0.connect()

    try:
        # 1. Fetch task
        task = await task_store.get(task_id)
        if not task:
            await reporter.report_failed(f"Task {task_id} not found")
            return

        await reporter.report_log(f"Agent {agent_id} starting task: {task.title}")

        # 2. Inject memories
        project_id = task_id[:8]
        memory_bridge = MemoryBridge(mem0, project_id)
        context = await memory_bridge.get_context(task)

        # 3. Build prompt
        prompt = build_prompt(task, context)
        logger.info("Prompt length: %d chars", len(prompt))

        # 4. Execute Claude Code
        executor = Executor(workspace=workspace)
        result = executor.run(prompt)

        # 5. Report result
        if result["success"]:
            await reporter.report_complete(result)
            # 6. Extract memories
            await memory_bridge.extract_learnings(task, result)
            logger.info("Task %s completed successfully", task_id)
        else:
            await reporter.report_failed(result.get("error", "Unknown error"))
            logger.error("Task %s failed: %s", task_id, result.get("error"))

    except Exception as e:
        logger.exception("Agent %s encountered an error", agent_id)
        await reporter.report_failed(str(e))
    finally:
        await redis.disconnect()
        await mem0.disconnect()


def main() -> None:
    """Entry point for the `legatus-agent` console script."""
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
