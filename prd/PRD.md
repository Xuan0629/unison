# Unison Plugin System — Design & Specification

**Status:** Draft, Phase 10 Round 2
**Author:** Architect (Claude Code)
**Target:** Unison v2.1

---

## 1. Motivation

Unison currently hardcodes four agent runtimes via a `Literal` type alias:

```python
# interfaces.py:38 — the bottleneck
Runtime: TypeAlias = Literal["claude", "codex", "hermes", "openclaw"]
```

Adding a new agent CLI (Gemini CLI, GitHub Copilot, a custom bash script wrapping an
in-house model, etc.) requires editing 5 source files. This is a **closed set**. The
plugin system makes it **open** — users declare a new runtime with a 6-line YAML
stanza, no source edits needed.

### Design Goals (ranked)

1. **Safe by default.** A declarative YAML-only path covers 90% of use cases
   (gemini, copilot, custom bash wrappers) without writing or importing Python.
2. **Extensible for power users.** An opt-in Python plugin API supports non-CLI
   backends (HTTP APIs, gRPC, WebSocket agents).
3. **Pre-flight validation.** All plugin errors surface before any agent runs.
4. **Mechanical backward compatibility.** Existing `pipeline.yaml` files load
   identically. Existing runners are untouched.

---

## 2. Two-Tier Plugin Architecture

The design splits into two tiers with a hard trust boundary between them.

### Tier 1: Declarative CLI Plugins (Always Available)

A `cli_plugins:` YAML section where the user declares `binary`, `args`, `env`, and
`timeout_grace`. No Python code is loaded — the orchestrator wraps the binary via
`subprocess.run` directly using a built-in `CLIPluginRunner`.

**This is the recommended path for all CLI-based runtimes** (Gemini, Copilot,
custom bash scripts, etc.). It covers the common case with zero trust risk.

### Tier 2: Python Plugin API (Opt-in via `--allow-python-plugins`)

A `UnisonPlugin` ABC in `src/unison/plugins/base.py`. Users write a Python class,
drop it in `~/.unison/plugins/`, and declare it in `python_plugins:`. Loading
Python plugins requires an explicit CLI flag. This gate prevents a pipeline from
an untrusted repo from executing arbitrary Python during validation.

---

## 3. Plugin Interface

### 3.1 Declarative CLI Plugin (YAML Schema)

```yaml
# pipeline.yaml
version: "2.1"
project_root: "."

# ── Declarative CLI plugins (Tier 1, always safe) ─────────────────────────
cli_plugins:
  gemini:
    binary: gemini-internal
    args: ["-p", "--output-format", "text", "-y"]
    env:
      GEMINI_API_KEY: "${GEMINI_API_KEY}"
      GEMINI_MODEL: "gemini-2.5-pro"
    timeout_grace: 10

  custom-script:
    binary: /opt/scripts/my-agent.sh
    args: ["--model", "ft-v3", "--format", "markdown"]
    env:
      API_KEY: "${MY_API_KEY}"
    timeout_grace: 2

# ── Python plugins (Tier 2, requires --allow-python-plugins) ──────────────
python_plugins:
  # Defined here but NOT loaded unless CLI flag is set
  openclaw-v2:
    module: openclaw_runner
    class: OpenClawV2Plugin
    search_paths:
      - ~/.unison/plugins
    kwargs:
      endpoint: "https://api.internal/openclaw/v2"
      pool_size: 4

# ── Agents (unchanged) ────────────────────────────────────────────────────
agents:
  planner:
    role: architect
    runtime: claude                    # built-in: unchanged
    model: opus-4.8
    system_prompt_path: "prompts/architect.md"
    pipeline_role: planner

  coder:
    role: developer
    runtime: gemini                    # <-- declarative CLI plugin
    model: gemini-2.5-pro
    system_prompt_path: "prompts/developer.md"
    pipeline_role: developer

  reviewer:
    role: reviewer
    runtime: custom-script             # <-- another declarative plugin
    model: ""
    system_prompt_path: "prompts/reviewer.md"
    pipeline_role: reviewer
```

### 3.2 Python Plugin API (Opt-in Tier 2)

