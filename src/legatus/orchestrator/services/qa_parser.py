"""Parse structured QA output from QA (tesserarius) agent."""

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TestFileWritten:
    file: str
    description: str = ""


@dataclass
class TestResult:
    name: str
    status: str = ""  # pass / fail / error / skip
    output: str = ""


@dataclass
class QAResult:
    verdict: str  # "pass" or "fail"
    summary: str = ""
    tests_written: list[TestFileWritten] = field(default_factory=list)
    test_results: list[TestResult] = field(default_factory=list)
    failure_details: str = ""


def parse_qa_output(output: str) -> QAResult | None:
    """Extract structured QA result from QA agent output.

    Looks for a JSON block in ```json ... ``` fences.
    Falls back to searching for raw JSON object with "verdict" key.
    Returns None if parsing fails entirely.
    """
    # Strategy 1: Find fenced JSON block
    json_str = _extract_fenced_json(output)

    # Strategy 2: Find raw JSON with verdict key
    if json_str is None:
        json_str = _extract_raw_json(output)

    if json_str is None:
        logger.error("No JSON result found in QA output")
        return None

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse QA JSON: %s", e)
        return None

    return _validate_qa_result(data)


def _extract_fenced_json(output: str) -> str | None:
    """Extract content from ```json ... ``` fences."""
    pattern = r"```json\s*\n(.*?)\n\s*```"
    matches = re.findall(pattern, output, re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip()


def _extract_raw_json(output: str) -> str | None:
    """Find a JSON object containing 'verdict' key."""
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
                if '"verdict"' in candidate:
                    return candidate
    return None


def _validate_qa_result(data: dict) -> QAResult | None:
    """Validate the parsed JSON against expected QA schema."""
    if not isinstance(data, dict):
        return None

    verdict = data.get("verdict", "")
    if not isinstance(verdict, str):
        return None

    verdict = verdict.lower().strip()
    if verdict not in ("pass", "fail"):
        logger.error("Invalid QA verdict: %r", verdict)
        return None

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)

    # Parse tests_written
    tests_written = []
    raw_written = data.get("tests_written", [])
    if isinstance(raw_written, list):
        for item in raw_written:
            if not isinstance(item, dict):
                continue
            tests_written.append(
                TestFileWritten(
                    file=item.get("file", ""),
                    description=item.get("description", ""),
                )
            )

    # Parse test_results
    test_results = []
    raw_results = data.get("test_results", [])
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            test_results.append(
                TestResult(
                    name=item.get("name", ""),
                    status=item.get("status", ""),
                    output=item.get("output", ""),
                )
            )

    # Parse failure_details
    failure_details = data.get("failure_details", "")
    if not isinstance(failure_details, str):
        failure_details = str(failure_details)

    return QAResult(
        verdict=verdict,
        summary=summary,
        tests_written=tests_written,
        test_results=test_results,
        failure_details=failure_details,
    )
