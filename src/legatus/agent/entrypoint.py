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


def build_dev_prompt(task: Task, memory_context: str) -> str:
    """Construct the prompt for a dev agent."""
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

    # Inject reviewer feedback from prior rejection (retry context)
    reviewer_feedback = task.agent_outputs.get("reviewer_feedback", "")
    if reviewer_feedback:
        parts.append("## Previous Reviewer Feedback")
        parts.append(
            "Your previous attempt was rejected by the reviewer. Address the following issues:"
        )
        parts.append(reviewer_feedback)
        parts.append("")

    # Inject QA feedback from prior rejection (retry context)
    qa_feedback = task.agent_outputs.get("qa_feedback", "")
    if qa_feedback:
        parts.append("## Previous QA Feedback")
        parts.append(
            "Your previous attempt failed QA testing. Address the following test failures:"
        )
        parts.append(qa_feedback)
        parts.append("")

    if memory_context:
        parts.append("## Relevant Context from Memory")
        parts.append(memory_context)
        parts.append("")

    parts.append("## Instructions")
    parts.append("Complete this task by making the necessary code changes in /workspace.")
    parts.append("Focus on clean, well-structured code with appropriate error handling.")
    parts.append("")

    parts.append("## Learnings")
    parts.append(
        "After completing the task, end your response with a `## Learnings` section containing:"
    )
    parts.append("- **Files modified**: List every file you created or changed")
    parts.append("- **Patterns/conventions**: Coding patterns or project conventions you followed")
    parts.append("- **Gotchas**: Anything surprising or tricky you encountered")
    parts.append("- **Dependencies affected**: Any dependencies added, removed, or updated")

    return "\n".join(parts)


def build_pm_prompt(
    task: Task,
    memory_context: str,
    parallel_enabled: bool = False,
) -> str:
    """Construct the prompt for a PM (planning) agent."""
    parts = [
        "# Role: Product Manager / Technical Planner",
        "",
        "You are a PM agent in a multi-agent software engineering"
        " system. Your job is to analyze a feature request and"
        " decompose it into ordered, implementable sub-tasks that"
        " will each be assigned to a separate dev agent.",
        "",
        "## Feature Request",
        f"**Title**: {task.title}",
        "",
        task.description,
        "",
    ]

    if task.acceptance_criteria:
        parts.append("## Acceptance Criteria (from user)")
        for criterion in task.acceptance_criteria:
            parts.append(f"- {criterion}")
        parts.append("")

    if memory_context:
        parts.append("## Relevant Context from Memory")
        parts.append(memory_context)
        parts.append("")

    parts.extend(
        [
            "## Instructions",
            "",
            "1. **Explore the workspace** at /workspace to understand:",
            "   - Project structure (languages, frameworks, build system)",
            "   - Existing patterns and conventions",
            "   - Files that will likely need changes",
            "",
        ]
    )

    if parallel_enabled:
        parts.extend(
            [
                "2. **Decompose the feature** into 2-7 sub-tasks that can"
                " execute **in parallel** where dependencies allow."
                " Each sub-task should be a self-contained unit of work"
                " that a dev agent can implement independently.",
                "",
                "3. **Use `depends_on`** to declare ordering constraints."
                " `depends_on` is an array of 0-based indices referencing"
                " earlier sub-tasks that must complete first. Tasks without"
                " `depends_on` (or with an empty array) will run in parallel"
                " immediately. Only add a dependency if the task truly"
                " requires output from a prior task.",
                "",
                "4. **Output your plan** as a JSON block in the format below.",
                "   You MUST include this JSON block in your response,"
                " wrapped in ```json``` fences:",
                "",
                "```json",
                "{",
                '  "analysis": "Brief analysis of the feature and approach",',
                '  "subtasks": [',
                "    {",
                '      "title": "Short title for the sub-task",',
                '      "description": "Detailed implementation instructions'
                " for the dev agent. Include file paths and function names"
                ' when possible.",',
                '      "acceptance_criteria": ["Criterion 1", "Criterion 2"],',
                '      "estimated_complexity": "low|medium|high",',
                '      "depends_on": []',
                "    },",
                "    {",
                '      "title": "A task that depends on the first",',
                '      "description": "This task needs the output of task 0.",',
                '      "acceptance_criteria": ["Criterion 1"],',
                '      "estimated_complexity": "medium",',
                '      "depends_on": [0]',
                "    }",
                "  ]",
                "}",
                "```",
                "",
                "## Guidelines",
                "- Maximize parallelism: only add a dependency when a task"
                " truly needs another task's output (files, APIs, etc.).",
                "- Each sub-task description should be detailed enough for a"
                " dev agent to implement without additional context.",
                "- Include file paths and function names when you can identify"
                " them from the workspace.",
                "- If a sub-task depends on files created by a prior sub-task,"
                " mention that explicitly in the description and in `depends_on`.",
                "- Do NOT make any file changes. You are planning only.",
                "- Keep the number of sub-tasks reasonable (2-7). Prefer"
                " fewer, larger sub-tasks over many tiny ones.",
            ]
        )
    else:
        parts.extend(
            [
                "2. **Decompose the feature** into 2-7 sequential sub-tasks.",
                "   Each sub-task should be a self-contained unit of work"
                " that a dev agent can implement independently (given prior"
                " sub-tasks are complete).",
                "",
                "3. **Order matters**: Sub-tasks execute sequentially. Earlier"
                " tasks may create files or APIs that later tasks depend on.",
                "",
                "4. **Output your plan** as a JSON block in the format below.",
                "   You MUST include this JSON block in your response,"
                " wrapped in ```json``` fences:",
                "",
                "```json",
                "{",
                '  "analysis": "Brief analysis of the feature and approach",',
                '  "subtasks": [',
                "    {",
                '      "title": "Short title for the sub-task",',
                '      "description": "Detailed implementation instructions'
                " for the dev agent. Include file paths and function names"
                ' when possible.",',
                '      "acceptance_criteria": ["Criterion 1", "Criterion 2"],',
                '      "estimated_complexity": "low|medium|high"',
                "    }",
                "  ]",
                "}",
                "```",
                "",
                "## Guidelines",
                "- Each sub-task description should be detailed enough for a"
                " dev agent to implement without additional context.",
                "- Include file paths and function names when you can identify"
                " them from the workspace.",
                "- If a sub-task depends on files created by a prior sub-task,"
                " mention that explicitly in the description.",
                "- Do NOT make any file changes. You are planning only.",
                "- Keep the number of sub-tasks reasonable (2-7). Prefer"
                " fewer, larger sub-tasks over many tiny ones.",
            ]
        )

    return "\n".join(parts)


