#!/usr/bin/env bash
# run-all-pipelines.sh — Sequential execution with state cleanup
set -euo pipefail

export PYTHONPATH="$HOME/projects/unison:$HOME/projects/unison/src"
export UNISON_DISCORD_WEBHOOK="https://discord.com/api/webhooks/1522867777894092862/HD-Sh5idmaTx9uqJpRPD2qI6L937T44baWWRViw7h9SffenMh-Dum5aUELuSOG0AU9dO"

cd ~/projects/unison

PIPELINES=(
  "p1-sse:SSE Push"
  "p2-control:Control Panel"
  "p3-split:File Split"
  "p4-generator:Pipeline Generator"
  "p5-selfheal:Lightweight Self-Heal"
  "p6-eventbus:Event Bus"
)

clean_state() {
  rm -f .unison/state.json reviews/iter-*.md 2>/dev/null
  rm -f .unison/locks/*.lock 2>/dev/null
}

for entry in "${PIPELINES[@]}"; do
  name="${entry%%:*}"
  desc="${entry##*:}"

  echo "============================================================"
  echo "  Pipeline: $name — $desc"
  echo "============================================================"

  clean_state

  if unison run --pipeline "$name.yaml" 2>&1; then
    echo "[OK] $name completed"
    # Commit if there are changes
    if ! git diff --quiet HEAD -- src/ tests/; then
      git add src/ tests/
      git commit -m "feat($name): $desc"
    fi
  else
    echo "[FAIL] $name failed — check state.json for details"
    exit 1
  fi

  echo ""
done

echo "All 6 pipelines completed successfully."
