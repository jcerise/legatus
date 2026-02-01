"""Parse structured design output from Architect agent."""

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ArchitectPlan:
    decisions: list[dict] = field(default_factory=list)
    interfaces: list[dict] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    design_notes: str = ""


def parse_architect_output(output: str) -> ArchitectPlan | None:
    """Extract structured design from Architect agent output.

    Looks for a JSON block in ```json ... ``` fences.
    Falls back to searching for raw JSON object with "decisions" key.
    Returns None if parsing fails entirely.
    """
    # Strategy 1: Find fenced JSON block
    json_str = _extract_fenced_json(output)

    # Strategy 2: Find raw JSON with decisions key
    if json_str is None:
        json_str = _extract_raw_json(output)

    if json_str is None:
        logger.error("No JSON design found in Architect output")
        return None

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse Architect design JSON: %s", e,
        )
        return None

    return _validate_plan(data)


def _extract_fenced_json(output: str) -> str | None:
    """Extract content from ```json ... ``` fences."""
    pattern = r"```json\s*\n(.*?)\n\s*```"
    matches = re.findall(pattern, output, re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip()


def _extract_raw_json(output: str) -> str | None:
    """Find a JSON object containing 'decisions' key."""
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
                if '"decisions"' in candidate:
                    return candidate
    return None


def _validate_plan(data: dict) -> ArchitectPlan | None:
    """Validate the parsed JSON against expected schema."""
    if not isinstance(data, dict):
        return None

    decisions = data.get("decisions", [])
    if not isinstance(decisions, list):
        decisions = []

    interfaces = data.get("interfaces", [])
    if not isinstance(interfaces, list):
        interfaces = []

    concerns = data.get("concerns", [])
    if not isinstance(concerns, list):
        concerns = []

    design_notes = data.get("design_notes", "")
    if not isinstance(design_notes, str):
        design_notes = str(design_notes)

    if not decisions and not interfaces and not design_notes:
        logger.error(
            "Architect output has no decisions, interfaces,"
            " or design notes",
        )
        return None

    return ArchitectPlan(
        decisions=decisions,
        interfaces=interfaces,
        concerns=concerns,
        design_notes=design_notes,
    )
