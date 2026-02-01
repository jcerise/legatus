"""Parse structured review output from Reviewer agent."""

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReviewFinding:
    category: str
    severity: str
    file: str = ""
    description: str = ""
    suggestion: str = ""


@dataclass
class ReviewResult:
    verdict: str  # "approve" or "reject"
    summary: str = ""
    findings: list[ReviewFinding] = field(default_factory=list)
    security_concerns: list[str] = field(default_factory=list)


def parse_reviewer_output(output: str) -> ReviewResult | None:
    """Extract structured review from Reviewer agent output.

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
        logger.error("No JSON review found in Reviewer output")
        return None

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Reviewer JSON: %s", e)
        return None

    return _validate_review(data)


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


def _validate_review(data: dict) -> ReviewResult | None:
    """Validate the parsed JSON against expected schema."""
    if not isinstance(data, dict):
        return None

    verdict = data.get("verdict", "")
    if not isinstance(verdict, str):
        return None

    verdict = verdict.lower().strip()
    if verdict not in ("approve", "reject"):
        logger.error("Invalid reviewer verdict: %r", verdict)
        return None

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)

    # Parse findings
    findings = []
    raw_findings = data.get("findings", [])
    if isinstance(raw_findings, list):
        for f in raw_findings:
            if not isinstance(f, dict):
                continue
            findings.append(ReviewFinding(
                category=f.get("category", "general"),
                severity=f.get("severity", "info"),
                file=f.get("file", ""),
                description=f.get("description", ""),
                suggestion=f.get("suggestion", ""),
            ))

    # Parse security concerns
    security_concerns = []
    raw_security = data.get("security_concerns", [])
    if isinstance(raw_security, list):
        for item in raw_security:
            if isinstance(item, str) and item.strip():
                security_concerns.append(item.strip())

    return ReviewResult(
        verdict=verdict,
        summary=summary,
        findings=findings,
        security_concerns=security_concerns,
    )
