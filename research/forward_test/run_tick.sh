#!/bin/bash
# London Breakout forward-test tick — invoked by host cron every 5 minutes.
docker exec -u abc xauusd-bot bash -lc \
  'export WINEPREFIX=/config/.wine HOME=/config; timeout 110 wine python /bot/research/forward_test/lb_forward.py' \
  2>&1 | grep -viE "fixme|wine:|gecko|mono|err:module" || true
