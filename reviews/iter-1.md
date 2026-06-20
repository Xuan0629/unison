---
verdict: REQUEST_CHANGES
summary: "Tests pass, but default/configured snapshot exclusions are not enforced by normal construction paths."
findings:
  - "src/unison/snapshot.py:68 leaves FileSnapshotManager.exclude_patterns empty by default, so FileSnapshotManager(base_dir=...) still allows snapshots of ~/.hermes/.env and ~/.openclaw/**/auth-profiles.json unless callers manually pass patterns; the PRD requires these sensitive paths to be excluded by default."
  - "src/unison/pipeline.py:337-343 omits exclude_patterns when building SnapshotConfig from YAML, so user-provided snapshot exclusions are silently ignored."
---