```python
# src/unison/plugins/base.py
from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from interfaces import AgentSpec, AgentResult
from unison.runners.base import AgentRunner


def _safe_tail(data: str | bytes | None, n: int = 500) -> str:
    """Normalize str|bytes|None to a UTF-8 string tail."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")[-n:]
    return data[-n:]


class UnisonPlugin(AgentRunner, ABC):
    """Abstract base for user-provided agent plugins (Tier 2 — opt-in).

    Only loaded when the user passes ``--allow-python-plugins``. Plugins
    execute **in the orchestrator process** — this is a trust boundary
    that the user must explicitly accept.

    Subclasses MUST override:
      - ``binary``: the CLI executable name (str)
      - ``cli_flags``: default CLI flags (list[str])
      - ``validate_installation``: check that the binary is callable

    Subclasses MAY override:
      - ``startup_grace``: extra seconds added to timeout (default 0)
      - ``run``: for non-subprocess backends (HTTP, gRPC, etc.)
      - ``_build_command``: for custom argument ordering
      - ``_env``: extra environment variables injected at runtime
    """

    # ---- Subclass contract ------------------------------------------------

    binary: ClassVar[str]
    """The CLI binary name, e.g. ``"gemini-internal"``."""

    cli_flags: ClassVar[list[str]]
    """Default flags appended before the prompt."""

    startup_grace: ClassVar[int] = 0
    """Extra seconds added to agent timeout for slow-starting CLIs."""

    _env: ClassVar[dict[str, str]] = {}
    """Extra env vars injected into the subprocess environment."""

    @classmethod
    @abstractmethod
    def validate_installation(cls) -> tuple[bool, str | None]:
        """Check that the binary is installed and callable.

        Returns:
            ``(True, None)`` if ready.
            ``(False, error_message)`` if missing, broken, or wrong version.
        """
        ...

    # ---- Default implementation -------------------------------------------

    def _build_command(self, spec: AgentSpec, prompt: str) -> list[str]:
        """Build the CLI command.

        Default: ``[binary, *cli_flags, prompt]``. Override for CLIs
        that need flags after the prompt.
        """
        return [self.binary, *self.cli_flags, prompt]

    def _build_env(self) -> dict[str, str]:
        """Return the merged environment for the subprocess.

        Starts from ``os.environ``, then overlays ``self._env``.
        Override for custom environment assembly.
        """
        env = os.environ.copy()
        env.update(self._env)
        return env

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Execute the agent via subprocess.run.

        Mirrors ClaudeRunner.run exactly — same timeout handling,
        same log format, same AgentResult shape.
        """
        cmd = self._build_command(spec, prompt)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        effective_timeout = timeout + self.startup_grace
        env = self._build_env()

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                env=env,
            )
            duration = time.monotonic() - start
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            success = proc.returncode == 0
            error = (
                None
                if success
                else f"Command exited with code {proc.returncode}"
            )

        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - start
            success = False
            stdout = (
                e.stdout.decode("utf-8", errors="replace")
                if isinstance(e.stdout, bytes)
                else (e.stdout or "")
            )
            stderr = (
                e.stderr.decode("utf-8", errors="replace")
                if isinstance(e.stderr, bytes)
                else (e.stderr or "")
            )
            error = f"Timeout after {effective_timeout}s"

        except FileNotFoundError:
            duration = time.monotonic() - start
            success = False
            stdout = ""
            stderr = ""
            error = f"{self.binary} binary not found"

        except Exception as exc:
            duration = time.monotonic() - start
            success = False
            stdout = ""
            stderr = ""
            error = f"Unexpected error: {exc}"

        # Write log — same format as ClaudeRunner
        log_path.write_text(
            f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )

        return AgentResult(
            success=success,
            exit_code=proc.returncode if "proc" in dir() else -1,
            duration=round(duration, 3),
            stdout_tail=_safe_tail(stdout, 500),
            stderr_tail=_safe_tail(stderr, 500),
            log_path=log_path,
            error=error,
        )
```

### 3.3 Concrete Python Plugin Example — Gemini CLI

```python
# ~/.unison/plugins/gemini_cli_plugin.py
import subprocess

from unison.plugins.base import UnisonPlugin


class GeminiCLIPlugin(UnisonPlugin):
    """Plugin for the `gemini-internal` CLI tool."""

    binary = "gemini-internal"
    cli_flags = ["-p", "--output-format", "text", "-y"]
    startup_grace = 10  # Gemini CLI slow cold start
    _env = {
        "GEMINI_OUTPUT_STYLE": "compact",
    }

    @classmethod
    def validate_installation(cls) -> tuple[bool, str | None]:
        try:
            result = subprocess.run(
                [cls.binary, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False, (
                    f"{cls.binary} --version exited {result.returncode}"
                )
            return True, None
        except FileNotFoundError:
            return False, f"{cls.binary} not found on PATH"
        except subprocess.TimeoutExpired:
            return False, f"{cls.binary} --version timed out"
```

### 3.4 Built-in CLIPluginRunner (Tier 1 Internal)

```python
# src/unison/plugins/cli_runner.py
"""Internal runner that drives declarative CLI plugins (Tier 1).

This is NOT a user-facing class — it's instantiated by PipelineLoader
for each entry in ``cli_plugins:``. It wraps the user's binary + args
in the same subprocess.run pattern as ClaudeRunner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from unison.plugins.base import UnisonPlugin


@dataclass
class CLIPluginRunner(UnisonPlugin):
    """Wraps a user-declared binary with args and env.

    Instantiated programmatically — users don't subclass this.
    They declare ``binary``, ``args``, ``env`` in ``cli_plugins:``.
    """

    binary: str = ""
    cli_flags: list[str] = field(default_factory=list)
    startup_grace: int = 0
    _env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def validate_installation(cls) -> tuple[bool, str | None]:
        # CLIPluginRunner instances are validated at construction time
        # by PluginRegistry._validate_cli_binary()
        return True, None
```

