import logging
import subprocess

logger = logging.getLogger(__name__)


class GitOps:
    """Simple git operations on the workspace directory."""

    def __init__(self, workspace_path: str):
        self.workspace = workspace_path

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=check,
        )

    def init_repo(self) -> None:
        """Initialize a git repo if one doesn't exist."""
        result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        if result.returncode != 0:
            self._run("init")
            self._run("config", "user.email", "legatus@local")
            self._run("config", "user.name", "Legatus")
            logger.info("Initialized git repo in %s", self.workspace)

    def commit_changes(self, message: str) -> str | None:
        """Stage all changes and commit.

        Returns the commit hash, or None if there was nothing to commit.
        """
        self._run("add", "-A")

        # Check if there are staged changes
        result = self._run("diff", "--cached", "--quiet", check=False)
        if result.returncode == 0:
            logger.debug("No staged changes to commit")
            return None

        result = self._run("commit", "-m", message)
        logger.info("Committed: %s", message)

        # Parse commit hash from first line
        return self._parse_commit_hash(result.stdout)

    def _parse_commit_hash(self, output: str) -> str:
        """Extract commit hash from git commit output.

        Typical output: '[main abc1234] commit message'
        """
        try:
            bracket_content = output.split("]")[0].split("[")[1]
            return bracket_content.split()[-1]
        except (IndexError, ValueError):
            return "unknown"
