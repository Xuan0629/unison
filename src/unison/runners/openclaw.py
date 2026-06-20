"""OpenClawRunner — wraps OpenClaw gateway HTTP API for agent invocation.

The OpenClaw gateway runs at http://127.0.0.1:18789 and exposes an agent
invocation API. Unlike the other runners (which use subprocess), this
runner communicates via HTTP POST with JSON payloads.

If the gateway is unreachable or returns an error, the runner returns
a failed AgentResult with a descriptive error message.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path

from interfaces import AgentSpec, AgentResult

GATEWAY_URL = "http://127.0.0.1:18789"


@dataclass
class OpenClawRunner:
    """Invoke an OpenClaw agent via the gateway HTTP API.

    By default targets the agent-invoke endpoint. Falls back to a
    generic chat endpoint if agent-specific invocation fails.
    """

    gateway_url: str = GATEWAY_URL
    timeout: int = 600

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Not used (HTTP API, not subprocess). Included for interface compat."""
        return []

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Invoke the OpenClaw agent via HTTP POST with JSON payload.

        Args:
            spec: AgentSpec with role/runtime/model.
            prompt: Full prompt text to send.
            workdir: Working directory (not used for HTTP).
            timeout: Max seconds for the HTTP request.
            log_path: Path to write invocation log.

        Returns:
            AgentResult with success/error details.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)

        payload = json.dumps({
            "prompt": prompt,
            "model": spec.model,
            "role": spec.role,
        }).encode("utf-8")

        endpoints = [
            f"{self.gateway_url}/api/agent/invoke",
            f"{self.gateway_url}/api/chat",
            f"{self.gateway_url}/v1/chat",
        ]

        start = time.monotonic()
        last_error = None

        for endpoint in endpoints:
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=min(timeout, self.timeout)) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    duration = time.monotonic() - start
                    stdout, stderr = self._parse_response(raw)

                    log_path.write_text(
                        f"=== ENDPOINT ===\n{endpoint}\n\n"
                        f"=== PAYLOAD ===\n{prompt[:500]}...\n\n"
                        f"=== RESPONSE ===\n{raw}\n",
                        encoding="utf-8",
                    )

                    return AgentResult(
                        success=True,
                        exit_code=0,
                        duration=round(duration, 3),
                        stdout_tail=stdout[-500:] if stdout else "",
                        stderr_tail=stderr[-500:] if stderr else "",
                        log_path=log_path,
                        error=None,
                    )
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason} at {endpoint}"
            except urllib.error.URLError as e:
                last_error = f"Connection failed: {e.reason} at {endpoint}"
            except Exception as e:
                last_error = f"Error: {e} at {endpoint}"

        duration = time.monotonic() - start
        error_msg = last_error or "All endpoints failed"

        log_path.write_text(
            f"=== ERROR ===\n{error_msg}\n\n=== PROMPT ===\n{prompt[:500]}...\n",
            encoding="utf-8",
        )

        return AgentResult(
            success=False,
            exit_code=-1,
            duration=round(duration, 3),
            stdout_tail="",
            stderr_tail=error_msg,
            log_path=log_path,
            error=error_msg,
        )

    @staticmethod
    def _parse_response(raw: str) -> tuple[str, str]:
        """Extract stdout-like and stderr-like content from gateway response."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw, ""

        # Try common response formats
        if isinstance(data, dict):
            text = data.get("text") or data.get("content") or data.get("response") or ""
            if isinstance(text, list):
                text = "".join(
                    t.get("text", "") if isinstance(t, dict) else str(t)
                    for t in text
                )
            error = data.get("error") or ""
            if isinstance(error, dict):
                error = error.get("message", str(error))
            return str(text), str(error) if error else ""
        return raw, ""

    @staticmethod
    def _cli_flags(spec: AgentSpec) -> list[str]:
        """OpenClaw uses HTTP API — no CLI flags needed."""
        return []