---

## 4. Plugin Loading Mechanism

### 4.1 Single Owner: PipelineLoader

**Critical design rule:** `PipelineLoader` owns all plugin discovery. The
`Orchestrator` consumes the already-validated registry from `PipelineSpec`.
No duplicated loading, no ambiguity about when validation happens.

```
pipeline.yaml parsed by PipelineLoader.load()
    │
    ├─ 1. Parse YAML via yaml.safe_load()
    ├─ 2. Schema migration (existing)
    ├─ 3. Parse cli_plugins: → PluginRegistry with CLIPluginRunner instances
    ├─ 4. Parse python_plugins: → only if --allow-python-plugins
    │       ├─ Resolve search_paths → find <module>.py
    │       ├─ importlib to load module with unique sys.modules key
    │       ├─ issubclass check against UnisonPlugin
    │       ├─ validate_installation() → (ok, err)
    │       └─ Instantiate → store in registry
    ├─ 5. Resolve valid runtimes = BUILTIN_RUNTIMES ∪ registry.names()
    ├─ 6. _build_agents() validates each agent.runtime ∈ valid
    ├─ 7. PipelineSpec stores the validated PluginRegistry
    │
    └─ Orchestrator.__init__()
           └─ self._plugin_registry = spec.plugin_registry  # consume, don't reload
```

### 4.2 PluginRegistry Implementation

