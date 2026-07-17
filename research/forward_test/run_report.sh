#!/bin/bash
# Daily/weekly report — invoked by host cron. Usage: run_report.sh daily|weekly
cd "$(dirname "$0")/.."
box_theory/.venv/bin/python forward_test/lb_reportgen.py "${1:-status}"
