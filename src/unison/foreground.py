"""Foreground invocation artifact and process-identity primitives.

This module deliberately owns no terminal, child process, or recovery-loop
behavior.  It defines the durable evidence that those later layers must
produce and verify before treating foreground work as complete.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import select
import signal
import shutil
import subprocess
import sys
import termios
import time
import tty
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unison.interfaces import AgentSpec
from unison.io import atomic_read_json, atomic_write_json
from unison.runners.base import mask_secrets


ARTIFACT_SCHEMA_VERSION = 1


def _build_foreground_base_command(spec: AgentSpec) -> list[str]:
    if spec.runtime == "claude":
        command = ["claude", "--permission-mode", "manual"]
        if spec.model and spec.model != "default":
            command += ["--model", spec.model]
        if spec.reasoning_effort:
            command += ["--effort", spec.reasoning_effort]
        return command

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
        return command

    raise ValueError("foreground execution only supports claude and codex")


def _foreground_command_with_prompt(command: list[str], prompt: str) -> list[str]:
    if prompt.startswith("-"):
        raise ValueError("foreground prompt must not begin with '-'" )
    return [*command, prompt]


def build_foreground_command(spec: AgentSpec, prompt: str) -> list[str]:
    """Build one native interactive Claude/Codex argv without headless flags.

    The caller's approved prompt-delivery policy is represented by the final
    positional token.  It starts the first native user turn but does not grant
    Unison any authority to answer later prompts or approvals.
    """
    return _foreground_command_with_prompt(_build_foreground_base_command(spec), prompt)


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


def foreground_child_and_group_status(invocation: "ForegroundInvocation") -> str:
    """Return ``dead``, ``live``, or ``unknown`` for a recorded child group.

    This is deliberately Linux-only.  Missing/malformed child evidence and any
    unsupported or unverifiable process identity are ``unknown``; callers must
    refuse replacement rather than infer that the invocation ended.
    """
    if sys.platform != "linux":
        return "unknown"
    child = atomic_read_json(invocation.child_path)
    if not isinstance(child, dict) or not invocation._matches_invocation(child):
        return "unknown"
    pid = child.get("child_pid")
    identity = child.get("child_start_identity")
    group_id = child.get("child_process_group_id")
    if (
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
        or not isinstance(identity, str) or not identity
        or isinstance(group_id, bool) or not isinstance(group_id, int) or group_id <= 0
    ):
        return "unknown"
    current = read_process_identity(pid)
    if current is None:
        process_exists = Path(f"/proc/{pid}").exists()
        if process_exists:
            return "unknown"
    elif current.start_identity == identity:
        return "live"
    else:
        return "unknown"
    try:
        members = [
            int(entry.name)
            for entry in Path("/proc").iterdir()
            if entry.name.isdigit()
            and _linux_process_group_id(int(entry.name)) == group_id
        ]
    except OSError:
        return "unknown"
    return "dead" if not members else "live"


def _linux_process_group_id(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        closing_paren = stat.rfind(")")
        fields_after_comm = stat[closing_paren + 2 :].split()
        process_group_id = fields_after_comm[2]
    except (IndexError, OSError, UnicodeError):
        return None
    return int(process_group_id) if process_group_id.isdigit() else None


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
        verified = self.read_verified_result_evidence()
        return verified[0] if verified is not None else None

    def read_verified_result_evidence(self) -> tuple[dict[str, Any], bytes] | None:
        """Return verified result plus immutable evidence bytes from one read.

        The returned bytes contain the exact result/child payloads used for
        validation, so callers can derive a corruption-detection digest without
        re-reading either mutable artifact.
        """
        try:
            result_bytes = self.result_path.read_bytes()
            child_bytes = self.child_path.read_bytes()
            result = json.loads(result_bytes)
            child = json.loads(child_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
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
        return result, result_bytes + b"\0" + child_bytes

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


def prepare_foreground_invocation(
    *,
    run_dir: Path,
    phase: str,
    spec: AgentSpec,
    prompt: str,
    workdir: Path,
    baseline_commit: str | None,
) -> ForegroundInvocation:
    """Create request/prompt artifacts for one approved native first turn."""
    command = _build_foreground_base_command(spec)
    # Validate prompt before creating artifacts; wrapper appends it only at exec.
    _foreground_command_with_prompt(command, prompt)
    invocation_id = str(uuid.uuid4())
    directory = Path(run_dir) / "foreground" / invocation_id
    prompt_path = directory / "prompt.txt"
    directory.mkdir(parents=True, exist_ok=False)
    prompt_path.write_text(prompt, encoding="utf-8")
    os.chmod(prompt_path, 0o600)
    invocation = ForegroundInvocation(invocation_id=invocation_id, directory=directory)
    atomic_write_json(
        invocation.request_path,
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "invocation_id": invocation_id,
            "phase": phase,
            "role": spec.role,
            "runtime": spec.runtime,
            "launched_at": _utc_now(),
            "workdir": str(Path(workdir)),
            "command": command,
            "prompt_path": str(prompt_path),
            "baseline_commit": baseline_commit,
        },
    )
    return invocation


def _terminal_size(fd: int) -> bytes:
    try:
        return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
    except OSError as exc:
        raise RuntimeError("foreground wrapper cannot read visible terminal size") from exc


def _apply_terminal_size(fd: int, size: bytes) -> None:
    if len(size) != 8:
        raise RuntimeError("foreground wrapper received invalid terminal size")
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
    except OSError as exc:
        raise RuntimeError("foreground wrapper cannot initialize child terminal size") from exc


def _write_all(fd: int, data: bytes) -> None:
    while data:
        written = os.write(fd, data)
        data = data[written:]


def _wrapper_request(invocation: ForegroundInvocation) -> tuple[list[str], Path, str]:
    request = invocation.read_request()
    command = request.get("command")
    workdir = request.get("workdir")
    prompt_value = request.get("prompt_path")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) or not arg for arg in command)
        or not isinstance(workdir, str)
        or not isinstance(prompt_value, str)
    ):
        raise RuntimeError("foreground request has invalid wrapper fields")

    root = invocation.directory.resolve()
    prompt_path = Path(prompt_value).resolve()
    if prompt_path != root / "prompt.txt":
        raise RuntimeError("foreground prompt path escapes invocation directory")
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("foreground prompt file is unreadable") from exc
    try:
        command = _foreground_command_with_prompt(command, prompt)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return command, Path(workdir), _utc_now()


def run_foreground_wrapper(
    invocation: ForegroundInvocation,
    *,
    stdin_fd: int = 0,
    stdout_fd: int = 1,
    heartbeat_interval: float = 30.0,
) -> int:
    """Relay a native CLI through a PTY and write durable evidence.

    The wrapper copies user bytes and child output verbatim. It never sends
    later input, approves requests, retries a child, or invokes a shell.
    """
    original_terminal: list[Any] | None = None
    raw_output: Path | None = None
    master_fd = -1
    raw_mode_enabled = False
    try:
        if not os.isatty(stdin_fd) or not os.isatty(stdout_fd):
            raise RuntimeError("foreground wrapper requires a visible TTY")
        wrapper = read_process_identity(os.getpid())
        if wrapper is None:
            raise RuntimeError("foreground wrapper identity is unverifiable")
        if heartbeat_interval <= 0:
            raise ValueError("foreground heartbeat_interval must be positive")

        command, workdir, started_at = _wrapper_request(invocation)
        original_terminal = termios.tcgetattr(stdin_fd)
        terminal_size = _terminal_size(stdin_fd)
        raw_output = invocation.directory / ".output.raw"
        input_open = True
        next_heartbeat = time.monotonic() + heartbeat_interval
        tty.setraw(stdin_fd)
        raw_mode_enabled = True
        child_pid, master_fd = pty.fork()
        if child_pid == 0:
            try:
                _apply_terminal_size(0, terminal_size)
                os.chdir(workdir)
                os.execvpe(command[0], command, os.environ.copy())
            except BaseException:
                os._exit(127)
        child = read_process_identity(child_pid)
        if child is None:
            os.kill(child_pid, signal.SIGTERM)
            os.waitpid(child_pid, 0)
            raise RuntimeError("foreground child identity is unverifiable")
        invocation.write_child(child, process_group_id=child_pid)
        invocation.write_heartbeat(wrapper, observed_at=_utc_now())

        with raw_output.open("wb") as output:
            while True:
                now = time.monotonic()
                if now >= next_heartbeat:
                    invocation.write_heartbeat(wrapper, observed_at=_utc_now())
                    next_heartbeat = now + heartbeat_interval

                read_fds = [master_fd]
                if input_open:
                    read_fds.append(stdin_fd)
                ready, _, _ = select.select(read_fds, [], [], 0.1)
                if master_fd in ready:
                    try:
                        data = os.read(master_fd, 65536)
                    except OSError:
                        data = b""
                    if data:
                        output.write(data)
                        output.flush()
                        _write_all(stdout_fd, data)
                if input_open and stdin_fd in ready:
                    try:
                        data = os.read(stdin_fd, 65536)
                    except OSError:
                        input_open = False
                    else:
                        if data:
                            _write_all(master_fd, data)
                        else:
                            input_open = False

                try:
                    waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
                except ChildProcessError as exc:
                    raise RuntimeError("foreground child exit status is unverifiable") from exc
                if waited_pid == child_pid:
                    while True:
                        try:
                            data = os.read(master_fd, 65536)
                        except OSError:
                            break
                        if not data:
                            break
                        output.write(data)
                        _write_all(stdout_fd, data)
                    exit_code = os.waitstatus_to_exitcode(status)
                    break

        raw_text = raw_output.read_text(encoding="utf-8", errors="replace")
        invocation.output_path.write_text(mask_secrets(raw_text), encoding="utf-8")
        invocation.write_result(child, exit_code=exit_code, started_at=started_at)
        return exit_code
    finally:
        if raw_mode_enabled and original_terminal is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_terminal)
        if master_fd >= 0:
            os.close(master_fd)
        if raw_output is not None:
            raw_output.unlink(missing_ok=True)


def launch_linux_terminal(invocation: ForegroundInvocation) -> int:
    """Open a visible GNOME Terminal for one wrapper invocation.

    The returned PID belongs to the terminal handoff process, not the wrapper.
    Callers must obtain wrapper identity only from a verified heartbeat.
    """
    if sys.platform != "linux":
        raise RuntimeError("foreground terminal launcher only supports Linux")
    terminal = shutil.which("gnome-terminal")
    if terminal is None:
        raise RuntimeError("foreground execution requires GNOME Terminal")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("foreground execution requires a GUI session")

    request = invocation.read_request()
    workdir = request.get("workdir")
    if not isinstance(workdir, str):
        raise RuntimeError("foreground request has invalid workdir")
    command = [
        terminal,
        "--window",
        "--title", f"Unison foreground {invocation.invocation_id}",
        "--working-directory", workdir,
        "--",
        sys.executable,
        "-m", "unison.foreground", "wrapper",
        "--invocation-dir", str(invocation.directory),
    ]
    return subprocess.Popen(command, start_new_session=True).pid


def main(argv: list[str] | None = None) -> int:
    """Run the internal foreground wrapper entrypoint."""
    parser = argparse.ArgumentParser(prog="python -m unison.foreground")
    subcommands = parser.add_subparsers(dest="command", required=True)
    wrapper = subcommands.add_parser("wrapper")
    wrapper.add_argument("--invocation-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command != "wrapper":
        parser.error("unsupported foreground command")

    directory = args.invocation_dir.resolve()
    request = atomic_read_json(directory / "request.json")
    if not isinstance(request, dict):
        parser.error("foreground invocation request is missing or invalid")
    invocation_id = request.get("invocation_id")
    if not isinstance(invocation_id, str) or not invocation_id:
        parser.error("foreground invocation request has invalid identity")
    return run_foreground_wrapper(
        ForegroundInvocation(invocation_id=invocation_id, directory=directory),
    )


if __name__ == "__main__":
    raise SystemExit(main())
