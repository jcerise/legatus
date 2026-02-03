import logging
import os
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    success: bool
    commit_hash: str | None = None
    conflict_files: list[str] = field(default_factory=list)


class GitOps:
    """Git operations on the workspace directory."""

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

    def _run_in(
        self, cwd: str, *args: str, check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a git command in a specific directory."""
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )

    def init_repo(self) -> None:
        """Initialize a git repo if one doesn't exist."""
        try:
            # Mark workspace as safe (ownership may differ in containers)
            subprocess.run(
                [
                    "git", "config", "--global", "--add",
                    "safe.directory", self.workspace,
                ],
                capture_output=True, text=True, check=True,
            )

            result = self._run(
                "rev-parse", "--is-inside-work-tree", check=False,
            )
            if result.returncode != 0:
                self._run("init")
                logger.info(
                    "Initialized git repo in %s", self.workspace,
                )

            # Always ensure identity is configured
            self._run("config", "user.email", "legatus@local")
            self._run("config", "user.name", "Legatus")

            # Ensure at least one commit exists — worktrees require it
            head = self._run("rev-parse", "HEAD", check=False)
            if head.returncode != 0:
                self._run(
                    "commit", "--allow-empty",
                    "-m", "legatus: initial commit",
                )
                logger.info("Created initial commit in %s", self.workspace)
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Git init/config failed (permissions?): %s", e,
            )

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

    # --------------------------------------------------
    # Branch operations
    # --------------------------------------------------

    def get_current_branch(self) -> str:
        """Return the current branch name."""
        result = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def checkout(self, branch_name: str) -> None:
        """Checkout an existing branch."""
        self._run("checkout", branch_name)

    def ensure_working_branch(self, branch_name: str) -> None:
        """Create a branch from current HEAD and check it out.

        If the branch already exists, just check it out.
        """
        result = self._run(
            "rev-parse", "--verify", branch_name, check=False,
        )
        if result.returncode == 0:
            self._run("checkout", branch_name)
        else:
            self._run("checkout", "-b", branch_name)
        logger.info("On working branch: %s", branch_name)

    def delete_branch(self, branch_name: str) -> None:
        """Delete a local branch."""
        self._run("branch", "-D", branch_name, check=False)

    # --------------------------------------------------
    # Worktree operations
    # --------------------------------------------------

    def create_worktree(
        self, worktree_path: str, branch_name: str,
    ) -> None:
        """Create a git worktree at the given path on a new branch.

        The new branch is created from the current HEAD of the
        main workspace (which should be on the campaign working branch).
        """
        # Mark the worktree path as safe for git
        subprocess.run(
            [
                "git", "config", "--global", "--add",
                "safe.directory", worktree_path,
            ],
            capture_output=True, text=True, check=False,
        )
        self._run("worktree", "add", worktree_path, "-b", branch_name)
        logger.info(
            "Created worktree at %s on branch %s",
            worktree_path, branch_name,
        )

    def remove_worktree(self, worktree_path: str) -> None:
        """Remove a git worktree."""
        self._run("worktree", "remove", worktree_path, "--force", check=False)
        # Prune stale worktree entries
        self._run("worktree", "prune", check=False)
        logger.info("Removed worktree at %s", worktree_path)

    def commit_in_worktree(
        self, worktree_path: str, message: str,
    ) -> str | None:
        """Stage all changes and commit in a specific worktree.

        Uses explicit ``--git-dir`` and ``--work-tree`` flags so that
        commits always target the correct branch in the **main**
        repository.  This is necessary because the agent container
        cannot follow the worktree's ``.git`` file (its ``gitdir``
        path points to the orchestrator's filesystem) and may
        overwrite it with ``git init``, creating a detached repo.
        By-passing the ``.git`` file entirely avoids this problem.

        Returns the commit hash, or None if nothing to commit.
        """
        worktree_name = os.path.basename(worktree_path)
        git_dir = os.path.join(
            self.workspace, ".git", "worktrees", worktree_name,
        )

        base_cmd = [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={worktree_path}",
        ]

        subprocess.run(
            [*base_cmd, "add", "-A"],
            capture_output=True, text=True, check=True,
        )

        result = subprocess.run(
            [*base_cmd, "diff", "--cached", "--quiet"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            logger.debug("No staged changes in worktree %s", worktree_path)
            return None

        result = subprocess.run(
            [*base_cmd, "commit", "-m", message],
            capture_output=True, text=True, check=True,
        )
        logger.info("Committed in worktree %s: %s", worktree_path, message)
        return self._parse_commit_hash(result.stdout)

    def resolve_conflicts_theirs(self, files: list[str]) -> None:
        """Resolve merge conflicts by accepting the incoming (theirs) version.

        For each file, runs ``git checkout --theirs`` to pick the
        task-branch version, then stages the result.
        """
        for f in files:
            self._run("checkout", "--theirs", "--", f)
            self._run("add", "--", f)

    # --------------------------------------------------
    # Merge operations
    # --------------------------------------------------

    def merge_branch(
        self, source_branch: str, message: str,
    ) -> MergeResult:
        """Merge source_branch into the current branch in the main workspace.

        Returns a MergeResult indicating success or conflict.
        """
        result = self._run(
            "merge", source_branch, "-m", message, "--no-ff", check=False,
        )

        if result.returncode == 0:
            commit_hash = self._parse_commit_hash(result.stdout)
            logger.info(
                "Merged %s: %s", source_branch, commit_hash,
            )
            return MergeResult(success=True, commit_hash=commit_hash)

        # Check for merge conflict
        conflict_files = self.get_conflict_files()
        if conflict_files:
            logger.warning(
                "Merge conflict merging %s: %s",
                source_branch, conflict_files,
            )
            return MergeResult(
                success=False, conflict_files=conflict_files,
            )

        # Some other merge failure
        logger.error(
            "Merge failed for %s: %s %s",
            source_branch, result.stdout, result.stderr,
        )
        return MergeResult(success=False)

    def abort_merge(self) -> None:
        """Abort an in-progress merge."""
        self._run("merge", "--abort", check=False)

    def get_conflict_files(self) -> list[str]:
        """List files with merge conflicts."""
        result = self._run("diff", "--name-only", "--diff-filter=U", check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

    def commit_merge_resolution(self, message: str) -> str | None:
        """Stage all files and commit a merge resolution.

        Used after the user resolves conflicts manually.
        """
        self._run("add", "-A")
        result = self._run("commit", "-m", message, check=False)
        if result.returncode != 0:
            logger.error("Failed to commit merge resolution: %s", result.stderr)
            return None
        logger.info("Committed merge resolution: %s", message)
        return self._parse_commit_hash(result.stdout)
