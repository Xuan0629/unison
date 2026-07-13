"""Tests for cli.py — CLI entry point."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from unison.cli import main, _cmd_run, _cmd_webui
from unison.interfaces import PipelineSpec, ProjectConfig, World




class TestCliAuthorization:
    @staticmethod
    def _args(tmp_path):
        return SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml",
            project=None,
            dry_run=False,
            json=False,
            switch=None,
            model=None,
            save_pref=False,
        )

    @staticmethod
    def _spec(tmp_path, principals):
        return PipelineSpec(
            version="2.0",
            world=World(root=tmp_path),
            agents={},
            project=ProjectConfig(),
            mode="moa:analyze",
            who_can_run=principals,
        )

    def test_cli_denied_before_orchestrator_construction(self, tmp_path, monkeypatch):
        import unison.cli as cli

        spec = self._spec(tmp_path, ["discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        assert _cmd_run(self._args(tmp_path)) == 3
        orchestrator.assert_not_called()
        records = [
            json.loads(line)
            for line in (tmp_path / "observer" / "audit.jsonl").read_text().splitlines()
        ]
        assert records[-1]["event"] == "run_authorization"
        assert records[-1]["principal"] == "cli"
        assert records[-1]["allowed"] is False
        assert records[-1]["reason"] == "principal_not_listed"
        assert records[-1]["configured"] == ["discord:123"]

    def test_cli_allowed_and_audited(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.state import State

        spec = self._spec(tmp_path, ["cli", "discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(cli, "_check_tools", lambda spec, switches: True)
        state = State(phase="done")
        runner = MagicMock()
        runner.run.return_value = state
        monkeypatch.setattr(cli, "Orchestrator", lambda **kwargs: runner)

        assert _cmd_run(self._args(tmp_path)) == 0
        runner.run.assert_called_once()
        record = json.loads(
            (tmp_path / "observer" / "audit.jsonl").read_text().splitlines()[-1]
        )
        assert record["allowed"] is True
        assert record["reason"] == "allowed"
        assert record["principal"] == "cli"

    def test_authorization_audit_failure_blocks_run(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.auth import RunAuthorizationError

        spec = self._spec(tmp_path, ["cli"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(
            cli,
            "authorize_run",
            MagicMock(side_effect=RunAuthorizationError("audit unavailable")),
        )
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        assert main(["run", "--pipeline", str(tmp_path / "pipeline.yaml")]) == 3
        orchestrator.assert_not_called()

    def test_non_cli_principals_fail_closed_without_bridge(self, tmp_path):
        from unison.auth import authorize_run

        spec = self._spec(tmp_path, ["hermes:session-1", "discord:123"])
        assert authorize_run(spec, "cli") is False
        assert authorize_run(spec, "hermes:session-1") is False
        assert authorize_run(spec, "discord:123") is False
        records = [
            json.loads(line)
            for line in (tmp_path / "observer" / "audit.jsonl").read_text().splitlines()
        ]
        assert records[0]["reason"] == "principal_not_listed"
        assert records[1]["reason"] == "principal_source_untrusted"
        assert records[2]["reason"] == "principal_source_untrusted"


class TestWebUiTokenTransport:
    def test_webui_reads_token_from_environment(self, tmp_path, monkeypatch):
        import unison.webui as webui

        serve = MagicMock()
        monkeypatch.setattr(webui, "serve", serve)
        monkeypatch.setenv("UNISON_WEBUI_TOKEN", "environment-token")
        args = argparse.Namespace(project=tmp_path, port=9099, token="")

        assert _cmd_webui(args) == 0
        serve.assert_called_once_with(
            str(tmp_path), port=9099, token="environment-token"
        )

    def test_explicit_token_remains_backward_compatible(self, tmp_path, monkeypatch):
        import unison.webui as webui

        serve = MagicMock()
        monkeypatch.setattr(webui, "serve", serve)
        monkeypatch.setenv("UNISON_WEBUI_TOKEN", "environment-token")
        args = argparse.Namespace(
            project=tmp_path, port=9099, token="explicit-token"
        )

        assert _cmd_webui(args) == 0
        serve.assert_called_once_with(str(tmp_path), port=9099, token="explicit-token")