def build_architect_prompt(task: Task, memory_context: str) -> str:
    """Construct the prompt for an architect agent."""
    parts = [
        "# Role: Software Architect (praefectus castrorum)",
        "",
        "You are the Architect agent in a multi-agent software"
        " engineering system. Your role is to review a PM's task"
        " decomposition plan, examine the workspace, and produce"
        " architectural design decisions that will guide the dev"
        " agents during implementation.",
        "",
        "## Campaign Overview",
        f"**Title**: {task.title}",
        "",
        task.description,
        "",
    ]

    if task.acceptance_criteria:
        parts.append("## Acceptance Criteria")
        for criterion in task.acceptance_criteria:
            parts.append(f"- {criterion}")
        parts.append("")

    # Include the PM's plan if available
    pm_output = task.agent_outputs.get("pm", "")
    if pm_output:
        parts.append("## PM Decomposition Plan")
        parts.append(pm_output)
        parts.append("")

    # Include sub-task summary if available
    if task.subtask_ids:
        parts.append("## Sub-tasks (from PM)")
        parts.append(
            "The following sub-tasks have been defined and will"
            " be executed sequentially by dev agents:"
        )
        parts.append("")

    if memory_context:
        parts.append("## Relevant Context from Memory")
        parts.append(memory_context)
        parts.append("")

    parts.extend(
        [
            "## Instructions",
            "",
            "1. **Explore the workspace** at /workspace to understand"
            " the existing codebase: structure, patterns, frameworks,"
            " and conventions.",
            "",
            "2. **Review the PM's plan** and assess whether the"
            " proposed decomposition is architecturally sound.",
            "",
            "3. **Produce design decisions** covering:",
            "   - Module structure and component boundaries",
            "   - Interface definitions between components",
            "   - Framework/library/pattern choices",
            "   - Data models and API contracts",
            "   - Any concerns or risks with the approach",
            "",
            "4. **Output your design** as a JSON block in the format"
            " below. You MUST include this JSON block in your"
            " response, wrapped in ```json``` fences:",
            "",
            "```json",
            "{",
            '  "decisions": [',
            "    {",
            '      "title": "Decision title",',
            '      "rationale": "Why this approach",',
            '      "alternatives_considered": ["Alt 1", "Alt 2"]',
            "    }",
            "  ],",
            '  "interfaces": [',
            "    {",
            '      "module": "Module or component name",',
            '      "definition": "Interface description, key functions/methods, data contracts"',
            "    }",
            "  ],",
            '  "concerns": [',
            '    "Any risks, trade-offs, or issues to flag"',
            "  ],",
            '  "design_notes": "Free-form architectural notes and guidance for dev agents"',
            "}",
            "```",
            "",
            "## Guidelines",
            "- Focus on decisions that will guide the dev agents."
            " Be specific about interfaces, data shapes, and patterns.",
            "- If the PM's decomposition has issues, note them in"
            " your concerns. The user can reject the plan if needed.",
            "- Reference existing code patterns from the workspace when recommending approaches.",
            "- Do NOT implement any features. Design and document"
            " only. You are planning, not coding.",
        ]
    )

    return "\n".join(parts)


