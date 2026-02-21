import json
import logging
import subprocess

logger = logging.getLogger(__name__)


class Executor:
    """Wraps Claude Code CLI invocation."""

    def __init__(self, workspace: str, timeout: int = 1800, max_turns: int = 200):
        self.workspace = workspace
        self.timeout = timeout
        self.max_turns = max_turns

    def run(self, prompt: str) -> dict:
        """Invoke Claude Code CLI and return a structured result.

        Returns:
            {
                "success": bool,
                "output": str,
                "cost": dict | None,
                "error": str | None,
            }
        """
        cmd = [
            "claude",
            "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "json",
            "--max-turns", str(self.max_turns),
        ]

        logger.info("Executing Claude Code in %s (timeout=%ds)", self.workspace, self.timeout)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode == 0:
                try:
                    output_data = json.loads(result.stdout)
                    is_error = output_data.get("is_error", False)
                    if is_error:
                        return {
                            "success": False,
                            "output": output_data.get("result", ""),
                            "cost": output_data.get("total_cost_usd"),
                            "error": output_data.get("result", "Claude Code reported an error"),
                        }
                    return {
                        "success": True,
                        "output": output_data.get("result", result.stdout),
                        "cost": output_data.get("total_cost_usd"),
                        "num_turns": output_data.get("num_turns"),
                        "session_id": output_data.get("session_id"),
                        "error": None,
                    }
                except json.JSONDecodeError:
                    return {
                        "success": True,
                        "output": result.stdout,
                        "cost": None,
                        "error": None,
                    }
            else:
                logger.error("Claude Code exit code %d: %s", result.returncode, result.stderr)
                return {
                    "success": False,
                    "output": result.stdout,
                    "cost": None,
                    "error": result.stderr or f"Exit code: {result.returncode}",
                }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "cost": None,
                "error": f"Claude Code timed out after {self.timeout}s",
            }
