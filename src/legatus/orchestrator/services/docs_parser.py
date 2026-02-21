"""Parse docs agent JSON output."""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DocsResult(BaseModel):
    files_updated: list[str] = []
    summary: str = ""


def parse_docs_output(output: str) -> DocsResult | None:
    """Extract structured JSON from docs agent output."""
    # Try to find JSON block in ```json fences
    match = re.search(r"```json\s*\n(.*?)\n\s*```", output, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return DocsResult(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.debug("Failed to parse docs JSON from fenced block")

    # Fallback: find any JSON object in the output
    for m in re.finditer(r"\{[^{}]*\}", output):
        try:
            data = json.loads(m.group())
            if "files_updated" in data or "summary" in data:
                return DocsResult(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    return None
