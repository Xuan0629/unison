---
verdict: REQUEST_CHANGES
summary: "Secret masking is applied to runner logs and the requested tests pass, but quoted API-key assignments can still leak."
findings:
  - "src/unison/runners/base.py:35 only masks unquoted NAME_API_KEY=value assignments. Common prompt/env-file forms such as OPENAI_API_KEY=\"plain-token\" or FOO_API_KEY='plain-token' are not redacted unless the value also matches another generic pattern or is already present in os.environ, so embedded API keys can still be written verbatim to runner logs."
---