```python
# src/unison/plugins/registry.py
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from unison.plugins.base import UnisonPlugin
from unison.plugins.cli_runner import CLIPluginRunner


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded.

    All plugin errors surface during PipelineLoader.load(), before any
    agent runs. The message is human-readable for CLI output.
    """

    def __init__(self, runtime_name: str, reason: str):
        self.runtime_name = runtime_name
        self.reason = reason
        super().__init__(f"Plugin '{runtime_name}': {reason}")


class PluginRegistry:
    """Holds loaded plugin instances, keyed by runtime name.

    Built-in runtimes (claude, codex, hermes, openclaw) are NOT stored
    here — they live in Orchestrator._runners. The registry only holds
    user-declared plugins.

    Constructed by PipelineLoader during ``load()`` and stored on
    ``PipelineSpec.plugin_registry``. Orchestrator reads it, never
    reloads.
    """

    BUILTIN_RUNTIMES: frozenset[str] = frozenset({
        "claude", "codex", "hermes", "openclaw"
    })

    def __init__(self, allow_python: bool = False) -> None:
        self._plugins: dict[str, UnisonPlugin] = {}
        self._allow_python = allow_python

    # ---- Tier 1: Declarative CLI plugins ----------------------------------

    def load_cli_plugins(
        self, raw: dict[str, Any] | None
    ) -> None:
        """Load declarative CLI plugins (always safe, no Python import).

        Args:
            raw: The parsed ``cli_plugins:`` dict from pipeline.yaml,
                 or None if the key is absent.

        Raises:
            PluginLoadError: On any validation failure.
        """
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise PluginLoadError("<cli_plugins>", "must be a mapping")

        for runtime_name, cfg in raw.items():
            if not isinstance(cfg, dict):
                raise PluginLoadError(
                    runtime_name, "definition must be a mapping"
                )
            if runtime_name in self.BUILTIN_RUNTIMES:
                raise PluginLoadError(
                    runtime_name,
                    f"'{runtime_name}' is a built-in runtime. "
                    f"Cannot override. Use a different name."
                )
            if runtime_name in self._plugins:
                raise PluginLoadError(
                    runtime_name,
                    f"Duplicate runtime name. Each plugin must have "
                    f"a unique name."
                )

            binary = cfg.get("binary", "")
            if not binary:
                raise PluginLoadError(
                    runtime_name, "missing required field: 'binary'"
                )

            # Resolve binary — absolute path or PATH lookup
            if not os.path.isabs(binary):
                resolved = shutil.which(binary)
                if resolved is None:
                    raise PluginLoadError(
                        runtime_name,
                        f"binary '{binary}' not found on PATH"
                    )
                binary = resolved

            args = cfg.get("args", [])
            if not isinstance(args, list):
                raise PluginLoadError(
                    runtime_name, "'args' must be a list of strings"
                )
            for i, a in enumerate(args):
                if not isinstance(a, str):
                    raise PluginLoadError(
                        runtime_name,
                        f"'args[{i}]' must be a string, got {type(a).__name__}"
                    )

            env = cfg.get("env", {})
            if not isinstance(env, dict):
                raise PluginLoadError(
                    runtime_name, "'env' must be a mapping"
                )

            # Expand ${VAR} references in env values
            expanded_env: dict[str, str] = {}
            for k, v in env.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise PluginLoadError(
                        runtime_name,
                        f"env key '{k}' and value must be strings"
                    )
                expanded_env[str(k)] = os.path.expandvars(str(v))

            timeout_grace = cfg.get("timeout_grace", 0)
            if not isinstance(timeout_grace, (int, float)) or timeout_grace < 0:
                raise PluginLoadError(
                    runtime_name,
                    "'timeout_grace' must be a non-negative number"
                )

            # Validate unknown keys
            known_keys = {"binary", "args", "env", "timeout_grace"}
            extra = set(cfg.keys()) - known_keys
            if extra:
                raise PluginLoadError(
                    runtime_name,
                    f"unknown fields: {sorted(extra)}. "
                    f"Allowed: {sorted(known_keys)}"
                )

            # Validate binary is executable
            self._validate_cli_binary(runtime_name, binary)

            runner = CLIPluginRunner(
                binary=binary,
                cli_flags=list(args),
                startup_grace=int(timeout_grace),
                _env=expanded_env,
            )
            self._plugins[runtime_name] = runner

    # ---- Tier 2: Python plugins (opt-in) ----------------------------------

    def load_python_plugins(
        self, raw: dict[str, Any] | None, pipeline_dir: Path
    ) -> None:
        """Load Python plugins from ``python_plugins:``.

        Requires ``allow_python=True`` on the registry. Raises
        PluginLoadError if called without the flag set.

        Args:
            raw: The parsed ``python_plugins:`` dict, or None.
            pipeline_dir: Directory containing pipeline.yaml (for
                          resolving relative search_paths).
        """
        if raw is None:
            return
        if not self._allow_python:
            raise PluginLoadError(
                "<python_plugins>",
                "Python plugins require --allow-python-plugins. "
                "Use 'cli_plugins:' for declarative CLI runtimes, "
                "or pass --allow-python-plugins to enable Python plugins."
            )
        if not isinstance(raw, dict):
            raise PluginLoadError("<python_plugins>", "must be a mapping")

        for runtime_name, cfg in raw.items():
            if not isinstance(cfg, dict):
                raise PluginLoadError(
                    runtime_name, "definition must be a mapping"
                )
            if runtime_name in self.BUILTIN_RUNTIMES:
                raise PluginLoadError(
                    runtime_name,
                    f"'{runtime_name}' is a built-in runtime."
                )
            if runtime_name in self._plugins:
                raise PluginLoadError(
                    runtime_name,
                    f"Duplicate runtime name '{runtime_name}'. "
                    f"Already defined in cli_plugins."
                )

            module_name = cfg.get("module")
            class_name = cfg.get("class")
            if not module_name or not class_name:
                raise PluginLoadError(
                    runtime_name,
                    "requires 'module' and 'class' fields"
                )

            # Resolve search paths with explicit, deterministic order:
            #   1. <pipeline_dir>/.unison/plugins/  (project-local, highest priority)
            #   2. ~/.unison/plugins/               (user-global)
            #   3. Any paths in search_paths:        (custom, in list order)
            raw_paths = cfg.get("search_paths", [])
            if not isinstance(raw_paths, list):
                raise PluginLoadError(
                    runtime_name, "'search_paths' must be a list"
                )

            search_paths: list[Path] = []

            # Always search project-local first (deterministic)
            project_plugins = (pipeline_dir / ".unison" / "plugins").resolve()
            search_paths.append(project_plugins)

            # Then user-global
            user_plugins = Path.home() / ".unison" / "plugins"
            search_paths.append(user_plugins)

            # Then explicit custom paths
            for p in raw_paths:
                if not isinstance(p, str):
                    raise PluginLoadError(
                        runtime_name,
                        f"search_paths entries must be strings, "
                        f"got {type(p).__name__}"
                    )
                resolved = Path(os.path.expanduser(p))
                if not resolved.is_absolute():
                    resolved = (pipeline_dir / resolved).resolve()
                search_paths.append(resolved)

            plugin_class = self._load_python_class(
                runtime_name, module_name, class_name, search_paths
            )

            # Validate installation
            ok, err = plugin_class.validate_installation()
            if not ok:
                raise PluginLoadError(
                    runtime_name,
                    f"installation check failed: {err}"
                )

            # Instantiate with optional kwargs
            kwargs = cfg.get("kwargs", {})
            if not isinstance(kwargs, dict):
                raise PluginLoadError(
                    runtime_name, "'kwargs' must be a mapping"
                )
            instance = plugin_class(**kwargs)

            self._plugins[runtime_name] = instance

    def _load_python_class(
        self,
        runtime_name: str,
        module_name: str,
        class_name: str,
        search_paths: list[Path],
    ) -> type[UnisonPlugin]:
        """Find, import, and validate a Python plugin class.

        Uses a deterministic import name derived from the runtime name
        and a hash of the resolved file path. This prevents two plugins
        with the same module filename from colliding in sys.modules.
        """
        module_file = f"{module_name}.py"

        # Find the first matching file in search paths
        found_path: Path | None = None
        for sp in search_paths:
            candidate = sp / module_file
            if candidate.is_file():
                found_path = candidate.resolve()
                break

        if found_path is None:
            searched = ", ".join(str(p) for p in search_paths)
            raise PluginLoadError(
                runtime_name,
                f"module '{module_file}' not found. "
                f"Searched (in order): [{searched}]"
            )

        # Unique import name: runtime_name + hash of resolved path.
        # Prevents sys.modules collisions when two plugins share a
        # module filename (e.g. both named "runner.py").
        path_hash = hashlib.sha256(
            str(found_path).encode()
        ).hexdigest()[:12]
        unique_name = f"unison_plugin_{runtime_name}_{path_hash}"

        spec = importlib.util.spec_from_file_location(
            unique_name, str(found_path)
        )
        if spec is None or spec.loader is None:
            raise PluginLoadError(
                runtime_name,
                f"could not create import spec for {found_path}"
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            # Remove from sys.modules on failure so a retry can work
            sys.modules.pop(unique_name, None)
            raise PluginLoadError(
                runtime_name,
                f"error executing {found_path}: {exc}"
            ) from exc

        plugin_class = getattr(module, class_name, None)
        if plugin_class is None:
            raise PluginLoadError(
                runtime_name,
                f"class '{class_name}' not found in {found_path}"
            )

        if not issubclass(plugin_class, UnisonPlugin):
            raise PluginLoadError(
                runtime_name,
                f"class '{class_name}' does not subclass UnisonPlugin"
            )

        return plugin_class

    # ---- Query -----------------------------------------------------------

    def get(self, runtime_name: str) -> UnisonPlugin | None:
        """Return a loaded plugin instance, or None if not found."""
        return self._plugins.get(runtime_name)

    def names(self) -> frozenset[str]:
        """Return all registered plugin runtime names."""
        return frozenset(self._plugins.keys())

    def list_plugins(self) -> list[tuple[str, str, str]]:
        """Return (runtime_name, class_name, binary) for validation output."""
        result: list[tuple[str, str, str]] = []
        for name, inst in self._plugins.items():
            class_name = type(inst).__name__
            binary = getattr(inst, "binary", "N/A")
            result.append((name, class_name, binary))
        return result

    # ---- Internal --------------------------------------------------------

    @staticmethod
    def _validate_cli_binary(runtime_name: str, binary: str) -> None:
        """Check that a CLI binary exists and is executable.

        Runs ``binary --version`` with a 5s timeout. On failure, raises
        PluginLoadError with a specific message.
        """
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                raise PluginLoadError(
                    runtime_name,
                    f"'{binary} --version' exited with code "
                    f"{result.returncode}. The binary may be broken."
                )
        except FileNotFoundError:
            raise PluginLoadError(
                runtime_name,
                f"binary '{binary}' not found"
            )
        except subprocess.TimeoutExpired:
            raise PluginLoadError(
                runtime_name,
                f"'{binary} --version' timed out after 5s"
            )
        except PermissionError:
            raise PluginLoadError(
                runtime_name,
                f"'{binary}' is not executable (permission denied)"
            )
```