def build_reviewer_prompt(task: Task, memory_context: str) -> str:
    """Construct the prompt for a reviewer (optio) agent."""
    parts = [
        "# Role: Code Reviewer (optio)",
        "",
        "You are the Reviewer agent in a multi-agent software"
        " engineering system. Your job is to review the code"
        " changes produced by a dev agent for a specific sub-task.",
        "",
        "## Sub-task Under Review",
        f"**Title**: {task.title}",
        "",
        task.description,
        "",
    ]

    if task.acceptance_criteria:
        parts.append("## Acceptance Criteria")
        for criterion in task.acceptance_criteria:
            parts.append(f"- {criterion}")
        parts.append("")

    # Include the dev output
    dev_output = task.agent_outputs.get("dev", "")
    if dev_output:
        parts.append("## Dev Agent Output")
        parts.append(dev_output)
        parts.append("")

    # Include previous reviewer feedback if this is a retry
    reviewer_feedback = task.agent_outputs.get("reviewer_feedback", "")
    if reviewer_feedback:
        parts.append("## Previous Reviewer Feedback")
        parts.append(
            "The dev agent was asked to address the following"
            " feedback from a prior review. Check whether the"
            " issues have been resolved:"
        )
        parts.append(reviewer_feedback)
        parts.append("")

    if memory_context:
        parts.append("## Relevant Context from Memory")
        parts.append(memory_context)
        parts.append("")

    parts.extend(
        [
            "## Instructions",
            "",
            "1. **Examine the workspace** at /workspace to see the current state of the code.",
            "",
            "2. **Run `git diff HEAD~1`** to see exactly what the dev agent changed.",
            "",
            "3. **Review the changes** for:",
            "   - **Correctness**: Does the code do what the task"
            " description and acceptance criteria require?",
            "   - **Security**: Are there any security vulnerabilities"
            " (injection, XSS, hardcoded secrets, etc.)?",
            "   - **Style**: Does the code follow the project's existing patterns and conventions?",
            "   - **Performance**: Are there any obvious performance issues?",
            "",
            "4. **Output your review** as a JSON block in the format"
            " below. You MUST include this JSON block in your"
            " response, wrapped in ```json``` fences:",
            "",
            "```json",
            "{",
            '  "verdict": "approve" or "reject",',
            '  "summary": "Brief summary of the review",',
            '  "findings": [',
            "    {",
            '      "category": "correctness|security|style|performance",',
            '      "severity": "critical|warning|info",',
            '      "file": "path/to/file",',
            '      "description": "What the issue is",',
            '      "suggestion": "How to fix it"',
            "    }",
            "  ],",
            '  "security_concerns": [',
            '    "Description of any security issue found"',
            "  ]",
            "}",
            "```",
            "",
            "## Guidelines",
            '- Use verdict `"approve"` if the code is acceptable (minor style issues are OK).',
            '- Use verdict `"reject"` only for correctness bugs,'
            " missing acceptance criteria, or significant issues.",
            "- **Always** populate `security_concerns` if you find any"
            " security vulnerabilities, even if you approve overall.",
            "- Do NOT modify any files. You are reviewing only.",
        ]
    )

    return "\n".join(parts)


