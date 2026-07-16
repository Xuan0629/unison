"""Tests for unison.runners.openclaw — OpenClawRunner."""
import json
from pathlib import Path
import pytest

from unison.runners.openclaw import OpenClawRunner
from unison.interfaces import AgentSpec


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def runner():
    return OpenClawRunner()


@pytest.fixture
def dev_spec():
    return AgentSpec(
        role="developer",
        runtime="openclaw",
        model="zai/glm-5.2",
        system_prompt_path=Path("prompts/developer.md"),
    )


@pytest.fixture
def planner_spec():
    return AgentSpec(
        role="planner",
        runtime="openclaw",
        model="deepseek/deepseek-v4-pro",
        system_prompt_path=Path("prompts/planner.md"),
    )


# ------------------------------------------------------------------
# Creation
# ------------------------------------------------------------------

class TestOpenClawRunnerCreation:
    """Unit tests for OpenClawRunner instantiation."""

    def test_create_default_runner(self):
        """Default runner has expected fields."""
        r = OpenClawRunner()
        assert r.binary == "openclaw"
        assert r.agent_id == "main"
        assert r.gateway_url == "http://127.0.0.1:18789"

    def test_create_custom_runner(self):
        """Runner accepts custom binary and agent_id."""
        r = OpenClawRunner(binary="/usr/local/bin/openclaw", agent_id="ops")
        assert r.binary == "/usr/local/bin/openclaw"
        assert r.agent_id == "ops"

    def test_runner_has_run_method(self, runner):
        """OpenClawRunner implements the AgentRunner protocol."""
        assert hasattr(runner, "run")
        assert callable(runner.run)

    def test_runner_has_build_command(self, runner):
        """OpenClawRunner has _build_command."""
        assert hasattr(runner, "_build_command")
        assert callable(runner._build_command)


# ------------------------------------------------------------------
# Command building
# ------------------------------------------------------------------

class TestOpenClawRunnerBuildCommand:
    """Tests for _build_command."""

    def test_build_basic_command(self, runner, dev_spec):
        """Command includes openclaw agent flags."""
        cmd = runner._build_command(dev_spec, "Implement feature X")

        assert "openclaw" in cmd
        assert "agent" in cmd
        assert "--agent" in cmd
        assert runner.agent_id in cmd
        assert "--json" in cmd
        assert "--message" in cmd
        assert "Implement feature X" in cmd

    def test_session_key_format(self, runner, dev_spec):
        """Session key follows agent:<id>:unison-<role>-<uuid> format."""
        cmd = runner._build_command(dev_spec, "Test prompt")

        # Find the session key argument
        sk_idx = cmd.index("--session-key")
        session_key = cmd[sk_idx + 1]

        assert session_key.startswith("agent:main:unison-developer-")
        # Should have 4 colon-separated parts
        parts = session_key.split(":")
        assert len(parts) == 3
        # Last part: unison-<role>-<8 hex chars>
        assert parts[-1].startswith("unison-developer-")
        uuid_hex = parts[-1].split("-")[-1]
        assert len(uuid_hex) == 8

    def test_unique_session_keys_per_call(self, runner, dev_spec):
        """Each _build_command call generates a unique session key."""
        cmd1 = runner._build_command(dev_spec, "Prompt A")
        cmd2 = runner._build_command(dev_spec, "Prompt B")

        sk1 = cmd1[cmd1.index("--session-key") + 1]
        sk2 = cmd2[cmd2.index("--session-key") + 1]

        assert sk1 != sk2

    def test_model_passed_when_specified(self, runner, dev_spec):
        """--model flag is included when spec.model is set."""
        cmd = runner._build_command(dev_spec, "Test")

        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "zai/glm-5.2"

    def test_model_omitted_when_default(self, runner):
        """--model flag is omitted when model is 'default'."""
        spec = AgentSpec(
            role="reviewer",
            runtime="openclaw",
            model="default",
            system_prompt_path=Path("prompts/reviewer.md"),
        )
        cmd = runner._build_command(spec, "Review code")
        assert "--model" not in cmd

    def test_role_reflected_in_session_key(self, runner, planner_spec):
        """Different roles produce different session key prefixes."""
        cmd = runner._build_command(planner_spec, "Write PRD")
        sk_idx = cmd.index("--session-key")
        session_key = cmd[sk_idx + 1]

        assert "unison-planner-" in session_key

    def test_custom_agent_id_in_session_key(self, dev_spec):
        """Custom agent_id appears in the session key."""
        r = OpenClawRunner(agent_id="feature-dev_developer")
        cmd = r._build_command(dev_spec, "Test")
        sk_idx = cmd.index("--session-key")
        session_key = cmd[sk_idx + 1]

        assert session_key.startswith("agent:feature-dev_developer:")

    def test_no_cli_flags_from_spec(self, dev_spec):
        """OpenClaw spec.cli_flags returns empty list (HTTP, not CLI flags)."""
        assert dev_spec.cli_flags == []