### 4.3 PipelineLoader Integration

```python
# src/unison/pipeline.py — modified load() method

def load(self, pipeline_file: Path, allow_python_plugins: bool = False) -> PipelineSpec:
    ...
    # ---- Parse YAML (unchanged) ----
    ...

    # ---- Plugin loading (NEW) ----
    plugin_registry = PluginRegistry(allow_python=allow_python_plugins)

    try:
        plugin_registry.load_cli_plugins(raw.get("cli_plugins"))
        plugin_registry.load_python_plugins(
            raw.get("python_plugins"), pipeline_dir
        )
    except PluginLoadError as e:
        raise PipelineValidationError(
            f"Plugin loading failed:\n  {e}\n\n"
            f"Tip: For CLI-based runtimes, use 'cli_plugins:' instead of "
            f"'python_plugins:'. Python plugins require --allow-python-plugins."
        ) from e

    # ---- Validate agents against built-in + plugin runtimes ----
    valid_runtimes = PluginRegistry.BUILTIN_RUNTIMES | plugin_registry.names()
    agents = self._build_agents(agents_raw, valid_runtimes)

    ...

    return PipelineSpec(
        version=version,
        world=world,
        agents=agents,
        plugin_registry=plugin_registry,  # <-- NEW: stored on spec
        ...
    )
```

### 4.4 PipelineSpec Gains plugin_registry

```python
# interfaces.py — PipelineSpec gains a new field

@dataclass(frozen=True)
class PipelineSpec:
    ...
    plugin_registry: Any | None = None  # PluginRegistry from loading phase
    ...
```

### 4.5 Orchestrator Consumes, Doesn't Reload

```python
# src/unison/orchestrator.py — modified __init__ and _select_runner

def __init__(self, spec: PipelineSpec, dry_run: bool = False) -> None:
    ...
    # Built-in runners — ALL FOUR, including openclaw
    self._runners: dict[str, Any] = {
        "claude": ClaudeRunner(),
        "codex": CodexRunner(),
        "hermes": HermesRunner(),
        "openclaw": OpenClawRunner(),   # <-- was missing in original proposal
    }

    # Consume pre-validated registry (don't reload)
    self._plugin_registry = spec.plugin_registry
    ...

def _select_runner(self, role: str) -> tuple:
    ...
    runner = self._runners.get(effective_spec.runtime)
    if runner is None and self._plugin_registry is not None:
        runner = self._plugin_registry.get(effective_spec.runtime)
    if runner is None:
        self.halt(f"No runner for runtime: {effective_spec.runtime}")
        return None, None
    return runner, effective_spec
```

