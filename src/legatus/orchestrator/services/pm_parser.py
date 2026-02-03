"""Parse structured task plans from PM agent output."""

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SubTaskPlan:
    title: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    estimated_complexity: str = "medium"
    depends_on: list[int] = field(default_factory=list)


@dataclass
class PMPlan:
    analysis: str
    subtasks: list[SubTaskPlan]


def parse_pm_output(output: str) -> PMPlan | None:
    """Extract structured plan from PM agent output.

    Looks for a JSON block in ```json ... ``` fences.
    Falls back to searching for raw JSON object with "subtasks" key.
    Returns None if parsing fails entirely.
    """
    # Strategy 1: Find fenced JSON block
    json_str = _extract_fenced_json(output)

    # Strategy 2: Find raw JSON with subtasks key
    if json_str is None:
        json_str = _extract_raw_json(output)

    if json_str is None:
        logger.error("No JSON plan found in PM output")
        return None

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse PM plan JSON: %s", e)
        return None

    return _validate_plan(data)


def _extract_fenced_json(output: str) -> str | None:
    """Extract content from ```json ... ``` fences."""
    pattern = r"```json\s*\n(.*?)\n\s*```"
    matches = re.findall(pattern, output, re.DOTALL)
    if not matches:
        return None
    # Use the last match (most likely the final plan)
    return matches[-1].strip()


def _extract_raw_json(output: str) -> str | None:
    """Find a JSON object containing 'subtasks' key."""
    depth = 0
    start = None
    for i, ch in enumerate(output):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = output[start : i + 1]
                if '"subtasks"' in candidate:
                    return candidate
    return None


def _validate_plan(data: dict) -> PMPlan | None:
    """Validate the parsed JSON against expected schema."""
    if not isinstance(data, dict):
        return None

    subtasks_raw = data.get("subtasks", [])
    if not isinstance(subtasks_raw, list) or len(subtasks_raw) == 0:
        logger.error("PM plan has no subtasks")
        return None

    subtasks = []
    for i, st in enumerate(subtasks_raw):
        if not isinstance(st, dict):
            continue
        title = st.get("title")
        description = st.get("description")
        if not title or not description:
            logger.warning(
                "Subtask %d missing title or description, skipping", i
            )
            continue
        raw_deps = st.get("depends_on", [])
        deps = []
        if isinstance(raw_deps, list):
            for d in raw_deps:
                if isinstance(d, int) and 0 <= d < i:
                    deps.append(d)

        subtasks.append(SubTaskPlan(
            title=title,
            description=description,
            acceptance_criteria=st.get("acceptance_criteria", []),
            estimated_complexity=st.get(
                "estimated_complexity", "medium"
            ),
            depends_on=deps,
        ))

    if not subtasks:
        logger.error("No valid subtasks found in PM plan")
        return None

    return PMPlan(
        analysis=data.get("analysis", ""),
        subtasks=subtasks,
    )
