#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE="${IDEASCOUT_ASSET_STORE:-/vePFS-Mindverse/user/intern/zhouch/asset_store}"
BATCH="${IDEASCOUT_HIGH_IMPACT_BATCH:-high_impact}"
SESSION="${IDEASCOUT_HIGH_IMPACT_SESSION:-ideascout_highimpact}"
SOURCES="${IDEASCOUT_HIGH_IMPACT_SOURCES:-ml,cvf,acl}"
MIN_YEAR="${IDEASCOUT_HIGH_IMPACT_MIN_YEAR:-2016}"
MAX_YEAR="${IDEASCOUT_HIGH_IMPACT_MAX_YEAR:-2025}"
PER_VENUE="${IDEASCOUT_HIGH_IMPACT_PER_VENUE:-80}"
CVF_PER_YEAR="${IDEASCOUT_HIGH_IMPACT_CVF_PER_YEAR:-80}"
ACL_PER_YEAR="${IDEASCOUT_HIGH_IMPACT_ACL_PER_YEAR:-80}"
MAX_RECORDS="${IDEASCOUT_HIGH_IMPACT_MAX_RECORDS:-0}"
TIMEOUT="${IDEASCOUT_HIGH_IMPACT_TIMEOUT:-45}"
LLM_TIMEOUT="${IDEASCOUT_HIGH_IMPACT_LLM_TIMEOUT:-180}"
REVIEW_PROVIDER="${IDEASCOUT_HIGH_IMPACT_REVIEW_PROVIDER:-codex}"
REVIEW_MODEL="${IDEASCOUT_HIGH_IMPACT_REVIEW_MODEL:-${PAPERHUB_AGENT_MODEL:-gpt-5.5}}"
OPENAI_BASE_URL_ARG="${IDEASCOUT_HIGH_IMPACT_OPENAI_BASE_URL:-${OPENAI_BASE_URL:-}}"
MODEL_COMMAND="${IDEASCOUT_HIGH_IMPACT_MODEL_COMMAND:-claude -p}"
LOG_DIR="$STORE/$BATCH/logs"
LOG_FILE="$LOG_DIR/tmux-$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$LOG_DIR"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session already exists: $SESSION"
  echo "attach: tmux attach -t $SESSION"
  exit 0
fi

printf -v CMD 'cd %q && rtk /opt/venv/bin/python scripts/high_impact_harvest.py --store %q --batch %q --sources %q --min-year %q --max-year %q --per-venue %q --cvf-per-year %q --acl-per-year %q --max-records %q --timeout %q --llm-timeout %q --review-provider %q --review-model %q --openai-base-url %q --model-command %q --delete-pdfs --rebuild-portal 2>&1 | tee -a %q' \
  "$REPO_ROOT" \
  "$STORE" \
  "$BATCH" \
  "$SOURCES" \
  "$MIN_YEAR" \
  "$MAX_YEAR" \
  "$PER_VENUE" \
  "$CVF_PER_YEAR" \
  "$ACL_PER_YEAR" \
  "$MAX_RECORDS" \
  "$TIMEOUT" \
  "$LLM_TIMEOUT" \
  "$REVIEW_PROVIDER" \
  "$REVIEW_MODEL" \
  "$OPENAI_BASE_URL_ARG" \
  "$MODEL_COMMAND" \
  "$LOG_FILE"

tmux new-session -d -s "$SESSION" "$CMD"

echo "started tmux session: $SESSION"
echo "attach: tmux attach -t $SESSION"
echo "log: $LOG_FILE"
