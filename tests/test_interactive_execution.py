"""Phase A tests for the opt-in interactive execution contract."""
import json
import os
import pty
import sys

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from unison.foreground import (
    ForegroundInvocation,
    ProcessIdentity,
    build_foreground_command,
    launch_linux_terminal,
    main as foreground_main,
    prepare_foreground_invocation,
    read_process_identity,
    run_foreground_wrapper,
)
from unison.io import atomic_write_json
from unison.interfaces import AgentSpec, PipelineSpec, ProjectConfig, World
from unison.pipeline import PipelineLoader, PipelineValidationError


def _pipeline_data(**overrides):
    data = {
        "version": "2.0",
        "project_root": ".",
        "mode": "dev:quick",
        "agents": {
            "developer": {
                "role": "developer",
                "pipeline_role": "developer",
                "runtime": "claude",
                "model": "default",
                "system_prompt_path": "prompts/developer.md",
            },
            "reviewer": {
                "role": "reviewer",
                "pipeline_role": "reviewer",
                "runtime": "codex",
                "model": "default",
                "system_prompt_path": "prompts/reviewer.md",
            },
        },
    }
    data.update(overrides)
    return data


def _load(tmp_path: Path, **overrides):
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump(_pipeline_data(**overrides)), encoding="utf-8")
    return PipelineLoader().load(pipeline)


class TestExecutionPolicyLoader:
    def test_omitted_execution_defaults_to_automatic_headless_bypass(self, tmp_path):
        spec = _load(tmp_path)

        assert spec.execution.selected_policy == "automatic"
        assert spec.execution.resolve_phase("dev_active") == "headless_bypass"

    def test_interactive_builtin_policy_resolves_all_phases_to_foreground(self, tmp_path):
        spec = _load(tmp_path, execution={"selected_policy": "interactive"})

        assert spec.execution.selected_policy == "interactive"
        assert spec.execution.resolve_phase("planning_active") == "foreground_manual"
        assert spec.execution.resolve_phase("dev_review") == "foreground_manual"

    def test_named_policy_phase_override_wins_over_default(self, tmp_path):
        spec = _load(
            tmp_path,
            execution={
                "selected_policy": "review-plan-first",
                "policies": {
                    "review-plan-first": {
                        "default": "headless_bypass",
                        "phases": {"planning_active": "foreground_manual"},
                    },
                },
            },
        )

        assert spec.execution.resolve_phase("planning_active") == "foreground_manual"
        assert spec.execution.resolve_phase("dev_active") == "headless_bypass"

    @pytest.mark.parametrize(
        ("execution", "message"),
        [
            ({"selected_policy": "missing"}, "selected_policy"),
            ({"selected_policy": 1}, "selected_policy must be a string"),
            ({"policies": []}, "policies must be a mapping"),
            ({"policies": {"interactive": {"default": "headless_bypass"}}}, "reserved"),
            ({"policies": {"bad name": {"default": "headless_bypass"}}}, "policy name"),
            ({"policies": {"safe": {"default": "unsafe"}}}, "default"),
            ({"policies": {"safe": {"default": "headless_bypass", "phases": {"not-a-phase": "foreground_manual"}}}}, "phase"),
            ({"policies": {"safe": {"default": "headless_bypass", "phases": {"dev_active": "unsafe"}}}}, "mode"),
        ],
    )
    def test_invalid_policy_config_fails_closed(self, tmp_path, execution, message):
        with pytest.raises(PipelineValidationError, match=message):
            _load(tmp_path, execution=execution)

    def test_legacy_herdr_execution_config_is_rejected(self, tmp_path):
        with pytest.raises(PipelineValidationError, match="execution.mode is no longer supported"):
            _load(tmp_path, execution={"mode": "interactive"})

    def test_legacy_herdr_interactive_block_is_rejected(self, tmp_path):
        with pytest.raises(PipelineValidationError, match="execution.interactive is no longer supported"):
            _load(tmp_path, execution={"interactive": {"backend": "herdr"}})

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"mode": "moa:analyze", "agents": {}}, "does not support MoA"),
            ({"dag": [{"name": "stage"}]}, "does not support DAG"),
            ({"parallel_dev": {"enabled": True}}, "does not support parallel_dev"),
            ({"mode": "chain", "chain": {"stages": [{"mode": "dev:quick"}]}}, "does not support chain"),
        ],
    )
    def test_foreground_policy_unsupported_combinations_fail_at_load(self, tmp_path, overrides, message):
        overrides["execution"] = {"selected_policy": "interactive"}

        with pytest.raises(PipelineValidationError, match=message):
            _load(tmp_path, **overrides)


