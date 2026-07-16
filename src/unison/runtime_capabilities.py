"""Immutable runtime metadata shared by validation, preflight, and presentation.

This registry intentionally contains only runnable built-in adapters.  Adding a
key here is a behavioral change: the loader will accept it and the CLI will
preflight it, so experimental runtimes must stay out until their adapter and
contract are verified.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


UsageProvenance = Literal["actual", "estimated", "unavailable"]
PromptTransport = Literal["argv", "stdin", "http"]


@dataclass(frozen=True)
class RuntimeCapability:
    """Declarative constraints for one supported runtime."""

    runtime_key: str
    executable: str | None
    prompt_transport: PromptTransport
    preserves_interactive_tty: bool
    supports_model_override: bool
    supports_reasoning_effort: bool
    supports_streaming: bool
    structured_result: bool
    usage_provenance: UsageProvenance
    supports_session_resume: bool
    safe_execution_modes: frozenset[str]
    max_concurrency: int | None
    cli_flags: tuple[str, ...] = ()


_CAPABILITIES: tuple[RuntimeCapability, ...] = (
    RuntimeCapability(
        runtime_key="claude",
        executable="claude",
        prompt_transport="argv",
        preserves_interactive_tty=True,
        supports_model_override=True,
        supports_reasoning_effort=True,
        supports_streaming=True,
        structured_result=False,
        usage_provenance="estimated",
        supports_session_resume=False,
        safe_execution_modes=frozenset({"headless_bypass", "foreground_manual"}),
        max_concurrency=None,
        cli_flags=("-p", "--dangerously-skip-permissions"),
    ),
    RuntimeCapability(
        runtime_key="codex",
        executable="codex",
        prompt_transport="argv",
        preserves_interactive_tty=True,
        supports_model_override=True,
        supports_reasoning_effort=True,
        supports_streaming=True,
        structured_result=False,
        usage_provenance="estimated",
        supports_session_resume=False,
        safe_execution_modes=frozenset({"headless_bypass", "foreground_manual"}),
        max_concurrency=None,
        cli_flags=("exec", "--dangerously-bypass-approvals-and-sandbox"),
    ),
    RuntimeCapability(
        runtime_key="hermes",
        executable="hermes",
        prompt_transport="argv",
        preserves_interactive_tty=False,
        supports_model_override=False,
        supports_reasoning_effort=False,
        supports_streaming=False,
        structured_result=False,
        usage_provenance="estimated",
        supports_session_resume=False,
        safe_execution_modes=frozenset({"headless_bypass"}),
        max_concurrency=None,
        cli_flags=("chat", "--yolo", "-q"),
    ),
    RuntimeCapability(
        runtime_key="openclaw",
        executable=None,
        prompt_transport="argv",
        preserves_interactive_tty=False,
        supports_model_override=True,
        supports_reasoning_effort=False,
        supports_streaming=True,
        structured_result=True,
        usage_provenance="actual",
        supports_session_resume=False,
        safe_execution_modes=frozenset({"headless_bypass"}),
        max_concurrency=None,
    ),
)

BUILTIN_RUNTIME_KEYS: tuple[str, ...] = tuple(
    capability.runtime_key for capability in _CAPABILITIES
)
_RUNTIME_CAPABILITIES: dict[str, RuntimeCapability] = {
    capability.runtime_key: capability for capability in _CAPABILITIES
}


def list_runtime_capabilities() -> tuple[RuntimeCapability, ...]:
    """Return the immutable, deterministic built-in capability list."""
    return _CAPABILITIES


def get_runtime_capability(runtime_key: str) -> RuntimeCapability:
    """Return a registered runtime or fail closed for unknown keys."""
    try:
        return _RUNTIME_CAPABILITIES[runtime_key]
    except KeyError as error:
        raise KeyError(f"unknown runtime: {runtime_key}") from error


def is_registered_runtime(runtime_key: str) -> bool:
    return runtime_key in _RUNTIME_CAPABILITIES
