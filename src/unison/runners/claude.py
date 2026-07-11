"""ClaudeRunner — wraps `claude -p --dangerously-skip-permissions [--model MODEL] [--effort EFFORT] {prompt}`.

P12c: Supports cc-switch-cli for multi-provider model routing.  When a model maps
to a non-default provider, the command is wrapped with
``cc-switch start claude <provider> -- <native claude args>``.

P12c: Passes ``--effort`` flag when agent spec has ``reasoning_effort`` set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from unison.interfaces import AgentSpec
from unison.runners.base import BaseRunner


# ---------------------------------------------------------------------------
# Provider mapping — model name → cc-switch provider ID
# ---------------------------------------------------------------------------

# Built-in defaults.  Override via UNISON_CLAUDE_PROVIDER_MAP env var
# (JSON dict: '{"deepseek-v4-pro":"default","glm-5.2":"glm-zhipu"}').
_BUILTIN_PROVIDER_MAP: dict[str, str] = {
    "deepseek-v4-pro": "default",
    "glm-5.2": "glm-zhipu",
}

def _load_provider_map() -> dict[str, str]:
    """Merge built-in map with env var overrides."""
    env = os.environ.get("UNISON_CLAUDE_PROVIDER_MAP", "")
    if not env:
        return dict(_BUILTIN_PROVIDER_MAP)
    import json
    try:
        overrides = json.loads(env)
        merged = dict(_BUILTIN_PROVIDER_MAP)
        merged.update(overrides)
        return merged
    except json.JSONDecodeError:
        return dict(_BUILTIN_PROVIDER_MAP)


@dataclass
class ClaudeRunner(BaseRunner):
    """Claude CLI wrapper with optional cc-switch multi-provider routing."""

    binary: str = "claude"
    cc_switch_binary: str = "cc-switch"

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        cmd = [self.binary, *spec.cli_flags]
        if spec.model and spec.model != "default":
            cmd += ["--model", spec.model]
        # P12c: Pass reasoning effort when specified in agent spec
        if getattr(spec, "reasoning_effort", None):
            cmd += ["--effort", spec.reasoning_effort]

        # P12c: route through cc-switch if model maps to non-default provider
        provider_map = _load_provider_map()
        model = spec.model or ""
        if model in provider_map:
            provider_id = provider_map[model]
            if provider_id != "default":
                # Wrap: cc-switch start claude <provider> -- <native args>
                native_args = cmd[1:]  # drop "claude" binary
                if self.use_stdin:
                    return [
                        self.cc_switch_binary, "start", "claude", provider_id,
                        "--", *native_args,
                    ]
                else:
                    return [
                        self.cc_switch_binary, "start", "claude", provider_id,
                        "--", *native_args, prompt,
                    ]

        if not self.use_stdin:
            cmd.append(prompt)
        return cmd