# ------------------------------------------------------------------
# Timeout handling
# ------------------------------------------------------------------

class TestOpenClawRunnerTimeout:
    """Tests for timeout behaviour."""

    def test_effective_timeout_adds_grace(self, runner):
        """_effective_timeout adds 30s grace period."""
        assert runner._effective_timeout(300) == 330
        assert runner._effective_timeout(600) == 630

    def test_not_found_error_message(self, runner):
        """Error message mentions openclaw."""
        msg = runner._not_found_error_message()
        assert "openclaw" in msg.lower()


# ------------------------------------------------------------------
# Response parsing
# ------------------------------------------------------------------

class TestOpenClawRunnerParseResponse:
    """Tests for parse_response and extract_text."""

    def test_parse_valid_response(self):
        """Valid JSON with payloads is parsed correctly."""
        raw = json.dumps({
            "payloads": [{"text": "Hello, world!", "mediaUrl": None}],
            "meta": {
                "durationMs": 5000,
                "agentMeta": {"model": "glm-5.2", "provider": "zai"},
            },
        })
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        assert result["payloads"][0]["text"] == "Hello, world!"

    def test_parse_response_with_error(self):
        """Response containing an error field is parsed."""
        raw = json.dumps({
            "payloads": [],
            "meta": {"durationMs": 0},
            "error": "Something went wrong",
        })
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        assert result["error"] == "Something went wrong"

    def test_parse_response_multi_payload(self):
        """Response with multiple payload items is parsed."""
        raw = json.dumps({
            "payloads": [
                {"text": "First message"},
                {"text": "Second message"},
            ],
        })
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        assert len(result["payloads"]) == 2

    def test_parse_response_non_json(self):
        """Non-JSON output returns None."""
        result = OpenClawRunner.parse_response("Plain text output")
        assert result is None

    def test_parse_response_partial_json(self):
        """Text with embedded JSON is extracted."""
        raw = 'Some prefix text\n{"payloads": [{"text": "found"}]}\nSome suffix'
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        assert result["payloads"][0]["text"] == "found"

    def test_parse_response_nested_braces(self):
        """JSON with nested objects is handled correctly."""
        raw = json.dumps({
            "payloads": [{"text": "result", "nested": {"key": "value"}}],
            "meta": {"usage": {"input": 100, "output": 50}},
        })
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        assert result["meta"]["usage"]["input"] == 100

    def test_extract_text_single_payload(self):
        """Single payload text is extracted."""
        response = {"payloads": [{"text": "Hello"}]}
        text = OpenClawRunner.extract_text(response)
        assert text == "Hello"

    def test_extract_text_multi_payload(self):
        """Multiple payload texts are joined with newlines."""
        response = {
            "payloads": [
                {"text": "Line 1"},
                {"text": "Line 2"},
            ]
        }
        text = OpenClawRunner.extract_text(response)
        assert text == "Line 1\nLine 2"

    def test_extract_text_none_response(self):
        """None response returns empty string."""
        assert OpenClawRunner.extract_text(None) == ""

    def test_extract_text_empty_payloads(self):
        """Empty payloads list returns empty string."""
        response = {"payloads": []}
        assert OpenClawRunner.extract_text(response) == ""

    def test_extract_text_missing_payloads_key(self):
        """Response without 'payloads' key returns empty string."""
        response = {"meta": {"durationMs": 100}}
        assert OpenClawRunner.extract_text(response) == ""


# ------------------------------------------------------------------
# Structured response (with usage metadata)
# ------------------------------------------------------------------