---

## 5. CLI Flag Mapping

For **built-in runtimes**, `AgentSpec.cli_flags` property continues to work
exactly as today:

```python
# interfaces.py — AgentSpec.cli_flags (unchanged for built-ins)
@property
def cli_flags(self) -> list[str]:
    _map: dict[Runtime, list[str]] = {
        "claude":   ["-p", "--dangerously-skip-permissions"],
        "codex":    ["exec", "--dangerously-bypass-approvals-and-sandbox"],
        "hermes":   ["chat", "-q", "--yolo"],
        "openclaw": [],
    }
    return _map[self.runtime]
```

For **plugin runtimes**, flags come from the plugin instance, not `AgentSpec`:

- **Tier 1 (cli_plugins):** `args:` in YAML → `CLIPluginRunner.cli_flags`
- **Tier 2 (python_plugins):** `cli_flags` ClassVar on the plugin class

The orchestrator's `_build_agent_cmd` uses the runner's own `_build_command`,
which reads `self.cli_flags`. No special case needed — each runner controls
its own flags.

```python
# orchestrator.py — command building (runs for both built-in and plugin)
def _invoke_agent_for_role(self, role, iteration, ...):
    runner, effective_spec = self._select_runner(role)
    # runner._build_command handles its own binary + flags
    # Built-ins: ClaudeRunner._build_command → ["claude", *spec.cli_flags, prompt]
    # Plugins:   CLIPluginRunner._build_command → [self.binary, *self.cli_flags, prompt]
    ...
```

---

## 6. Backward Compatibility — Complete Proof

**Claim:** Every existing `pipeline.yaml` works without modification.

**Proof by construction (5 checks):**

### 6.1 `cli_plugins:` and `python_plugins:` Keys Absent → No-op

```python
# PipelineLoader.load() — the exact code path
plugin_registry.load_cli_plugins(raw.get("cli_plugins"))     # None → returns early
plugin_registry.load_python_plugins(raw.get("python_plugins"), pipeline_dir)  # None → returns early
```

No keys = no plugins loaded = empty registry. Zero warnings, zero errors.

### 6.2 `plugins_raw` on PipelineSpec is Optional

```python
# interfaces.py — PipelineSpec field
plugin_registry: Any | None = None  # None for existing pipelines without plugins
```

### 6.3 All Four Built-in Runtimes Resolve

```python
# orchestrator.py — the exact runner dict
self._runners: dict[str, Any] = {
    "claude": ClaudeRunner(),
    "codex": CodexRunner(),
    "hermes": HermesRunner(),
    "openclaw": OpenClawRunner(),   # ALL four present
}
```

A pipeline with `runtime: openclaw` hits `_runners.get("openclaw")` and
returns `OpenClawRunner()`. The plugin registry is never consulted.

### 6.4 VALID_RUNTIMES Validates Correctly

```python
# pipeline.py — _build_agents (modified)
valid_runtimes = PluginRegistry.BUILTIN_RUNTIMES | plugin_registry.names()
# When no plugins: valid_runtimes = {"claude", "codex", "hermes", "openclaw"}
# Same set as the old VALID_RUNTIMES.
```

```python
# pipeline.py — _build_agents runtime check
if runtime not in valid_runtimes:
    raise PipelineValidationError(
        f"Invalid runtime '{runtime}' for agent '{key}'. "
        f"Valid runtimes: {sorted(valid_runtimes)}"
    )
```

### 6.5 Existing Tests Pass Unchanged

```bash
# The test suite validates:
$ pytest tests/ -k "phase14" -v
# ✓ phase14-pipeline.yaml loads without cli_plugins or python_plugins keys
# ✓ All four built-in runtimes resolve to correct runners
# ✓ AgentSpec.cli_flags returns correct flags for each built-in
# ✓ pipeline.yaml with runtime: openclaw loads and runs
```

**What changes for existing users:** Nothing. The `Runtime` type alias in
`interfaces.py` widens from `Literal["claude", "codex", "hermes", "openclaw"]`
to `str`, but this is lossless — validation still happens at load time against
the exact same set of built-in names.

---

## 7. Error Handling — Comprehensive Table

All plugin errors are **pre-flight**: they occur during `PipelineLoader.load()`,
before any agent runs. The user sees a clear, specific error message.

