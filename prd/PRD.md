# P12b: Conditional Model Tiering

Implement dynamic model tier-switching based on budget/error conditions.

## Part 1: tier_upgrade Config
- Add `tier_upgrade: dict[str, dict[str, str]]` to BudgetConfig in interfaces.py
- Keys: `from` (model), `to` (model), `reasoning_effort`
- Default: empty dict
- YAML example:
```yaml
budget:
  tier_upgrade:
    reviewer:
      from: gpt-5.6-terra
      to: gpt-5.6-sol
      reasoning_effort: xhigh
```

## Part 2: Multi-hop Downgrade Chains
- Current downgrade_map: single-hop `{role: {to, model}}`
- Extend: support list entries for chain downgrades
- `_select_runner()` cascades when current downgrade is exhausted
- YAML example:
```yaml
downgrade_map:
  reviewer:
    - {to: hermes, model: qwen3.7-plus}
    - {to: hermes, model: MiniMax-M3}
```

## Part 3: Snapshot/Restore on Tier Switch
- Before tier switch: snapshot workspace via SnapshotManager
- On tier switch failure: restore from snapshot
- Track snapshots in `_tier_snapshot_ids: dict[str, list[str]]`
- Integrate with existing RiskEngine post-invoke evaluation

## Acceptance Criteria
- tier_upgrade config parsed from YAML
- Multi-hop downgrade cascades correctly
- Snapshot taken before tier switch, restored on failure
- pytest passes: 1284 baseline maintained
