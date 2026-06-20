# Fix: API key masking in runner logs

## Problem
Runner log files contain full prompts including API keys passed via environment
or embedded in the prompt text. This is a security risk.

## Solution
Add mask_secrets(text: str) -> str utility that replaces:
- sk-... (OpenAI keys)
- sk-ant-... (Anthropic keys)
- Bearer <token>
- api_key=<value>
- Values of os.environ keys ending in _API_KEY

With "[REDACTED]". Apply to log writing in all runners.

## Implementation
- New function in src/unison/runners/base.py or new src/unison/utils.py
- Call mask_secrets() before writing log files
- Preserve format and context, only mask the values

## Acceptance
- Existing tests pass
- Manual verification: logs contain [REDACTED] not actual keys