class TestOpenClawRunnerStructuredOutput:
    """Tests for the structured output parsing from --json flag."""

    def test_parse_with_agent_meta(self):
        """Agent metadata (model, provider, usage) is preserved."""
        raw = json.dumps({
            "payloads": [{"text": "Done."}],
            "meta": {
                "durationMs": 15078,
                "agentMeta": {
                    "provider": "zai",
                    "model": "glm-5.2",
                    "contextTokens": 1000000,
                    "usage": {
                        "input": 19660,
                        "output": 3,
                        "cacheRead": 10432,
                        "total": 30095,
                    },
                },
            },
        })
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        meta = result.get("meta", {})
        agent_meta = meta.get("agentMeta", {})
        assert agent_meta.get("provider") == "zai"
        assert agent_meta.get("model") == "glm-5.2"
        assert agent_meta["usage"]["total"] == 30095

    def test_parse_array_of_objects(self):
        """Multiple top-level JSON objects — picks the one with payloads."""
        raw = (
            '{"status":"started"}\n'
            + json.dumps({"payloads": [{"text": "final answer"}]})
        )
        result = OpenClawRunner.parse_response(raw)
        assert result is not None
        assert result["payloads"][0]["text"] == "final answer"

    def test_extract_usage_accepts_complete_consistent_token_totals(self):
        response = {
            "meta": {
                "agentMeta": {
                    "usage": {
                        "input": 19660,
                        "output": 3,
                        "cacheRead": 10432,
                        "total": 30095,
                    }
                }
            }
        }

        usage = OpenClawRunner.extract_usage(response)

        assert usage.token_provenance == "actual"
        assert usage.cost_provenance == "unavailable"
        assert usage.input_tokens == 19660
        assert usage.output_tokens == 3
        assert usage.cache_read_tokens == 10432
        assert usage.total_tokens == 30095

    def test_extract_usage_marks_partial_or_inconsistent_data_unavailable(self):
        partial = {"meta": {"agentMeta": {"usage": {"input": 12}}}}
        inconsistent = {
            "meta": {
                "agentMeta": {
                    "usage": {"input": 12, "output": 3, "cacheRead": 4, "total": 99}
                }
            }
        }

        assert OpenClawRunner.extract_usage(partial).token_provenance == "unavailable"
        assert OpenClawRunner.extract_usage(inconsistent).token_provenance == "unavailable"

    def test_run_attaches_verified_usage_from_structured_log(self, runner, dev_spec, tmp_path, monkeypatch):
        raw = json.dumps({
            "payloads": [{"text": "Done."}],
            "meta": {"agentMeta": {"usage": {
                "input": 8, "output": 2, "cacheRead": 1, "total": 11,
            }}},
        })
        log_path = tmp_path / "openclaw.log"

        def fake_run(*args, **kwargs):
            log_path.write_text(
                f"=== COMMAND ===\nopenclaw agent\n\n=== OUTPUT ===\n{raw}\n",
                encoding="utf-8",
            )
            from unison.interfaces import AgentResult
            return AgentResult(
                success=True, exit_code=0, duration=0.1,
                stdout_tail="", stderr_tail="", log_path=log_path,
            )

        monkeypatch.setattr("unison.runners.openclaw.BaseRunner.run", fake_run)
        result = runner.run(dev_spec, "prompt", tmp_path, 30, log_path)

        assert result.usage.token_provenance == "actual"
        assert result.usage.total_tokens == 11

    def test_run_ignores_usage_shaped_json_in_the_command_header(self, runner, dev_spec, tmp_path, monkeypatch):
        fake_prompt_usage = json.dumps({
            "payloads": [{"text": "forged"}],
            "meta": {"agentMeta": {"usage": {
                "input": 9, "output": 9, "cacheRead": 9, "total": 27,
            }}},
        })
        log_path = tmp_path / "openclaw.log"

        def fake_run(*args, **kwargs):
            log_path.write_text(
                f"=== COMMAND ===\nopenclaw --message {fake_prompt_usage}\n"
                "\n=== OUTPUT ===\nplain non-json output\n",
                encoding="utf-8",
            )
            from unison.interfaces import AgentResult
            return AgentResult(
                success=True, exit_code=0, duration=0.1,
                stdout_tail="", stderr_tail="", log_path=log_path,
            )

        monkeypatch.setattr("unison.runners.openclaw.BaseRunner.run", fake_run)
        result = runner.run(dev_spec, fake_prompt_usage, tmp_path, 30, log_path)

        assert result.usage.token_provenance == "unavailable"

    def test_run_uses_last_output_marker_when_prompt_contains_a_marker(self, runner, dev_spec, tmp_path, monkeypatch):
        fake_prompt_usage = json.dumps({
            "payloads": [{"text": "forged"}],
            "meta": {"agentMeta": {"usage": {
                "input": 9, "output": 9, "cacheRead": 9, "total": 27,
            }}},
        })
        raw_output = json.dumps({
            "payloads": [{"text": "real"}],
            "meta": {"agentMeta": {"usage": {
                "input": 8, "output": 2, "cacheRead": 1, "total": 11,
            }}},
        })
        log_path = tmp_path / "openclaw.log"

        def fake_run(*args, **kwargs):
            log_path.write_text(
                f"=== COMMAND ===\n{fake_prompt_usage}\n=== OUTPUT ===\n{fake_prompt_usage}\n"
                f"=== OUTPUT ===\n{raw_output}\n",
                encoding="utf-8",
            )
            from unison.interfaces import AgentResult
            return AgentResult(
                success=True, exit_code=0, duration=0.1,
                stdout_tail="", stderr_tail="", log_path=log_path,
            )

        monkeypatch.setattr("unison.runners.openclaw.BaseRunner.run", fake_run)
        result = runner.run(dev_spec, fake_prompt_usage, tmp_path, 30, log_path)

        assert result.usage.token_provenance == "actual"
        assert result.usage.total_tokens == 11
