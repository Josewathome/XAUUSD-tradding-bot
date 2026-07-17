#!/bin/bash
# Daily/weekly report — invoked by host cron. Usage: run_report.sh daily|weekly
cd "$(dirname "$0")/.."
# refresh broker-truth dump first (tolerate failure; reportgen flags staleness)
docker exec -u abc xauusd-bot bash -lc 'export WINEPREFIX=/config/.wine HOME=/config; timeout 100 wine python /bot/research/forward_test/lb_forward.py --audit' >/dev/null 2>&1 || true
box_theory/.venv/bin/python forward_test/lb_reportgen.py "${1:-status}"
