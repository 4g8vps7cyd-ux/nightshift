"""Configuration grid with a train / validation split.

Why this script exists: any single backtest number can be produced by trying
enough settings. So the grid is run **twice over the same data** — the first 70%
of bars ("train") and the last 30% ("validation") — and the full table is printed
either way. If a configuration only wins on the training half, that is visible
here instead of being quietly shipped as the headline.

Usage (from the repository root):

    python -m scripts.grid --credentials creds.json --pages 6 \\
        --out docs/paper/grid.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nightshift.exchange import Bitget, Credentials  # noqa: E402
from nightshift.paper import align_topics, run_paper  # noqa: E402

DEFAULT_SYMBOLS = ["XAUUSDT", "BTCUSDT", "ETHUSDT"]
ENGINES = ["momentum", "reversion"]
ER_FLOORS = [0.10]
# The stop-distance floor is the variable that decides whether a strategy is
# fee-dominated: fees/risk = (entry × 2 × fee_rate) / stop_distance, so a 0.6%
# floor puts fees near 20% of risk, while no floor leaves them near 100%.
MIN_STOP_PCTS = [0.0, 0.6, 1.2]


def split(candles: list[list[float]], ratio: float = 0.7) -> tuple[list, list]:
    cut = int(len(candles) * ratio)
    return candles[:cut], candles[cut:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--credentials", default=os.environ.get("BITGET_CREDENTIALS"))
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--timeframes", default="15m,1H")
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    creds = Credentials.from_file(args.credentials) if args.credentials else Credentials.from_env()
    client = Bitget(creds)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    # Fetch once per (symbol, timeframe) — the grid is free compared to the network.
    history: dict[str, dict[str, list[list[float]]]] = {}
    for tf in timeframes:
        history[tf] = {}
        for symbol in symbols:
            rows = client.candles_history(symbol, tf, pages=args.pages)
            history[tf][symbol] = rows
            print(f"# {symbol:>10} {tf:>4}: {len(rows)} bars", file=sys.stderr)
            time.sleep(0.2)

    table: list[dict] = []
    for tf in timeframes:
        aligned = align_topics(history[tf])
        if not aligned:
            continue
        train = {s: split(rows)[0] for s, rows in aligned.items()}
        valid = {s: split(rows)[1] for s, rows in aligned.items()}
        for engine in ENGINES:
            for floor in ER_FLOORS:
                for stop_pct in MIN_STOP_PCTS:
                    row: dict = {"timeframe": tf, "engine": engine, "erFloor": floor,
                                 "minStopPct": stop_pct}
                    for label, data in (("train", train), ("validation", valid)):
                        out = run_paper(data, engine=engine, timeframe=tf,
                                        start_equity=args.equity, er_floor=floor,
                                        min_stop_pct=stop_pct)
                        agg = out["aggregate"]
                        row[label] = {
                            "trades": agg["trades"], "winRate": agg["winRate"],
                            "sharpe": agg["sharpe"], "maxDrawdown": agg["maxDrawdown"],
                            "expectancy": agg["expectancy"], "profitFactor": agg["profitFactor"],
                            "totalReturn": agg["totalReturn"], "feesPaid": agg["feesPaid"],
                            "refusals": out["totals"]["refusals"],
                            "skippedChop": out["totals"]["skippedChop"],
                            "days": out["window"]["days"],
                        }
                    row["consistent"] = (row["train"]["expectancy"] > 0
                                         and row["validation"]["expectancy"] > 0)
                    table.append(row)

    header = (f"{'tf':>4} {'engine':<10} {'ER':>4} {'stop%':>6} | {'tr n':>5} {'win':>5} "
              f"{'sharpe':>7} {'exp':>8} {'fee$':>8} | {'va n':>5} {'win':>5} {'sharpe':>7} "
              f"{'exp':>8} | ok")
    print(header)
    print("-" * len(header))
    for r in table:
        t, v = r["train"], r["validation"]
        print(f"{r['timeframe']:>4} {r['engine']:<10} {r['erFloor']:>4} {r['minStopPct']:>6} | "
              f"{t['trades']:>5} {t['winRate']:>5.2f} {t['sharpe']:>7.2f} {t['expectancy']:>8.3f} "
              f"{t['feesPaid']:>8.1f} | "
              f"{v['trades']:>5} {v['winRate']:>5.2f} {v['sharpe']:>7.2f} {v['expectancy']:>8.3f} | "
              f"{'YES' if r['consistent'] else ''}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"symbols": symbols, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                     time.gmtime()),
                       "pages": args.pages, "equity": args.equity, "grid": table}, fh, indent=2)
        print(f"# wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())