| Failure Mode | When Detected | Error Message Example |
|---|---|---|
| `cli_plugins:` key absent | `load_cli_plugins(None)` | No error — no-op |
| `cli_plugins:` not a mapping | `load_cli_plugins()` | `Plugin '<cli_plugins>': must be a mapping` |
| Missing `binary` field | `load_cli_plugins()` | `Plugin 'gemini': missing required field: 'binary'` |
| Binary not on PATH | `load_cli_plugins()` | `Plugin 'gemini': binary 'gemini-internal' not found on PATH` |
| Binary not executable | `_validate_cli_binary()` | `Plugin 'gemini': 'gemini-internal' is not executable (permission denied)` |
| Binary broken (`--version` fails) | `_validate_cli_binary()` | `Plugin 'gemini': 'gemini-internal --version' exited with code 127` |
| `args` not a list | `load_cli_plugins()` | `Plugin 'gemini': 'args' must be a list of strings` |
| `env` not a mapping | `load_cli_plugins()` | `Plugin 'gemini': 'env' must be a mapping` |
| `timeout_grace` negative | `load_cli_plugins()` | `Plugin 'gemini': 'timeout_grace' must be a non-negative number` |
| Unknown YAML key in plugin def | `load_cli_plugins()` | `Plugin 'gemini': unknown fields: ['extra_key']. Allowed: ['binary', 'args', 'env', 'timeout_grace']` |
| Duplicate runtime name | `load_cli_plugins()` | `Plugin 'gemini': Duplicate runtime name. Each plugin must have a unique name.` |
| Built-in name reused | `load_cli_plugins()` | `Plugin 'claude': 'claude' is a built-in runtime. Cannot override. Use a different name.` |
| `python_plugins:` without `--allow-python-plugins` | `load_python_plugins()` | `Plugin '<python_plugins>': Python plugins require --allow-python-plugins.` |
| Plugin `.py` file not found | `_load_python_class()` | `Plugin 'openclaw-v2': module 'openclaw_runner.py' not found. Searched (in order): [...]` |
| Class not found in module | `_load_python_class()` | `Plugin 'openclaw-v2': class 'OpenClawV2Plugin' not found in /path/to/file.py` |
| Class doesn't subclass `UnisonPlugin` | `_load_python_class()` | `Plugin 'openclaw-v2': class 'OpenClawV2Plugin' does not subclass UnisonPlugin` |
| Module import raises exception | `_load_python_class()` | `Plugin 'openclaw-v2': error executing /path/to/file.py: NameError: ...` |
| `validate_installation()` fails | `load_python_plugins()` | `Plugin 'openclaw-v2': installation check failed: binary not found on PATH` |
| Plugin crashes mid-run (timeout) | `UnisonPlugin.run()` | `AgentResult(success=False, error="Timeout after 600s")` — same as built-in failures |
| Plugin crashes mid-run (non-zero exit) | `UnisonPlugin.run()` | `AgentResult(success=False, error="Command exited with code 1")` |
| Runtime name not in built-ins or plugins | `_select_runner()` | `No runner for runtime: 'unknown-runtime'` (halts pipeline) |

### 7.1 CLI Validation Output (`unison validate`)

```
$ unison validate pipeline.yaml
Built-in runtimes: claude, codex, hermes, openclaw
CLI plugins:
  ✓ gemini        → binary: /usr/local/bin/gemini-internal (v2.5.1)
  ✓ custom-script → binary: /opt/scripts/my-agent.sh (v1.0)
Python plugins: (skipped — use --allow-python-plugins to load)
  - openclaw-v2  → module: openclaw_runner (not loaded)
Agent validation:
  ✓ planner   → runtime: claude       (built-in)
  ✓ coder     → runtime: gemini       (CLI plugin)
  ✓ reviewer  → runtime: custom-script (CLI plugin)
Ready to run.
```

---

## 8. Trust & Security Model

### 8.1 Threat Boundaries

| Boundary | Risk | Mitigation |
|---|---|---|
| `cli_plugins:` YAML from untrusted repo | Low — runs only the declared binary | Binary validated at load time (`--version` check). Subprocess isolation via `subprocess.run`. |
| `python_plugins:` from untrusted repo | **High** — arbitrary Python in orchestrator process | Blocked by default. Requires `--allow-python-plugins`. |
| User-global plugins (`~/.unison/plugins/`) | Low — user controls these files | Only loaded when explicitly referenced in `python_plugins:`. Not auto-scanned. |
| Project-local plugins (`.unison/plugins/`) | Moderate — repo author controls these | Project-local Python plugins still require `--allow-python-plugins`. Declarative plugins use only the binary, not Python import. |

### 8.2 The `--allow-python-plugins` Gate

```python
# cli.py — the gate
parser.add_argument(
    "--allow-python-plugins",
    action="store_true",
    default=False,
    help="Enable Python plugin loading (Tier 2). "
         "Without this flag, only declarative 'cli_plugins:' are loaded. "
         "Python plugins execute IN-PROCESS — only use with trusted plugins."
)
```

Without this flag:
- `cli_plugins:` entries are loaded normally (safe — they only declare binaries)
- `python_plugins:` entries cause `PluginLoadError` with a clear message
- No Python code from any plugin directory is imported

With this flag:
- Both `cli_plugins:` and `python_plugins:` are loaded
- Python files in `~/.unison/plugins/` and `.unison/plugins/` are imported
- The user accepts the trust implication

