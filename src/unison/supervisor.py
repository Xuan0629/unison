"""supervisor.py — Crash recovery with env snapshot, classification, and bounded retry.

Provides three cooperating classes:

  ManifestWriter  — atomic JSON manifest writer (temp-file + rename); used
                     by SupervisedRunner instead of raw json.dump().
  EnvSnapshot     — point-in-time capture of pip freeze, git status/diff,
                     and environment variables with secrets redacted.
  CrashClassifier — classifies AgentResult failures; UNSAFE patterns
                     (traceback in our code) are checked **before** SAFE
                     patterns (timeout, rate-limit, API error).
  SupervisedRunner — wraps an AgentRunner, takes an EnvSnapshot before
                     every restart, writes crash records via ManifestWriter,
                     and enforces bounded attempts.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unison.interfaces import AgentResult, AgentSpec, AgentRunner

# Re-use the secret-masking engine from the runner base
from unison.runners.base import mask_secrets


# ============================================================================
# _atomic_write_json — temp-file + rename, shared by ManifestWriter & snapshots
# ============================================================================


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write *data* atomically to *path* via temp-file + ``os.rename``.

    Every writer that persists JSON to disk MUST use this helper so that
    readers never see a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    os.rename(tmp, path)


# ============================================================================
# ManifestWriter — atomic JSON manifest, no raw json.dump in app code
# ============================================================================


@dataclass
class ManifestWriter:
    """Atomic JSON manifest writer for crash / supervision records.

    Every write goes through ``_atomic_write_json`` so a reader never
    sees a half-written file.
    """

    path: Path

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write *data* atomically to ``self.path``."""
        _atomic_write_json(self.path, data)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Read the current manifest, returning ``{}`` when absent."""
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: dict[str, Any]) -> None:
        """Overwrite the manifest with *data*."""
        self._atomic_write(data)

    def append_record(self, key: str, record: dict[str, Any]) -> None:
        """Read-modify-write: insert *record* under *key*."""
        manifest = self.read()
        manifest[key] = record
        self._atomic_write(manifest)

    def update_record(self, key: str, updates: dict[str, Any]) -> None:
        """Read-modify-write: merge *updates* into *key*."""
        manifest = self.read()
        record = manifest.get(key, {})
        record.update(updates)
        manifest[key] = record
        self._atomic_write(manifest)

    def records_for(self, key_prefix: str) -> list[dict[str, Any]]:
        """Return all records whose key starts with *key_prefix*, newest first."""
        manifest = self.read()
        matching = [
            (k, v) for k, v in manifest.items() if k.startswith(key_prefix)
        ]
        matching.sort(key=lambda kv: kv[0], reverse=True)
        return [v for _, v in matching]


# ============================================================================
# EnvSnapshot — point-in-time capture (secrets redacted)
# ============================================================================


@dataclass
class EnvSnapshot:
    """Point-in-time capture of the execution environment.

    Captures:
      - pip freeze
      - git status / git diff / recent git log
      - environment variables (values redacted for API keys / secrets)
      - UTC timestamp
    """

    timestamp: str
    pip_freeze: str
    git_status: str
    git_diff: str
    git_log: str
    env_vars: dict[str, str]

    @classmethod
    def capture(cls, cwd: Path | None = None) -> "EnvSnapshot":
        """Take a snapshot of the current environment.

        Every command failure is non-fatal — the corresponding field
        is set to an empty string.
        """
        workdir = Path(cwd) if cwd else Path.cwd()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return cls(
            timestamp=timestamp,
            pip_freeze=cls._run(["pip", "freeze"], workdir),
            git_status=cls._run(["git", "status"], workdir),
            git_diff=cls._run(["git", "diff"], workdir),
            git_log=cls._run(["git", "log", "-5", "--oneline"], workdir),
            env_vars=cls._capture_env(),
        )

    @staticmethod
    def _run(cmd: list[str], cwd: Path) -> str:
        """Run *cmd*, return stdout or ``""`` on failure."""
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=15,
            )
            return mask_secrets(proc.stdout + "\n" + proc.stderr).strip()
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _capture_env() -> dict[str, str]:
        """Return a copy of ``os.environ`` with secret values redacted."""
        env: dict[str, str] = {}
        for name, val in os.environ.items():
            # Redact the value through mask_secrets so any API-key-like
            # value is replaced with [REDACTED].
            env[name] = mask_secrets(val)
        return env

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "timestamp": self.timestamp,
            "pip_freeze": self.pip_freeze,
            "git_status": self.git_status,
            "git_diff": self.git_diff,
            "git_log": self.git_log,
            "env_vars": self.env_vars,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvSnapshot":
        """Deserialize from a dict (e.g. read from manifest)."""
        return cls(
            timestamp=d.get("timestamp", ""),
            pip_freeze=d.get("pip_freeze", ""),
            git_status=d.get("git_status", ""),
            git_diff=d.get("git_diff", ""),
            git_log=d.get("git_log", ""),
            env_vars=d.get("env_vars", {}),
        )


# ============================================================================
# CrashClassifier — UNSAFE patterns checked BEFORE SAFE patterns
# ============================================================================


# Patterns that indicate a code bug — these MUST be checked first so
# a traceback in ``src/unison/`` is never masked by a "timeout" keyword.
_UNISON_PREFIX = "src/unison/"

# SAFE patterns — transient errors that are safe to retry.
_MODEL_ERROR_KW = (
    "rate limit", "rate_limit", "too many requests",
    "api error", "api_error",
    "unauthorized", "authentication", "invalid api key",
    "model not found", "model_not_found",
    "overloaded", "service unavailable",
    "context length", "context_length", "maximum context",
)


class CrashClassifier:
    """Classify an AgentResult failure for crash-recovery routing.

    **Ordering contract**: UNSAFE patterns (traceback in ``src/unison/``,
    ``AssertionError``, ``ImportError`` in our code) are checked **before**
    SAFE patterns (timeout, rate-limit, API errors).  A timeout that
    occurs inside our own timeout-handling code is still a bug.

    Return values:
      ``UNISON_BUG``   — traceback in ``src/unison/`` (do NOT retry).
      ``CONSUMER_BUG`` — traceback in ``src/`` (not our code).
      ``TIMEOUT``      — transient timeout (safe to retry).
      ``MODEL_ERROR``   — rate-limit, auth, overloaded, … (safe to retry).
      ``UNKNOWN``       — could not classify.
    """

    @staticmethod
    def classify(result: AgentResult) -> str:
        """Classify *result*.

        Note: this is a standalone classifier.  The orchestrator's
        self-heal path uses :class:`unison.self_heal.ErrorClassifier`
        which was fixed in the same pass to also check UNSAFE first.
        """
        err = (result.error or "").lower()
        tail = (result.stderr_tail or "").lower()

        # ── 1. UNSAFE — read agent log for tracebacks ──────────────────────
        if result.log_path and Path(result.log_path).exists():
            log_content = Path(result.log_path).read_text(errors="replace")
            if _UNISON_PREFIX in log_content:
                return "UNISON_BUG"
            if "src/" in log_content:
                return "CONSUMER_BUG"

        # ── 2. UNSAFE — stderr tracebacks ──────────────────────────────────
        if result.stderr_tail:
            if _UNISON_PREFIX in tail:
                return "UNISON_BUG"
            if "traceback" in tail and "src/" in tail:
                return "CONSUMER_BUG"

        # ── 3. SAFE — timeout ─────────────────────────────────────────────
        if "timeout" in err:
            return "TIMEOUT"

        # ── 4. SAFE — model / API errors ──────────────────────────────────
        combined_error = f"{err}\n{tail}" if result.stderr_tail else err
        for kw in _MODEL_ERROR_KW:
            if kw in combined_error:
                return "MODEL_ERROR"

        # ── 5. UNSAFE — explicit consumer traceback without src path ──────
        if result.stderr_tail and "traceback" in tail:
            return "CONSUMER_BUG"

        return "UNKNOWN"

    @staticmethod
    def is_retryable(error_type: str) -> bool:
        """Return True when *error_type* is safe to retry."""
        return error_type in ("TIMEOUT", "MODEL_ERROR", "UNKNOWN")


# ============================================================================
# SupervisedRunner — bounded retry with env snapshot + manifest recording
# ============================================================================


@dataclass
class SupervisedRunner:
    """Wraps an :class:`AgentRunner` with crash supervision.

    Before each execution:
      1. Takes an :class:`EnvSnapshot` (pip / git / env).
      2. Runs the wrapped runner.
      3. On failure, classifies via :class:`CrashClassifier`.
      4. Records the attempt in a manifest via :class:`ManifestWriter`.
      5. Retries up to *max_attempts* times for SAFE errors.
      6. Halts immediately on UNSAFE errors (no retry).

    Attributes:
        runner: The underlying agent runner to wrap.
        manifest: ManifestWriter for crash/supervision records.
        max_attempts: Maximum total attempts (1 = no retry).
        snapshot_dir: Where per-attempt env snapshots are stored
                      (default: ``<manifest_dir>/snapshots/``).
    """

    runner: AgentRunner
    manifest: ManifestWriter
    max_attempts: int = 3
    snapshot_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.snapshot_dir is None:
            self.snapshot_dir = self.manifest.path.parent / "snapshots"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute *spec* under supervision, retrying on SAFE failures.

        Returns the last :class:`AgentResult` (successful or final failure).
        """
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

        if self.max_attempts <= 0:
            raise ValueError(
                f"max_attempts must be >= 1, got {self.max_attempts}"
            )

        for attempt in range(1, self.max_attempts + 1):
            # ── env snapshot before every attempt ─────────────────────────
            snapshot = EnvSnapshot.capture(workdir)
            self._save_snapshot(f"{session_id}-{attempt}", snapshot)

            # ── run the wrapped agent ─────────────────────────────────────
            result = self.runner.run(spec, prompt, workdir, timeout, log_path)

            if result.success:
                self._record_attempt(
                    session_id, attempt, "success",
                    snapshot=snapshot, result=result,
                )
                return result

            # ── classify the failure ──────────────────────────────────────
            error_type = CrashClassifier.classify(result)
            retryable = CrashClassifier.is_retryable(error_type)

            self._record_attempt(
                session_id, attempt, error_type,
                snapshot=snapshot, result=result,
                retryable=retryable,
            )

            # UNSAFE → no retry
            if not retryable:
                return result

            # SAFE + attempts remaining → loop
            # (last attempt falls through and returns the final result)

        return result

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _save_snapshot(self, label: str, snapshot: EnvSnapshot) -> None:
        """Persist *snapshot* atomically to ``snapshot_dir/<label>.json``."""
        snap_path = self.snapshot_dir / f"{label}.json"
        _atomic_write_json(snap_path, snapshot.to_dict())

    def _record_attempt(
        self,
        session_id: str,
        attempt: int,
        error_type: str,
        *,
        snapshot: EnvSnapshot,
        result: AgentResult,
        retryable: bool | None = None,
    ) -> None:
        """Append an attempt record to the supervision manifest."""
        record: dict[str, Any] = {
            "session_id": session_id,
            "attempt": attempt,
            "error_type": error_type,
            "timestamp": snapshot.timestamp,
            "exit_code": result.exit_code,
            "duration": result.duration,
            "error": result.error,
            "log_path": str(result.log_path),
        }
        if retryable is not None:
            record["retryable"] = retryable

        key = f"{session_id}-{attempt}"
        self.manifest.append_record(key, record)

    # ------------------------------------------------------------------
    # inspection helpers
    # ------------------------------------------------------------------

    def last_attempt(self, session_id: str) -> dict[str, Any] | None:
        """Return the most recent attempt record for *session_id*."""
        records = self.manifest.records_for(session_id)
        return records[0] if records else None

    def session_summary(self, session_id: str) -> dict[str, Any]:
        """Return a summary of *session_id* (attempts, final outcome)."""
        records = self.manifest.records_for(session_id)
        if not records:
            return {"session_id": session_id, "attempts": 0}
        return {
            "session_id": session_id,
            "attempts": len(records),
            "final_error_type": records[0]["error_type"],
            "success": any(r.get("error_type") == "success" for r in records),
        }
