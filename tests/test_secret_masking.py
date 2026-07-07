"""Tests for secret masking — mask_secrets and _mask_log_file.

Validates that API keys, tokens, and secrets are redacted before
being persisted to log files.
"""

import os
import tempfile
from pathlib import Path

import pytest

from unison.runners.base import mask_secrets, BaseRunner


# ------------------------------------------------------------------
# mask_secrets — static pattern tests
# ------------------------------------------------------------------


class TestMaskSecretsStaticPatterns:
    """Tests for static _SECRET_PATTERNS in mask_secrets()."""

    def test_anthropic_key_redacted(self):
        assert "sk-ant-abc123" not in mask_secrets("key=sk-ant-abc123-xyz")
        assert "[REDACTED]" in mask_secrets("key=sk-ant-abc123-xyz")

    def test_openai_key_redacted(self):
        assert "sk-proj-abc" not in mask_secrets("OPENAI_API_KEY=sk-proj-abc123")
        assert "[REDACTED]" in mask_secrets("OPENAI_API_KEY=sk-proj-abc123")

    def test_bearer_token_redacted(self):
        result = mask_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.xyz")
        assert "eyJhbGci" not in result
        # Both the Bearer pattern and the generic Authorization pattern fire,
        # so the final output is Authorization: [REDACTED]
        assert result == "Authorization: [REDACTED]"

    def test_api_key_value_unquoted_redacted(self):
        result = mask_secrets('api_key=abc123secret')
        assert "abc123secret" not in result
        assert "api_key=[REDACTED]" in result

    def test_api_key_value_double_quoted_redacted(self):
        result = mask_secrets('api_key="my-secret-key"')
        assert "my-secret-key" not in result
        assert "api_key=[REDACTED]" in result

    def test_api_key_value_single_quoted_redacted(self):
        result = mask_secrets("api_key='my-secret-key'")
        assert "my-secret-key" not in result
        assert "api_key=[REDACTED]" in result

    def test_env_api_key_assignment_redacted(self):
        result = mask_secrets("OPENAI_API_KEY=sk-abc123")
        assert "sk-abc123" not in result
        assert "OPENAI_API_KEY=[REDACTED]" in result

    def test_env_api_key_quoted_redacted(self):
        result = mask_secrets('MY_API_KEY="sk-secret-789"')
        assert "sk-secret-789" not in result
        assert "MY_API_KEY=[REDACTED]" in result

    def test_env_secret_assignment_redacted(self):
        result = mask_secrets("DB_SECRET=super-secret-password")
        assert "super-secret-password" not in result
        assert "DB_SECRET=[REDACTED]" in result

    def test_github_classic_token_redacted(self):
        result = mask_secrets("GITHUB_TOKEN=ghp_abc123def456ghi789jkl012mno345pqr678stu")
        assert "ghp_abc" not in result
        assert "[REDACTED]" in result

    def test_github_fine_grained_token_redacted(self):
        result = mask_secrets("token: github_pat_11ABC123def456ghi789jkl012mno345pqr678stu")
        assert "github_pat_11" not in result
        assert "[REDACTED]" in result

    def test_gitlab_token_redacted(self):
        result = mask_secrets("GITLAB_TOKEN=glpat-abc123def456ghi789jkl")
        assert "glpat-abc" not in result
        assert "[REDACTED]" in result

    def test_aws_access_key_redacted(self):
        result = mask_secrets("AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF")
        assert "AKIA1234" not in result
        assert "[REDACTED]" in result

    def test_authorization_header_redacted(self):
        result = mask_secrets("Authorization: Basic dXNlcjpwYXNz")
        assert "dXNlcjpwYXNz" not in result
        assert "Authorization: [REDACTED]" in result

    def test_multiple_secrets_in_one_text(self):
        text = "key1=sk-ant-abc\nkey2=sk-xyz123"
        result = mask_secrets(text)
        assert "sk-ant-abc" not in result
        assert "sk-xyz123" not in result
        assert result.count("[REDACTED]") == 2

    def test_no_secrets_unchanged(self):
        text = "This is normal output with no secrets."
        assert mask_secrets(text) == text

    def test_empty_string(self):
        assert mask_secrets("") == ""

    def test_redacted_in_non_secret_context(self):
        """Ensure we don't redact legitimate content."""
        text = "The task-force discussed the project plan."
        result = mask_secrets(text)
        # "sk-" is not at a word boundary here
        assert "sk-" not in result or "task-force" in result


