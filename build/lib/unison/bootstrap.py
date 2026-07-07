"""bootstrap.py — BootstrapExecutor: execute bootstrap commands."""

from __future__ import annotations

import subprocess
from pathlib import Path


class BootstrapExecutor:
    """Execute bootstrap commands in a working directory.

    Runs shell commands sequentially.  Returns True if all commands
    succeed, or False if any command fails (non-zero exit code).

    Usage::

        executor = BootstrapExecutor()
        ok = executor.execute(["pip install -e .", "pre-commit install"], workdir=project_dir)
    """

    def execute(self, commands: list[str], workdir: Path) -> bool:
        """Run each command in *commands* sequentially inside *workdir*.

        Args:
            commands: List of shell command strings to execute.
            workdir: Directory in which to run the commands.

        Returns:
            True if all commands exit with code 0, False otherwise.
        """
        if not commands:
            return True

        workdir = Path(workdir)

        for cmd in commands:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=workdir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return False

        return True
