# MoA Analyzer — P10: Observer Intelligence + Notification Overhaul

You are one of 3 analysts designing Observer upgrades for Unison.

## Current State

Observer watches state.json + notifications.jsonl, only detects stalls (>300s no activity). It does NOT:
- Know user intent
- Read agent outputs
- Intervene when pipelines loop without converging
- Send structured, human-readable notifications
- Support language configuration (user wants Chinese)

## Design Tasks

Design these capabilities:

1. **Structured notifications** — 6 event types (pipeline_start, phase_done, pipeline_done, stalled, intervention, halted). Each with pipeline name, phase, iteration, verdict, summary.

2. **Language configuration** — `observer_language` in PipelineSpec (default "en"). Chinese message templates.

3. **Intervention: SKIP detection** — After 3+ consecutive REQUEST_CHANGES, Observer checks if current output minimally satisfies user needs. If yes → write `.unison/SKIP`. Orchestrator already has `_skip_requested` flag.

4. **Intervention: REDIRECT** (design only — implementation deferred to P11) — When output does NOT satisfy user needs, write `.unison/REDIRECT` with corrective instructions injected into next agent prompt.

5. **Feishu cron script** — Adapt to new structured notification format. Join existing `unison-observer-feishu.py`.

## Architecture Reference

- `src/unison/observer.py` — Observer class (993 lines). `run()` → `_run_loop()` with file watcher + event bus.
- `src/unison/observer.py:32` — `Notification` dataclass (needs extension)
- `src/unison/orchestrator.py:1357` — `_skip_requested` flag (dashboard skip)
- `src/unison/interfaces.py` — PipelineSpec (add observer_language)
- `~/.hermes/scripts/unison-observer-feishu.py` — Feishu cron (64 lines)

Output to `reviews/moa-{your-role}-round1.md`.
