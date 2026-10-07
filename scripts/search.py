"""Search the configuration space for setups that survive out-of-sample.

Each cell is run twice: on the first 70% of bars (train) and on the last 30%
(validation). Only configurations that make money on **both** halves are worth a
second look, and the table prints every cell either way — a grid that is only
shown when it wins is a marketing document.

    python -m scripts.search --pages 6 --out docs/paper/search.json
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

SYMBOLS = ["XAUUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = ["15m", "1H"]
ENGINES = ["momentum", "reversion", "rsi", "exhaustion"]
STOP_FLOORS = [0.0, 0.6, 1.2]
ER_FLOORS = [0.0, 0.10]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--credentials", default=os.environ.get("BITGET_CREDENTIALS"))
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--ratio", type=float, default=0.7)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    creds = Credentials.from_file(args.credentials) if args.credentials else Credentials.from_env()
    client = Bitget(creds)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    history: dict[str, dict[str, list[list[float]]]] = {}
    for tf in timeframes:
        history[tf] = {}
        for symbol in symbols:
            rows = client.candles_history(symbol, tf, pages=args.pages)
            history[tf][symbol] = rows
            print(f"# {symbol:>10} {tf:>4}: {len(rows)} bars", file=sys.stderr)
            time.sleep(0.2)

    results: list[dict] = []
    total = len(timeframes) * len(ENGINES) * len(STOP_FLOORS) * len(ER_FLOORS)
    done = 0
    for tf in timeframes:
        aligned = align_topics({s: r for s, r in history[tf].items() if len(r) > 300})
        if not aligned:
            continue
        cut = int(len(next(iter(aligned.values()))) * args.ratio)
        train = {s: v[:cut] for s, v in aligned.items()}
        valid = {s: v[cut:] for s, v in aligned.items()}
        for engine in ENGINES:
            for stop_pct in STOP_FLOORS:
                for er_floor in ER_FLOORS:
                    row: dict = {"timeframe": tf, "engine": engine,
                                 "minStopPct": stop_pct, "erFloor": er_floor}
                    for label, data in (("train", train), ("validation", valid)):
                        try:
                            out = run_paper(data, engine=engine, timeframe=tf,
                                            start_equity=args.equity, risk_pct=args.risk,
                                            er_floor=er_floor, min_stop_pct=stop_pct)
                        except Exception as exc:  # keep the sweep alive
                            row[label] = {"error": str(exc)[:80]}
                            continue
                        a = out["aggregate"]
                        row[label] = {
                            "trades": a["trades"], "winRate": a["winRate"], "sharpe": a["sharpe"],
                            "sortino": a["sortino"], "maxDrawdown": a["maxDrawdown"],
                            "expectancy": a["expectancy"], "profitFactor": a["profitFactor"],
                            "totalReturn": a["totalReturn"], "feesPaid": a["feesPaid"],
                            "refusals": out["totals"]["refusals"],
                            "days": out["window"]["days"],
                        }
                    t, v = row.get("train", {}), row.get("validation", {})
                    row["consistent"] = bool(t.get("trades") and v.get("trades")
                                             and t.get("expectancy", -1) > 0
                                             and v.get("expectancy", -1) > 0)
                    row["worstSharpe"] = min(t.get("sharpe", -99), v.get("sharpe", -99))
                    results.append(row)
                    done += 1
                    print(f"# [{done}/{total}] {tf} {engine} stop{stop_pct} er{er_floor} "
                          f"train exp {t.get('expectancy')} val exp {v.get('expectancy')}",
                          file=sys.stderr)

    ranked = sorted(results, key=lambda r: -r["worstSharpe"])
    header = (f"{'tf':>4} {'engine':<11} {'stop%':>6} {'ER':>5} | {'tr n':>5} {'win':>5} "
              f"{'sharpe':>7} {'exp':>8} | {'va n':>5} {'win':>5} {'sharpe':>7} {'exp':>8} | ok")
    print(header)
    print("-" * len(header))
    for r in ranked:
        t, v = r["train"], r["validation"]
        print(f"{r['timeframe']:>4} {r['engine']:<11} {r['minStopPct']:>6} {r['erFloor']:>5} | "
              f"{t['trades']:>5} {t['winRate']:>5.2f} {t['sharpe']:>7.2f} {t['expectancy']:>8.3f} | "
              f"{v['trades']:>5} {v['winRate']:>5.2f} {v['sharpe']:>7.2f} {v['expectancy']:>8.3f} | "
              f"{'YES' if r['consistent'] else ''}")

    winners = [r for r in ranked if r["consistent"]]
    print()
    print(f"# consistent configurations: {len(winners)} of {len(results)}")
    for r in winners:
        print(f"#   {r['timeframe']} {r['engine']} stop>={r['minStopPct']}% er>={r['erFloor']} "
              f"-> train Sharpe {r['train']['sharpe']}, validation Sharpe {r['validation']['sharpe']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "symbols": symbols, "pages": args.pages, "ratio": args.ratio,
                       "results": ranked, "winners": winners}, fh, indent=2)
        print(f"# wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
