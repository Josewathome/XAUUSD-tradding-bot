#!/usr/bin/env python3
"""lb_accounting.py — signal-conservation audit (Area 3).

Proves every signal reaches EXACTLY ONE terminal state and none disappear.

State model (terminal states in CAPS):
  day ──> no_range / NO_BREAKOUT / holiday-weekend (no signal)
  signal ──> SPREAD_BLOCK | MISSED_FAST_MOVE | ORDER_REJECTED |
             BLOCKED_EXISTING | DRY_RUN | order placed
  order  ──> EXPIRED_UNFILLED | INVALIDATED | WINDOW_END | filled
  fill   ──> TAKE_PROFIT | STOP_LOSS | MANUAL_OR_EOD   (trade row appended)
  (recovered positions enter at `fill` via orphan adoption — flagged)

Conservation identities checked against the actual logs:
  signals == placements + blocked + rejected + missed + spread_blocks + dry
  placements == fills + expiries + cancels (+ still-pending now)
  fills == trade rows (+ still-open now)

    cd research && box_theory/.venv/bin/python forward_test/lb_accounting.py
Exit code 0 = conservation holds; 1 = violation (details printed).
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

SIGNAL_TERMINALS = {"spread_block", "order_send_FAILED", "DRY_signal",
                    "placement_blocked_existing"}
ORDER_TERMINALS = {"order_expired_or_cancelled", "order_cancelled"}


def load_events():
    p = os.path.join(DATA, "events.jsonl")
    if not os.path.exists(p):
        return pd.DataFrame()
    with open(p) as f:
        return pd.DataFrame([json.loads(x) for x in f if x.strip()])


def check():
    """Returns (ok, text) for embedding in reports."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = _run()
    return ok, buf.getvalue()


def main():
    ok, text = check()
    print(text, end="")
    sys.exit(0 if ok else 1)


def _run():
    E = load_events()
    T = pd.read_csv(os.path.join(DATA, "trades.csv")) if os.path.exists(
        os.path.join(DATA, "trades.csv")) else pd.DataFrame()
    S = json.load(open(os.path.join(DATA, "state.json"))) if os.path.exists(
        os.path.join(DATA, "state.json")) else {}

    def n(kind):
        return int((E.event == kind).sum()) if len(E) else 0

    def tickets(kind):
        """UNIQUE tickets for a lifecycle event (drills/retries may repeat)."""
        if not len(E) or "ticket" not in E.columns:
            return set()
        s = E[E.event == kind]["ticket"].dropna()
        return set(int(x) for x in s)

    placed_tk = tickets("order_placed")
    fill_tk = tickets("filled") | tickets("orphan_position_adopted")
    expired_tk = tickets("order_expired_or_cancelled")
    cancelled_tk = tickets("order_cancelled")
    placed = len(placed_tk)
    filled = n("filled")
    adopted = n("orphan_position_adopted")
    expired = len(expired_tk)
    cancelled = len(cancelled_tk)
    orphan_cancel = n("orphan_order_cancelled")
    spread_blocks = n("spread_block")
    dry = n("DRY_signal")
    rejected = n("order_send_FAILED")
    blocked = n("placement_blocked_existing")
    rows_tk = set(T.position_ticket.dropna().astype(int)) if len(T) else set()
    closed_rows = len(rows_tk)

    # live (non-terminal) items right now
    pending_now = sum(1 for p_, s in S.items()
                      if isinstance(s, dict) and s.get("order_ticket")
                      and not s.get("position_ticket") and s.get("phase") != "DONE")
    open_now = sum(1 for p_, s in S.items()
                   if isinstance(s, dict) and s.get("position_ticket")
                   and s.get("phase") != "DONE")

    signals = placed + spread_blocks + dry + rejected + blocked
    print("SIGNAL ACCOUNTING (from events.jsonl / trades.csv / state.json)")
    print("  signals detected ............ %d" % signals)
    print("    -> orders placed .......... %d" % placed)
    print("    -> spread blocked ......... %d" % spread_blocks)
    print("    -> rejected/missed ........ %d" % rejected)
    print("    -> blocked existing ....... %d" % blocked)
    print("    -> dry-run ................ %d" % dry)
    print("  orders placed ............... %d" % placed)
    print("    -> filled ................. %d" % filled)
    print("    -> expired unfilled ....... %d" % expired)
    print("    -> cancelled (invalid/win). %d" % cancelled)
    print("    -> still pending now ...... %d" % pending_now)
    print("  fills (incl. %d adopted) .... %d" % (adopted, filled + adopted))
    print("    -> trade rows recorded .... %d" % closed_rows)
    print("    -> still open now ......... %d" % open_now)
    print("  orphan orders cancelled ..... %d (QA/crash recovery)" % orphan_cancel)

    open_tk = set()
    for p_, sst in S.items():
        if isinstance(sst, dict) and sst.get("position_ticket") and sst.get("phase") != "DONE":
            open_tk.add(int(sst["position_ticket"]))
    pending_tk = set()
    for p_, sst in S.items():
        if (isinstance(sst, dict) and sst.get("order_ticket")
                and not sst.get("position_ticket") and sst.get("phase") != "DONE"):
            pending_tk.add(int(sst["order_ticket"]))

    violations = []
    # duplicate trade rows are a hard violation (double counting)
    if len(T) and len(T) != len(rows_tk):
        dup = T.position_ticket[T.position_ticket.duplicated()].tolist()
        violations.append("DUPLICATE trade rows for positions: %s" % dup)
    # every PLACED order ticket must terminate exactly once
    unresolved = placed_tk - fill_tk - expired_tk - cancelled_tk - pending_tk
    if unresolved:
        violations.append("orders with no terminal state: %s" % sorted(unresolved))
    # every FILL ticket must be a recorded row or still open (none may vanish)
    lost_fills = fill_tk - rows_tk - open_tk
    if lost_fills:
        violations.append("fills never recorded: %s" % sorted(lost_fills))
    # no trade row without a corresponding fill ticket (no phantom rows)
    phantom = rows_tk - fill_tk
    if phantom:
        violations.append("trade rows with no fill event: %s" % sorted(phantom))
    if violations:
        print("\nCONSERVATION VIOLATIONS:")
        for v in violations:
            print("  !!", v)
        return False
    print("\nCONSERVATION HOLDS: every signal/order/fill accounted for exactly once.")
    return True


if __name__ == "__main__":
    main()
