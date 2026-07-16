"""Foreground invocation artifact and process-identity primitives.

This module deliberately owns no terminal, child process, or recovery-loop
behavior.  It defines the durable evidence that those later layers must
produce and verify before treating foreground work as complete.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unison.interfaces import AgentSpec
from unison.io import atomic_read_json, atomic_write_json


ARTIFACT_SCHEMA_VERSION = 1


def build_foreground_command(spec: AgentSpec, prompt: str) -> list[str]:
    """Build one native interactive Claude/Codex argv without headless flags.

    The caller's approved prompt-delivery policy is represented by the final
    positional token.  It starts the first native user turn but does not grant
    Unison any authority to answer later prompts or approvals.
    """
    if spec.runtime == "claude":
        command = ["claude", "--permission-mode", "manual"]
        if spec.model and spec.model != "default":
            command += ["--model", spec.model]
        if spec.reasoning_effort:
            command += ["--effort", spec.reasoning_effort]
        return [*command, prompt]

    if spec.runtime == "codex":
        if spec.reasoning_effort:
            raise ValueError(
                "foreground Codex reasoning_effort is unsupported until its interactive flag is verified"
            )
        command = [
            "codex", "--sandbox", "workspace-write", "--ask-for-approval", "on-request",
        ]
        if spec.model and spec.model != "default":
            command += ["--model", spec.model]
        return [*command, prompt]

    raise ValueError("foreground execution only supports claude and codex")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ProcessIdentity:
    """A PID paired with an opaque OS process-start fingerprint."""

    pid: int
    start_identity: str


def read_process_identity(pid: int) -> ProcessIdentity | None:
    """Return *pid* identity when the operating system can verify it.

    A missing, unreadable, or unsupported process source remains unknown;
    callers must not turn this into evidence that a process has exited.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform != "linux":
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        closing_paren = stat.rfind(")")
        fields_after_comm = stat[closing_paren + 2 :].split()
        # /proc/<pid>/stat field 22 is starttime.  The remaining fields begin
        # at field 3, making it element 19 after the process name.
        starttime = fields_after_comm[19]
    except (IndexError, OSError, UnicodeError):
        return None
    if not starttime.isdigit():
        return None
    return ProcessIdentity(pid=pid, start_identity=f"linux:{starttime}")


@dataclass(frozen=True)
class ForegroundInvocation:
    """Paths and validated artifact access for one foreground invocation."""

    invocation_id: str
    directory: Path

    @property
    def request_path(self) -> Path:
        return self.directory / "request.json"

    @property
    def child_path(self) -> Path:
        return self.directory / "child.json"

    @property
    def result_path(self) -> Path:
        return self.directory / "result.json"

    @property
    def heartbeat_path(self) -> Path:
        return self.directory / "heartbeat.json"

    @property
    def output_path(self) -> Path:
        return self.directory / "output.log"

    @classmethod
    def create(
        cls,
        *,
        run_dir: Path,
        phase: str,
        role: str,
        runtime: str,
        workdir: Path,
        command: list[str],
        prompt_path: Path,
        baseline_commit: str | None,
    ) -> "ForegroundInvocation":
        """Create a unique run-scoped directory and its immutable request."""
        invocation_id = str(uuid.uuid4())
        directory = Path(run_dir) / "foreground" / invocation_id
        invocation = cls(invocation_id=invocation_id, directory=directory)
        atomic_write_json(
            invocation.request_path,
            {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "invocation_id": invocation_id,
                "phase": phase,
                "role": role,
                "runtime": runtime,
                "launched_at": _utc_now(),
                "workdir": str(Path(workdir)),
                "command": list(command),
                "prompt_path": str(Path(prompt_path)),
                "baseline_commit": baseline_commit,
            },
        )
        return invocation

    def read_request(self) -> dict[str, Any]:
        request = atomic_read_json(self.request_path)
        if not isinstance(request, dict) or not self._matches_invocation(request):
            raise ValueError("foreground request artifact is invalid")
        return request

    def write_child(self, child: ProcessIdentity, *, process_group_id: int) -> None:
        atomic_write_json(
            self.child_path,
            {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "invocation_id": self.invocation_id,
                "child_pid": child.pid,
                "child_start_identity": child.start_identity,
                "child_process_group_id": process_group_id,
            },
        )

    def write_result(
        self,
        child: ProcessIdentity,
        *,
        exit_code: int,
        started_at: str,
    ) -> None:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError("foreground exit code must be an integer")
        atomic_write_json(
            self.result_path,
            {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "invocation_id": self.invocation_id,
                "child_pid": child.pid,
                "child_start_identity": child.start_identity,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "exit_code": exit_code,
            },
        )

    def write_heartbeat(self, wrapper: ProcessIdentity, *, observed_at: str) -> None:
        atomic_write_json(
            self.heartbeat_path,
            {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "invocation_id": self.invocation_id,
                "wrapper_pid": wrapper.pid,
                "wrapper_start_identity": wrapper.start_identity,
                "observed_at": observed_at,
            },
        )

    def read_verified_result(self) -> dict[str, Any] | None:
        result = atomic_read_json(self.result_path)
        child = atomic_read_json(self.child_path)
        if (
            not isinstance(result, dict)
            or not isinstance(child, dict)
            or not self._matches_invocation(result)
            or not self._matches_invocation(child)
        ):
            return None
        if isinstance(result.get("exit_code"), bool) or not isinstance(result.get("exit_code"), int):
            return None
        if result.get("child_pid") != child.get("child_pid"):
            return None
        if result.get("child_start_identity") != child.get("child_start_identity"):
            return None
        return result

    def read_verified_heartbeat(
        self, wrapper: ProcessIdentity,
    ) -> dict[str, Any] | None:
        heartbeat = atomic_read_json(self.heartbeat_path)
        if not isinstance(heartbeat, dict) or not self._matches_invocation(heartbeat):
            return None
        if heartbeat.get("wrapper_pid") != wrapper.pid:
            return None
        if heartbeat.get("wrapper_start_identity") != wrapper.start_identity:
            return None
        if not isinstance(heartbeat.get("observed_at"), str):
            return None
        return heartbeat

    def _matches_invocation(self, artifact: dict[str, Any] | None) -> bool:
        return (
            isinstance(artifact, dict)
            and artifact.get("schema_version") == ARTIFACT_SCHEMA_VERSION
            and artifact.get("invocation_id") == self.invocation_id
        )