### 8.3 Plugin Sandboxing (Future)

The current design runs plugins in-process. A future iteration (v2.2) will
add a subprocess-based sandbox for Python plugins:
- Load plugin in a `multiprocessing.Process` with restricted `sys.path`
- Communicate via pickle over a pipe (same `AgentRunner` protocol)
- Optional seccomp/AppArmor profile for the worker process

This is deferred because it adds complexity and the declarative `cli_plugins:`
path already covers the common case safely.

---

## 9. Migration Path

### For Existing Pipelines

**No changes needed.** Existing `pipeline.yaml` files without `cli_plugins:` or
`python_plugins:` keys load identically. The `version` field stays at whatever
the pipeline already declares. Schema migration handles version bumps.

### For Adding a Custom Runtime

**Step 1:** Add a `cli_plugins:` section to `pipeline.yaml`:

```yaml
cli_plugins:
  gemini:
    binary: gemini-internal
    args: ["-p", "--output-format", "text", "-y"]
    env:
      GEMINI_API_KEY: "${GEMINI_API_KEY}"
    timeout_grace: 10
```

**Step 2:** Change the agent's `runtime:` to the plugin name:

```yaml
# BEFORE
agents:
  coder:
    runtime: claude

# AFTER
agents:
  coder:
    runtime: gemini
```

**Step 3:** Validate:

```
$ unison validate pipeline.yaml
✓ gemini → binary: /usr/local/bin/gemini-internal
Ready to run.
```

**Step 4:** Run as normal:

```
$ unison run pipeline.yaml
```

### For Python Plugin Authors

**Step 1:** Write a plugin class (see §3.2-3.3) and drop it in
`~/.unison/plugins/`.

**Step 2:** Declare in `python_plugins:`:

```yaml
python_plugins:
  my-runner:
    module: my_runner
    class: MyRunnerPlugin
```

**Step 3:** Pass the trust gate:

```
$ unison run pipeline.yaml --allow-python-plugins
```

---

## 10. Open Questions

1. **pip-installable plugins?** The current design uses file-based discovery.
   A future iteration could support entry points (`unison.plugins` group in
   `pyproject.toml`). Deferred to v2.2.

2. **Plugin precedence between cli_plugins and python_plugins?** Currently,
   `cli_plugins` are loaded first, so a `python_plugins` entry with the same
   name as a `cli_plugins` entry is rejected as a duplicate. This is the
   safest default — it prevents ambiguous resolution.

3. **Hot reload?** Plugins are loaded once during `PipelineLoader.load()`.
   Edits take effect on the next `unison run`. Matches existing runner behavior.

4. **Plugin metadata?** Should plugins declare `min_unison_version`,
   `author`, `description`? Useful for debugging and the `unison validate`
   output. Deferred to v2.2.

5. **Non-subprocess plugins (HTTP, gRPC)?** Supported via Python plugin
   `run()` override. The `AgentRunner` protocol doesn't care how
   `AgentResult` is produced. Only available via Tier 2 (Python API).

---

## 11. File Changes Summary

| File | Change |
|---|---|
| `interfaces.py` | `Runtime` type alias: `Literal[...]` → `str`. `PipelineSpec` gains `plugin_registry`. |
| `src/unison/plugins/__init__.py` | **NEW.** Re-exports `UnisonPlugin`, `PluginRegistry`, `PluginLoadError`, `CLIPluginRunner`. |
| `src/unison/plugins/base.py` | **NEW.** `UnisonPlugin` ABC with `binary`, `cli_flags`, `validate_installation`, `run()`. |
| `src/unison/plugins/cli_runner.py` | **NEW.** `CLIPluginRunner` dataclass — internal runner for declarative CLI plugins. |
| `src/unison/plugins/registry.py` | **NEW.** `PluginRegistry` with `load_cli_plugins`, `load_python_plugins`, `get`, `names`. |
| `src/unison/pipeline.py` | `load()` parses `cli_plugins:` and `python_plugins:`. `_build_agents` validates against `BUILTIN_RUNTIMES ∪ plugin names`. |
| `src/unison/orchestrator.py` | Runner dict includes all 4 built-ins (adds `openclaw`). `_select_runner` checks registry after built-ins. |
| `src/unison/cli.py` | New `--allow-python-plugins` flag. `unison validate` shows plugin status. |
| `tests/test_plugins.py` | **NEW.** Tests for declarative load, missing binary, duplicate name, built-in collision, Python plugin gate, all 4 built-in resolution. |

---

## 12. Summary

The plugin system adds one YAML section (`cli_plugins:`), one optional YAML
section (`python_plugins:`), one new ABC (`UnisonPlugin`), and a registry
that bridges them. The primary path — declarative CLI plugins — requires no
Python code from the user and no trust escalation. The power-user path —
Python plugins — is gated behind an explicit CLI flag. Existing pipelines
work exactly as before. Users add custom runtimes with a 6-line YAML stanza
and zero source edits. All errors surface pre-flight with specific,
actionable messages.
