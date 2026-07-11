# P12b: Conditional Model Tiering

Dynamic model tier-switching based on budget/error conditions. Three parts:

## 1. tier_upgrade YAML Config (interfaces.py)

Add `tier_upgrade` to `BudgetConfig`:

```python
tier_upgrade: dict[str, dict[str, str]] = field(default_factory=dict)
```

Pipeline YAML example:
```yaml
budget:
  tier_upgrade:
    developer:
      from: deepseek-v4-pro
      to: deepseek-v4-pro    # same model, higher effort
      reasoning_effort: xhigh
    reviewer:
      from: gpt-5.6-terra
      to: gpt-5.6-sol
      reasoning_effort: xhigh
```

## 2. Multi-hop downgrade chains (orchestrator.py)

Current `_select_runner()` supports single-hop downgrade via `downgrade_map`. Extend to support chain downgrades:

```yaml
downgrade_map:
  reviewer:
    - {to: hermes, model: qwen3.7-plus}
    - {to: hermes, model: MiniMax-M3}
```

If the first downgrade also runs out of budget, cascade to the next. The `from` field in each entry is implicit (the previous downgrade's `to`).

## 3. Snapshot/Restore on Tier Switch (orchestrator.py)

When tier switches (upgrade or downgrade), take a snapshot before the switch and restore if the new tier fails. Add `_tier_snapshot_ids` dict tracking which snapshots belong to which tier switch.

## Acceptance Criteria
- `tier_upgrade` config parsed from YAML
- `_select_runner()` returns next downgrade when current tier exhausted
- Snapshot taken before tier switch, restored on failure
- pytest passes
