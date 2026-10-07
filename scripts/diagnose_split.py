"""Reconcile a full-window run against the train / validation split of the grid.

A grid that splits the series gives each half a fresh warm-up and a fresh
position slot, so the split trade counts do not add up to the full-window count.
When the two disagree, this script prints both so the difference is explained
rather than hidden.

    python -m scripts.diagnose_split --symbols XAUUSDT,BTCUSDT --engine reversion --min-stop-pct 1.2
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nightshift.exchange import Bitget, Credentials  # noqa: E402
from nightshift.paper import align_topics, run_paper  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--credentials", default=os.environ.get("BITGET_CREDENTIALS"))
    ap.add_argument("--symbols", default="XAUUSDT,BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--engine", default="reversion")
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--min-stop-pct", type=float, default=1.2)
    ap.add_argument("--er-floor", type=float, default=0.10)
    ap.add_argument("--ratio", type=float, default=0.7)
    args = ap.parse_args()

    creds = Credentials.from_file(args.credentials) if args.credentials else Credentials.from_env()
    client = Bitget(creds)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    raw = {s: client.candles_history(s, args.timeframe, pages=args.pages) for s in symbols}
    aligned = align_topics(raw)
    print("bars per symbol:", {s: len(v) for s, v in aligned.items()})

    scenes = {
        "full window": aligned,
        f"train {args.ratio:.0%}": {s: v[: int(len(v) * args.ratio)] for s, v in aligned.items()},
        f"validation {1 - args.ratio:.0%}": {s: v[int(len(v) * args.ratio):] for s, v in aligned.items()},
    }
    print(f"{'scene':<18} {'bars':>6} {'trades':>7} {'win':>6} {'sharpe':>8} "
          f"{'exp':>9} {'fees$':>8} {'days':>6}")
    for label, data in scenes.items():
        if not data or len(next(iter(data.values()))) < 200:
            print(f"{label:<18} (too few bars)")
            continue
        out = run_paper(data, engine=args.engine, timeframe=args.timeframe,
                        start_equity=1000.0, er_floor=args.er_floor,
                        min_stop_pct=args.min_stop_pct)
        agg = out["aggregate"]
        bars = len(next(iter(data.values())))
        print(f"{label:<18} {bars:>6} {agg['trades']:>7} {agg['winRate']:>6} {agg['sharpe']:>8} "
              f"{agg['expectancy']:>9} {agg['feesPaid']:>8} {out['window']['days']:>6}")
    print()
    print("Note: split halves restart their warm-up and their position slot, so trades")
    print("near the boundary can appear in both halves or in neither. The full-window")
    print("number is the one quoted as the paper-trading result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())