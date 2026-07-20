"""Tests for GitEvidenceReader — read-only Git prompt evidence."""

import subprocess

from unison.git_evidence import GitEvidenceReader


def _completed(args: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args, returncode, stdout.encode("utf-8"), b"")


def test_head_commit_returns_short_hash_and_empty_on_git_failure(tmp_path, monkeypatch):
    reader = GitEvidenceReader(tmp_path)

    monkeypatch.setattr(
        "unison.git_evidence.subprocess.run",
        lambda *args, **kwargs: _completed(args[0], "1234567890abcdef\n"),
    )
    assert reader.head_commit() == "12345678"

    def _raise(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr("unison.git_evidence.subprocess.run", _raise)
    assert reader.head_commit() == ""


def test_cumulative_diff_requires_existing_baseline_and_truncates(tmp_path, monkeypatch):
    reader = GitEvidenceReader(tmp_path)
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "cat-file", "-e"]:
            return _completed(args)
        return _completed(args, "abcdef")

    monkeypatch.setattr("unison.git_evidence.subprocess.run", _run)

    assert reader.cumulative_diff("baseline", max_chars=3) == "abc\n...[cumulative diff truncated]"
    assert calls == [
        ["git", "cat-file", "-e", "baseline"],
        ["git", "diff", "baseline", "HEAD", "--stat"],
    ]

    assert reader.cumulative_diff("") == ""


def test_recent_diff_uses_cached_diff_for_initial_commit_and_truncates(tmp_path, monkeypatch):
    reader = GitEvidenceReader(tmp_path)

    def _run(args, **kwargs):
        if args == ["git", "rev-parse", "HEAD~1"]:
            return _completed(args, returncode=1)
        assert args == ["git", "diff", "--cached"]
        return _completed(args, "abcdef")

    monkeypatch.setattr("unison.git_evidence.subprocess.run", _run)

    assert reader.recent_diff(max_chars=3) == "abc\n...[diff truncated]"
