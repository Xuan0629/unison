"""CrushRunner — constrained headless adapter for the official Crush CLI."""
from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from unison.interfaces import AgentResult, AgentSpec
from unison.runners.base import BaseRunner, ProcessHandle
from unison.usage import UsageRecord


@dataclass
class CrushRunner(BaseRunner):
    """Run one isolated Crush session without reusing global session state."""

    binary: str = "crush"

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        raise RuntimeError("CrushRunner requires an invocation-specific state directory")

    def _new_state_dir(self, log_path: Path) -> Path:
        return log_path.parent / ".crush-state" / log_path.stem

    def _build_command_for_state(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        log_path: Path,
        state_dir: Path,
    ) -> list[str]:
        del log_path
        cmd = [
            self.binary,
            "run",
            "--quiet",
            "--cwd",
            str(workdir),
            "--data-dir",
            str(state_dir),
        ]
        if spec.model and spec.model != "default":
            cmd.extend(["--model", spec.model])
        cmd.append(prompt)
        return cmd

    @staticmethod
    def extract_usage(meta: Any) -> UsageRecord:
        """Accept only a complete Crush token breakdown; local cost is not billing."""
        if not isinstance(meta, dict):
            return UsageRecord.unavailable()
        values = (
            meta.get("prompt_tokens"),
            meta.get("completion_tokens"),
            meta.get("cache_read_tokens"),
            meta.get("total_tokens"),
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            return UsageRecord.unavailable()
        try:
            return UsageRecord(
                token_provenance="actual",
                cost_provenance="unavailable",
                input_tokens=values[0],
                output_tokens=values[1],
                cache_read_tokens=values[2],
                total_tokens=values[3],
            )
        except ValueError:
            return UsageRecord.unavailable()

    def _session_meta(self, workdir: Path, state_dir: Path) -> tuple[str, dict[str, Any]] | None:
        common = ["--cwd", str(workdir), "--data-dir", str(state_dir), "--json"]
        try:
            listed = subprocess.run(
                [self.binary, "session", "list", *common],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            sessions = json.loads(listed.stdout)
            if not isinstance(sessions, list) or len(sessions) != 1:
                return None
            session_id = sessions[0].get("uuid") if isinstance(sessions[0], dict) else None
            if not isinstance(session_id, str) or not session_id:
                return None
            shown = subprocess.run(
                [self.binary, "session", "show", session_id, *common],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            detail = json.loads(shown.stdout)
            meta = detail.get("meta") if isinstance(detail, dict) else None
            return (session_id, meta) if isinstance(meta, dict) else None
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return None

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
        *,
        on_started: Callable[[ProcessHandle], None] | None = None,
    ) -> AgentResult:
        state_dir = self._new_state_dir(log_path)
        state_dir.mkdir(parents=True, exist_ok=False)
        command = self._build_command_for_state(spec, prompt, workdir, log_path, state_dir)
        if on_started is None:
            result = self._run_command(command, prompt, workdir, timeout, log_path)
        else:
            result = self._run_command(
                command,
                prompt,
                workdir,
                timeout,
                log_path,
                on_started=on_started,
            )
        if not result.success:
            return result
        session = self._session_meta(workdir, state_dir)
        if session is None:
            return result
        session_id, meta = session
        (state_dir / "unison-session.json").write_text(
            json.dumps({"session_uuid": session_id}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return AgentResult(
            success=result.success,
            exit_code=result.exit_code,
            duration=result.duration,
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
            log_path=result.log_path,
            commit=result.commit,
            verdict=result.verdict,
            error=result.error,
            usage=self.extract_usage(meta),
        )

    def _wait_after_interrupt(self, proc: subprocess.Popen[str]) -> bool:
        try:
            proc.wait(timeout=5)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _terminate_on_timeout(self, proc: subprocess.Popen[str], timeout: int) -> None:
        del timeout
        try:
            process_group = os.getpgid(proc.pid)
            os.killpg(process_group, signal.SIGINT)
            if self._wait_after_interrupt(proc):
                return
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()

    def _not_found_error_message(self) -> str:
        return f"{self.binary} binary not found. Install official Crush or adjust the binary path."
