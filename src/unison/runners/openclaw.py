"""OpenClawRunner — invokes agents via the OpenClaw gateway.

Uses ``openclaw agent`` CLI with unique session keys for isolated,
concurrent agent turns. The CLI internally communicates with the
OpenClaw gateway (http://127.0.0.1:18789) — no direct HTTP needed.

Architecture:
    openclaw agent --agent <id> --session-key agent:<id>:unison-<role>-<uuid>
                   --model <provider/model> --message <prompt> --json
                   --timeout <seconds>

The ``--json`` flag emits structured stdout that is parsed to extract
the agent's response text, model metadata, and token usage.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from unison.interfaces import AgentSpec, AgentResult
from unison.runners.base import BaseRunner


@dataclass
class OpenClawRunner(BaseRunner):
    """Invoke an OpenClaw agent via ``openclaw agent`` CLI.

    Each invocation gets a unique session key so multiple agents can
    run concurrently without colliding.  The CLI streams output to
    stdout (JSON when ``--json`` is passed) which is captured by the
    BaseRunner subprocess machinery.

    Configurable fields:

    * **binary** — path to the ``openclaw`` CLI (default ``"openclaw"``).
    * **agent_id** — OpenClaw agent id (default ``"main"``).
    * **gateway_url** — informational; the CLI resolves its own gateway
      address from ``~/.openclaw/openclaw.json``.
    """

    binary: str = "openclaw"
    agent_id: str = "main"
    gateway_url: str = field(default="http://127.0.0.1:18789", repr=False)

    # ------------------------------------------------------------------
    # _build_command
    # ------------------------------------------------------------------

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the ``openclaw agent`` command line.

        Format::

            openclaw agent --agent <agent_id>
                           --session-key agent:<agent_id>:unison-<role>-<uuid>
                           [--model <model>]
                           --message <prompt>
                           --json
        """
        session_id = f"unison-{spec.role}-{uuid.uuid4().hex[:8]}"
        cmd = [
            self.binary,
            "agent",
            "--agent", self.agent_id,
            "--session-key", f"agent:{self.agent_id}:{session_id}",
            "--json",
        ]

        # Pass model override when specified
        if spec.model and spec.model != "default":
            cmd += ["--model", spec.model]

        cmd += ["--message", prompt]
        return cmd

    # ------------------------------------------------------------------
    # _effective_timeout — CLI handles its own timeout
    # ------------------------------------------------------------------

    def _effective_timeout(self, base_timeout: int) -> int:
        """The ``openclaw agent`` CLI respects its own ``--timeout`` flag.

        We add a small grace period (30 s) on top of *base_timeout* so
        the CLI's internal timeout fires before our outer one kills the
        process.
        """
        return base_timeout + 30

    # ------------------------------------------------------------------
    # response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_response(raw_stdout: str) -> dict | None:
        """Extract structured data from the CLI's JSON output.

        The ``--json`` flag produces one or more JSON objects on stdout.
        We look for the first object that contains a ``"payloads"`` key
        (the final agent response).

        Returns:
            Parsed response dict, or *None* if no valid JSON found.
        """
        # The JSON output may span multiple lines; try to find the
        # largest valid JSON object.
        candidates = []
        for i, ch in enumerate(raw_stdout):
            if ch == "{":
                depth = 0
                for j in range(i, len(raw_stdout)):
                    c = raw_stdout[j]
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            candidates.append(raw_stdout[i:j + 1])
                            break
        # Try parsing each candidate, preferring those with "payloads"
        for cand in reversed(candidates):
            try:
                obj = json.loads(cand)
                if isinstance(obj, dict) and "payloads" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
        # Fallback: return the last successfully parsed object
        for cand in reversed(candidates):
            try:
                obj = json.loads(cand)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def extract_text(response: dict | None) -> str:
        """Extract the agent's text reply from a parsed response dict.

        Expects ``{"payloads": [{"text": "..."}]}`` structure.
        """
        if not response:
            return ""
        payloads = response.get("payloads", [])
        texts = []
        for p in payloads:
            if isinstance(p, dict):
                t = p.get("text", "")
                if t:
                    texts.append(t)
        return "\n".join(texts)

    # ------------------------------------------------------------------
    # error reporting
    # ------------------------------------------------------------------

    def _not_found_error_message(self) -> str:
        return (
            f"{self.binary} binary not found. "
            f"Install OpenClaw or adjust the 'binary' field."
        )
