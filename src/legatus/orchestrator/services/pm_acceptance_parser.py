"""Parse PM acceptance review JSON output."""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class PMAcceptanceResult(BaseModel):
    verdict: str = "accept"
    summary: str = ""
    criteria_results: list[dict] = []
    feedback: str = ""


def parse_pm_acceptance_output(output: str) -> PMAcceptanceResult | None:
    """Extract structured JSON from PM acceptance agent output."""
    # Try to find JSON block in ```json fences
    match = re.search(r"```json\s*\n(.*?)\n\s*```", output, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return PMAcceptanceResult(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.debug("Failed to parse PM acceptance JSON from fenced block")

    # Fallback: find any JSON object with "verdict" key
    for m in re.finditer(r"\{[^{}]*\}", output):
        try:
            data = json.loads(m.group())
            if "verdict" in data:
                return PMAcceptanceResult(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    return None