def build_qa_prompt(task: Task, memory_context: str) -> str:
    """Construct the prompt for a QA (tesserarius) agent."""
    parts = [
        "# Role: QA Engineer (tesserarius)",
        "",
        "You are the QA agent in a multi-agent software"
        " engineering system. Your job is to write and run"
        " tests for code produced by a dev agent.",
        "",
        "## Sub-task Under Test",
        f"**Title**: {task.title}",
        "",
        task.description,
        "",
    ]

    if task.acceptance_criteria:
        parts.append("## Acceptance Criteria")
        for criterion in task.acceptance_criteria:
            parts.append(f"- {criterion}")
        parts.append("")

    # Include the dev output
    dev_output = task.agent_outputs.get("dev", "")
    if dev_output:
        parts.append("## Dev Agent Output")
        parts.append(dev_output)
        parts.append("")

    # Include reviewer output if available
    reviewer_output = task.agent_outputs.get("reviewer", "")
    if reviewer_output:
        parts.append("## Reviewer Output")
        parts.append(reviewer_output)
        parts.append("")

    # Include previous QA feedback if this is a retry
    qa_feedback = task.agent_outputs.get("qa_feedback", "")
    if qa_feedback:
        parts.append("## Previous QA Feedback")
        parts.append(
            "A prior QA run failed. The dev agent was asked to"
            " fix the issues below. Verify whether they have"
            " been resolved:"
        )
        parts.append(qa_feedback)
        parts.append("")

    if memory_context:
        parts.append("## Relevant Context from Memory")
        parts.append(memory_context)
        parts.append("")

    parts.extend(
        [
            "## Instructions",
            "",
            "1. **Examine the workspace** at /workspace to see the current state of the code.",
            "",
            "2. **Run `git diff HEAD~1`** to see what the dev agent changed.",
            "",
            "3. **Identify the test framework** used by the project"
            " (e.g., pytest, jest, go test). If none exists, pick"
            " the standard one for the language.",
            "",
            "4. **Write tests** that cover the acceptance criteria"
            " and the code changes. Place test files in the"
            " project's conventional test directory.",
            "",
            "5. **Run the tests** and capture the output.",
            "",
            "6. **Output your results** as a JSON block in the"
            " format below. You MUST include this JSON block in"
            " your response, wrapped in ```json``` fences:",
            "",
            "```json",
            "{",
            '  "verdict": "pass" or "fail",',
            '  "summary": "Brief summary of test results",',
            '  "tests_written": [',
            "    {",
            '      "file": "path/to/test_file.py",',
            '      "description": "What this test covers"',
            "    }",
            "  ],",
            '  "test_results": [',
            "    {",
            '      "name": "test_name",',
            '      "status": "pass|fail|error|skip",',
            '      "output": "Relevant output or error message"',
            "    }",
            "  ],",
            '  "failure_details": "Detailed explanation of failures (empty string if all pass)"',
            "}",
            "```",
            "",
            "## Guidelines",
            '- Use verdict `"pass"` if all tests pass and the acceptance criteria are met.',
            '- Use verdict `"fail"` if any test fails or acceptance criteria are not met.',
            "- Write meaningful tests — not just smoke tests. Cover edge cases where appropriate.",
            "- You have full read-write access and can run commands.",
            "- Do NOT fix the code yourself. Only write and run tests.",
            "",
        ]
    )

    parts.append("## Learnings")
    parts.append(
        "After completing the task, end your response with a `## Learnings` section containing:"
    )
    parts.append("- **Test files created**: List every test file you wrote")
    parts.append("- **Framework/tools used**: Test framework and any helpers used")
    parts.append("- **Coverage notes**: What is and isn't covered by your tests")
    parts.append("- **Gotchas**: Anything surprising or tricky you encountered")

    return "\n".join(parts)


def build_prompt(task: Task, memory_context: str, role: str) -> str:
    """Dispatch to the appropriate prompt builder based on role."""
    if role == "pm":
        parallel = os.environ.get("PARALLEL_ENABLED", "") == "1"
        return build_pm_prompt(task, memory_context, parallel_enabled=parallel)
    if role == "architect":
        return build_architect_prompt(task, memory_context)
    if role == "reviewer":
        return build_reviewer_prompt(task, memory_context)
    if role == "qa":
        return build_qa_prompt(task, memory_context)
    return build_dev_prompt(task, memory_context)


async def run_agent() -> None:
    task_id = os.environ["TASK_ID"]
    agent_id = os.environ["AGENT_ID"]
    agent_role = os.environ.get("AGENT_ROLE", "dev")
    redis_url = os.environ["REDIS_URL"]
    mem0_url = os.environ["MEM0_URL"]
    workspace = os.environ.get("WORKSPACE_PATH", "/workspace")

    logger.info(
        "Agent %s (role=%s) starting for task %s",
        agent_id,
        agent_role,
        task_id,
    )

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

        await reporter.report_log(f"Agent {agent_id} ({agent_role}) starting task: {task.title}")

        # 2. Inject memories
        project_id = os.environ.get("PROJECT_ID") or task_id[:8]
        memory_bridge = MemoryBridge(mem0, project_id)
        context = await memory_bridge.get_context(task)

        # 3. Build prompt (role-aware)
        prompt = build_prompt(task, context, role=agent_role)
        logger.info("Prompt length: %d chars", len(prompt))

        # 4. Execute Claude Code
        timeout = int(os.environ.get("AGENT_TIMEOUT", "600"))
        max_turns = int(os.environ.get("AGENT_MAX_TURNS", "50"))
        executor = Executor(
            workspace=workspace,
            timeout=timeout,
            max_turns=max_turns,
        )
        result = executor.run(prompt)

        # 5. Report result
        if result["success"]:
            await reporter.report_complete(result)
            # 6. Extract memories (dev agents only — PM/architect don't write code)
            if agent_role not in ("pm", "architect", "reviewer", "qa"):
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
