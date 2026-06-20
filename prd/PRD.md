# Fix: Exclude sensitive paths from snapshots

## Problem
Snapshots store files unencrypted. Manifest contains full resolved paths.
If snapshot captures .env or auth config, API keys leak.

## Solution
Add 'exclude_patterns' to SnapshotConfig (optional, default includes:
~/.hermes/.env, ~/.openclaw/**/auth-profiles.json).
SnapshotManager._should_snapshot() checks each path against patterns
using fnmatch before copying.

## Implementation
- interfaces.py: SnapshotConfig +exclude_patterns: list[str] field
- snapshot.py: filter paths before snapshot

## Acceptance
- snapshot tests pass (12+)
- New test: sensitive file excluded from snapshot