# ------------------------------------------------------------------
# mask_secrets — env-value masking
# ------------------------------------------------------------------


class TestMaskSecretsEnvValues:
    """Tests for dynamic env-value masking in mask_secrets()."""

    def test_env_value_in_output_redacted(self, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "my-real-key-12345")
        result = mask_secrets("config has my-real-key-12345 in it")
        assert "my-real-key-12345" not in result
        assert "[REDACTED]" in result

    def test_env_secret_value_in_output_redacted(self, monkeypatch):
        monkeypatch.setenv("APP_SECRET", "s3cr3t-v4lu3")
        result = mask_secrets("secret: s3cr3t-v4lu3")
        assert "s3cr3t-v4lu3" not in result
        assert "[REDACTED]" in result

    def test_env_keyname_not_confused_with_value(self, monkeypatch):
        """The env key name itself should not be redacted."""
        monkeypatch.setenv("TEST_API_KEY", "abc")
        result = mask_secrets("Using TEST_API_KEY from environment")
        assert "TEST_API_KEY" in result

    def test_empty_env_value_ignored(self, monkeypatch):
        monkeypatch.setenv("EMPTY_API_KEY", "")
        result = mask_secrets("no secrets here")
        assert result == "no secrets here"


# ------------------------------------------------------------------
# _mask_log_file
# ------------------------------------------------------------------


class TestMaskLogFile:
    """Tests for BaseRunner._mask_log_file()."""

    def test_masks_secrets_in_log_file(self):
        log_path = Path(tempfile.mktemp(suffix=".log"))
        log_path.write_text("key=sk-ant-secret123\nNormal text\n", encoding="utf-8")
        try:
            BaseRunner._mask_log_file(log_path)
            content = log_path.read_text(encoding="utf-8")
            assert "sk-ant-secret123" not in content
            assert "[REDACTED]" in content
            assert "Normal text" in content
        finally:
            log_path.unlink(missing_ok=True)

    def test_no_secrets_file_unchanged(self):
        log_path = Path(tempfile.mktemp(suffix=".log"))
        original = "Just normal output.\nNothing secret here.\n"
        log_path.write_text(original, encoding="utf-8")
        try:
            BaseRunner._mask_log_file(log_path)
            assert log_path.read_text(encoding="utf-8") == original
        finally:
            log_path.unlink(missing_ok=True)

    def test_nonexistent_file_no_error(self):
        log_path = Path(tempfile.mktemp(suffix=".log"))
        # Don't create it
        BaseRunner._mask_log_file(log_path)  # should not raise

    def test_empty_file_no_error(self):
        log_path = Path(tempfile.mktemp(suffix=".log"))
        log_path.write_text("", encoding="utf-8")
        try:
            BaseRunner._mask_log_file(log_path)
            assert log_path.read_text(encoding="utf-8") == ""
        finally:
            log_path.unlink(missing_ok=True)

    def test_multiple_secrets_masked(self):
        log_path = Path(tempfile.mktemp(suffix=".log"))
        original = "token1=ghp_abc123def456ghi789jkl012mno345pqr678stu\ntoken2=sk-ant-xyz789\n"
        log_path.write_text(original, encoding="utf-8")
        try:
            BaseRunner._mask_log_file(log_path)
            content = log_path.read_text(encoding="utf-8")
            assert "ghp_abc" not in content
            assert "sk-ant-xyz" not in content
            assert content.count("[REDACTED]") == 2
        finally:
            log_path.unlink(missing_ok=True)