class TestExecutionPolicyCli:
    def _spec(self, tmp_path: Path) -> PipelineSpec:
        agent = AgentSpec(
            role="reviewer",
            pipeline_role="reviewer",
            runtime="codex",
            model="default",
            system_prompt_path=Path("prompts/reviewer.md"),
        )
        return PipelineSpec(
            version="2.0",
            world=World(root=tmp_path),
            agents={"reviewer": agent},
            project=ProjectConfig(),
            mode="inspect-only",
        )

    def test_policy_override_is_ephemeral_and_reported(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=pipeline, project=None, dry_run=False, json=False,
            switch=None, model=None, save_pref=False,
            execution_policy="interactive", save_execution_policy=None,
        )

        assert cli._cmd_run(args) == 1
        assert pipeline.read_text(encoding="utf-8") == original
        assert "Effective execution policy: interactive" in capsys.readouterr().out
        orchestrator.assert_not_called()

    def test_policy_override_revalidates_effective_spec(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = replace(self._spec(tmp_path), agents={
            "reviewer": replace(self._spec(tmp_path).agents["reviewer"], runtime="hermes"),
        })
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml", project=None, dry_run=False,
            json=False, switch=None, model=None, save_pref=False,
            execution_policy="interactive", save_execution_policy=None,
        )

        assert cli._cmd_run(args) == 1
        assert "only supports claude and codex" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_unknown_policy_override_fails_before_dispatch(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        monkeypatch.setattr(cli, "_load", lambda path: (self._spec(tmp_path), MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml", project=None, dry_run=False,
            json=False, switch=None, model=None, save_pref=False,
            execution_policy="missing", save_execution_policy=None,
        )

        assert cli._cmd_run(args) == 1
        assert "selected_policy 'missing'" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_conflicting_execution_policy_flags_fail_before_dispatch(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        monkeypatch.setattr(cli, "_load", lambda path: (self._spec(tmp_path), MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml", project=None, dry_run=False,
            json=False, switch=None, model=None, save_pref=False,
            execution_policy="automatic", save_execution_policy="interactive",
        )

        assert cli._cmd_run(args) == 1
        assert "must match" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_save_execution_policy_persists_only_after_validation(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text(yaml.safe_dump(_pipeline_data(), sort_keys=False), encoding="utf-8")

        cli._save_execution_policy(pipeline, "automatic")

        assert yaml.safe_load(pipeline.read_text(encoding="utf-8"))["execution"] == {
            "selected_policy": "automatic"
        }

    def test_save_execution_policy_rejects_unknown_policy_before_replace(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")

        with pytest.raises(PipelineValidationError, match="selected_policy 'missing'"):
            cli._save_execution_policy(pipeline, "missing")

        assert pipeline.read_text(encoding="utf-8") == original

    def test_unauthorized_run_does_not_save_execution_policy(self, tmp_path, monkeypatch):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        denied_spec = replace(self._spec(tmp_path), who_can_run=["discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (denied_spec, MagicMock()))
        save = MagicMock()
        monkeypatch.setattr(cli, "_save_execution_policy", save)
        args = SimpleNamespace(
            pipeline=pipeline, project=None, dry_run=False, json=False,
            switch=None, model=None, save_pref=False,
            execution_policy=None, save_execution_policy="automatic",
        )

        assert cli._cmd_run(args) == 3
        save.assert_not_called()
        assert pipeline.read_text(encoding="utf-8") == original

    def test_save_execution_policy_rejects_invalid_source_yaml(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("execution: [", encoding="utf-8")

        with pytest.raises(ValueError, match="pipeline YAML is invalid"):
            cli._save_execution_policy(pipeline, "automatic")

    def test_save_execution_policy_rejects_non_mapping_source_yaml(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("- not-a-pipeline", encoding="utf-8")

        with pytest.raises(ValueError, match="pipeline YAML must be a mapping"):
            cli._save_execution_policy(pipeline, "automatic")

    def test_dry_run_reports_selected_policy(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        loader = MagicMock()
        loader.mode.return_value = "inspect-only"
        monkeypatch.setattr(cli, "_load", lambda path: (spec, loader))

        assert cli._cmd_dry_run(SimpleNamespace(pipeline=tmp_path / "pipeline.yaml")) == 0
        assert "OK  execution.selected_policy = automatic" in capsys.readouterr().out


class TestForegroundInvocationArtifacts:
    def test_create_writes_run_scoped_request_with_immutable_identity(self, tmp_path):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "runs" / "pipeline" / "run-id",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path / "workspace",
            command=["claude", "--permission-mode", "manual"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit="abc123",
        )

        request = invocation.read_request()

        assert invocation.directory.parent == tmp_path / "runs" / "pipeline" / "run-id" / "foreground"
        assert invocation.directory.name == invocation.invocation_id
        assert request["schema_version"] == 1
        assert request["invocation_id"] == invocation.invocation_id
        assert request["phase"] == "dev_active"
        assert request["command"] == ["claude", "--permission-mode", "manual"]

        invocation.request_path.write_text(
            json.dumps({"schema_version": 1, "invocation_id": "other"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="invalid"):
            invocation.read_request()

    def test_result_requires_matching_child_identity_and_numeric_exit_code(self, tmp_path):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=["claude"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        child = ProcessIdentity(pid=123, start_identity="linux:456")
        invocation.write_child(child, process_group_id=123)
        invocation.write_result(child, exit_code=0, started_at="2026-07-15T00:00:00Z")

        assert invocation.read_verified_result() is not None

        invocation.child_path.unlink()
        assert invocation.read_verified_result() is None
        invocation.write_child(child, process_group_id=123)

        with pytest.raises(ValueError, match="integer"):
            invocation.write_result(child, exit_code=True, started_at="2026-07-15T00:00:00Z")

        invocation.result_path.write_text(
            json.dumps({
                "schema_version": 1,
                "invocation_id": invocation.invocation_id,
                "child_pid": 123,
                "child_start_identity": "linux:wrong",
                "exit_code": 0,
            }),
            encoding="utf-8",
        )
        assert invocation.read_verified_result() is None

    def test_heartbeat_requires_matching_wrapper_identity(self, tmp_path):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="codex",
            workdir=tmp_path,
            command=["codex"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        wrapper = ProcessIdentity(pid=999, start_identity="linux:1000")
        invocation.write_heartbeat(wrapper, observed_at="2026-07-15T00:00:00Z")

        assert invocation.read_verified_heartbeat(wrapper) is not None
        assert invocation.read_verified_heartbeat(
            ProcessIdentity(pid=999, start_identity="linux:other")
        ) is None



    def test_linux_process_identity_is_unverifiable_when_proc_stat_is_missing(self, monkeypatch):
        monkeypatch.setattr("unison.foreground.sys.platform", "linux")
        monkeypatch.setattr("unison.foreground.Path.read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")))

        assert read_process_identity(12345) is None


class TestForegroundCommandBuilder:
    def _spec(self, runtime: str, **overrides) -> AgentSpec:
        return AgentSpec(
            role="developer",
            pipeline_role="developer",
            runtime=runtime,
            model=overrides.pop("model", "model-id"),
            system_prompt_path=Path("prompts/developer.md"),
            **overrides,
        )

    def test_claude_uses_native_manual_mode_and_submits_prompt_as_one_argv_token(self):
        prompt = "Implement the task\nwithout shell interpolation."

        command = build_foreground_command(self._spec("claude"), prompt)

        assert command == [
            "claude", "--permission-mode", "manual", "--model", "model-id", prompt,
        ]
        assert not {"-p", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"}.intersection(command)

    def test_codex_uses_native_approval_mode_and_submits_prompt_as_one_argv_token(self):
        prompt = "Implement the task\nwithout shell interpolation."

        command = build_foreground_command(self._spec("codex"), prompt)

        assert command == [
            "codex", "--sandbox", "workspace-write", "--ask-for-approval", "on-request",
            "--model", "model-id", prompt,
        ]
        assert not {"exec", "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust"}.intersection(command)

    def test_default_model_is_not_forwarded(self):
        command = build_foreground_command(self._spec("claude", model="default"), "task")

        assert command == ["claude", "--permission-mode", "manual", "task"]

    def test_prompt_starting_with_option_marker_is_rejected(self):
        with pytest.raises(ValueError, match="must not begin"):
            build_foreground_command(self._spec("claude"), "--help")

    def test_unsupported_runtime_fails_closed(self):
        with pytest.raises(ValueError, match="only supports claude and codex"):
            build_foreground_command(self._spec("hermes"), "task")

    def test_claude_forwards_verified_interactive_reasoning_effort(self):
        command = build_foreground_command(
            self._spec("claude", reasoning_effort="high"), "task",
        )

        assert command == [
            "claude", "--permission-mode", "manual", "--model", "model-id",
            "--effort", "high", "task",
        ]

    def test_codex_reasoning_effort_fails_closed_until_interactive_flag_is_verified(self):
        with pytest.raises(ValueError, match="reasoning_effort"):
            build_foreground_command(self._spec("codex", reasoning_effort="high"), "task")


class TestForegroundWrapper:
    def test_prepare_writes_utf8_prompt_and_argv_request(self, tmp_path):
        spec = AgentSpec(
            role="developer",
            pipeline_role="developer",
            runtime="claude",
            model="default",
            system_prompt_path=Path("prompts/developer.md"),
        )

        invocation = prepare_foreground_invocation(
            run_dir=tmp_path / "run",
            phase="dev_active",
            spec=spec,
            prompt="Review the UTF-8 task: 中文",
            workdir=tmp_path,
            baseline_commit="abc123",
        )

        prompt_path = invocation.directory / "prompt.txt"
        assert prompt_path.read_text(encoding="utf-8") == "Review the UTF-8 task: 中文"
        request = invocation.read_request()
        assert request["prompt_path"] == str(prompt_path)
        assert request["command"] == ["claude", "--permission-mode", "manual"]
        assert "Review the UTF-8 task: 中文" not in request["command"]

    def test_wrapper_requires_linux_identity_before_spawning(self, tmp_path, monkeypatch):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(0)"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        monkeypatch.setattr("unison.foreground.read_process_identity", lambda _pid: None)
        spawn = MagicMock()
        monkeypatch.setattr("unison.foreground.pty.fork", spawn)

        stdin_master, stdin_slave = pty.openpty()
        stdout_master, stdout_slave = pty.openpty()
        try:
            with pytest.raises(RuntimeError, match="wrapper identity"):
                run_foreground_wrapper(
                    invocation, stdin_fd=stdin_slave, stdout_fd=stdout_slave,
                )
        finally:
            os.close(stdin_master)
            os.close(stdin_slave)
            os.close(stdout_master)
            os.close(stdout_slave)

        spawn.assert_not_called()

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity is required")
    def test_wrapper_writes_verified_child_result_and_output_for_pty_child(self, tmp_path):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=[sys.executable, "-c", "print('foreground output')"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )

        prompt_path = invocation.directory / "prompt.txt"
        prompt_path.write_text("first task", encoding="utf-8")
        request = invocation.read_request()
        request["prompt_path"] = str(prompt_path)
        atomic_write_json(invocation.request_path, request)

        stdin_master, stdin_slave = pty.openpty()
        stdout_master, stdout_slave = pty.openpty()
        try:
            exit_code = run_foreground_wrapper(
                invocation,
                stdin_fd=stdin_slave,
                stdout_fd=stdout_slave,
                heartbeat_interval=0.01,
            )
        finally:
            os.close(stdin_master)
            os.close(stdin_slave)
            os.close(stdout_master)
            os.close(stdout_slave)

        assert exit_code == 0
        assert invocation.read_verified_result()["exit_code"] == 0
        assert invocation.output_path.read_text(encoding="utf-8").replace("\r\n", "\n") == "foreground output\n"
        assert invocation.heartbeat_path.exists()

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity is required")
    def test_wrapper_records_exact_nonzero_child_exit(self, tmp_path):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(7)"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        prompt_path = invocation.directory / "prompt.txt"
        prompt_path.write_text("first task", encoding="utf-8")
        request = invocation.read_request()
        request["prompt_path"] = str(prompt_path)
        atomic_write_json(invocation.request_path, request)

        stdin_master, stdin_slave = pty.openpty()
        stdout_master, stdout_slave = pty.openpty()
        try:
            exit_code = run_foreground_wrapper(
                invocation, stdin_fd=stdin_slave, stdout_fd=stdout_slave,
            )
        finally:
            os.close(stdin_master)
            os.close(stdin_slave)
            os.close(stdout_master)
            os.close(stdout_slave)

        assert exit_code == 7
        assert invocation.read_verified_result()["exit_code"] == 7

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity is required")
    def test_wrapper_does_not_write_result_when_output_masking_fails(self, tmp_path, monkeypatch):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(0)"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        prompt_path = invocation.directory / "prompt.txt"
        prompt_path.write_text("first task", encoding="utf-8")
        request = invocation.read_request()
        request["prompt_path"] = str(prompt_path)
        atomic_write_json(invocation.request_path, request)
        monkeypatch.setattr("unison.foreground.mask_secrets", lambda _text: (_ for _ in ()).throw(RuntimeError("mask failed")))

        stdin_master, stdin_slave = pty.openpty()
        stdout_master, stdout_slave = pty.openpty()
        try:
            with pytest.raises(RuntimeError, match="mask failed"):
                run_foreground_wrapper(
                    invocation, stdin_fd=stdin_slave, stdout_fd=stdout_slave,
                )
        finally:
            os.close(stdin_master)
            os.close(stdin_slave)
            os.close(stdout_master)
            os.close(stdout_slave)

        assert invocation.read_verified_result() is None

    def test_wrapper_rejects_non_tty_without_spawning(self, tmp_path, monkeypatch):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=[sys.executable, "-c", "raise SystemExit(0)"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        monkeypatch.setattr("unison.foreground.os.isatty", lambda _fd: False)
        spawn = MagicMock()
        monkeypatch.setattr("unison.foreground.pty.fork", spawn)

        with pytest.raises(RuntimeError, match="TTY"):
            run_foreground_wrapper(invocation)

        spawn.assert_not_called()


class TestLinuxForegroundLauncher:
    def _invocation(self, tmp_path: Path) -> ForegroundInvocation:
        return ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=["claude", "--permission-mode", "manual"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )

    def test_launches_gnome_terminal_with_wrapper_argv_and_returns_handoff_pid(self, tmp_path, monkeypatch):
        invocation = self._invocation(tmp_path)
        monkeypatch.setattr("unison.foreground.sys.platform", "linux")
        monkeypatch.setattr("unison.foreground.shutil.which", lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None)
        monkeypatch.setenv("DISPLAY", ":0")
        process = MagicMock(pid=4321)
        spawn = MagicMock(return_value=process)
        monkeypatch.setattr("unison.foreground.subprocess.Popen", spawn)

        handoff_pid = launch_linux_terminal(invocation)

        assert handoff_pid == 4321
        assert spawn.call_args.args[0] == [
            "/usr/bin/gnome-terminal",
            "--window",
            "--title", f"Unison foreground {invocation.invocation_id}",
            "--working-directory", str(tmp_path),
            "--",
            sys.executable,
            "-m", "unison.foreground", "wrapper",
            "--invocation-dir", str(invocation.directory),
        ]
        assert spawn.call_args.kwargs == {"start_new_session": True}

    def test_fails_closed_without_gui_before_spawning(self, tmp_path, monkeypatch):
        invocation = self._invocation(tmp_path)
        monkeypatch.setattr("unison.foreground.sys.platform", "linux")
        monkeypatch.setattr("unison.foreground.shutil.which", lambda _name: "/usr/bin/gnome-terminal")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        spawn = MagicMock()
        monkeypatch.setattr("unison.foreground.subprocess.Popen", spawn)

        with pytest.raises(RuntimeError, match="GUI session"):
            launch_linux_terminal(invocation)

        spawn.assert_not_called()

    def test_fails_closed_when_gnome_terminal_is_missing(self, tmp_path, monkeypatch):
        invocation = self._invocation(tmp_path)
        monkeypatch.setattr("unison.foreground.sys.platform", "linux")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr("unison.foreground.shutil.which", lambda _name: None)

        with pytest.raises(RuntimeError, match="GNOME Terminal"):
            launch_linux_terminal(invocation)

    def test_fails_closed_on_non_linux_before_spawning(self, tmp_path, monkeypatch):
        invocation = self._invocation(tmp_path)
        monkeypatch.setattr("unison.foreground.sys.platform", "darwin")
        spawn = MagicMock()
        monkeypatch.setattr("unison.foreground.subprocess.Popen", spawn)

        with pytest.raises(RuntimeError, match="only supports Linux"):
            launch_linux_terminal(invocation)

        spawn.assert_not_called()


class TestForegroundWrapperEntry:
    def test_wrapper_entry_delegates_to_invocation_wrapper(self, tmp_path, monkeypatch):
        invocation = ForegroundInvocation.create(
            run_dir=tmp_path / "run",
            phase="dev_active",
            role="developer",
            runtime="claude",
            workdir=tmp_path,
            command=["claude", "--permission-mode", "manual"],
            prompt_path=tmp_path / "prompt.txt",
            baseline_commit=None,
        )
        wrapper = MagicMock(return_value=7)
        monkeypatch.setattr("unison.foreground.run_foreground_wrapper", wrapper)

        exit_code = foreground_main([
            "wrapper", "--invocation-dir", str(invocation.directory),
        ])

        assert exit_code == 7
        called_invocation = wrapper.call_args.args[0]
        assert called_invocation.invocation_id == invocation.invocation_id
        assert called_invocation.directory == invocation.directory

    def test_wrapper_entry_rejects_missing_request(self, tmp_path):
        missing = tmp_path / "missing"

        with pytest.raises(SystemExit) as exc:
            foreground_main(["wrapper", "--invocation-dir", str(missing)])

        assert exc.value.code == 2
