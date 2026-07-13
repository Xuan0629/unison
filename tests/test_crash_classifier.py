from pathlib import Path

from unison.interfaces import AgentResult
from unison.supervisor import CrashClassifier


def _failed_result(*, error: str, stderr_tail: str) -> AgentResult:
    return AgentResult(
        success=False,
        exit_code=1,
        duration=1.0,
        stdout_tail="",
        stderr_tail=stderr_tail,
        log_path=Path("/nonexistent/agent.log"),
        error=error,
    )


def test_rate_limit_stderr_is_retryable_model_error():
    result = _failed_result(
        error="request failed",
        stderr_tail="Error: 429 rate limit exceeded",
    )

    assert CrashClassifier.classify(result) == "MODEL_ERROR"


def test_timeout_error_wins_over_generic_stderr_error_text():
    result = _failed_result(
        error="subprocess timeout after 600s",
        stderr_tail="Error: request aborted",
    )

    assert CrashClassifier.classify(result) == "TIMEOUT"


def test_traceback_in_consumer_source_remains_non_retryable():
    result = _failed_result(
        error="subprocess timeout after 600s",
        stderr_tail='Traceback\n  File "src/app.py", line 1',
    )

    assert CrashClassifier.classify(result) == "CONSUMER_BUG"