"""Trade journal + post-trade review.

Every decision is appended as one JSON line — including the refusals. A journal
that only records winners is marketing; recording *why* a trade was skipped is
what lets the next session learn anything.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Iterable

DEFAULT_PATH = os.environ.get("NIGHTSHIFT_JOURNAL", "journal.jsonl")


def record(event: str, payload: dict[str, Any], path: str = DEFAULT_PATH) -> dict:
    row = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **payload}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return row


def read(path: str = DEFAULT_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def review(path: str = DEFAULT_PATH) -> dict:
    """Expectancy view over closed trades: the only number that matters."""
    rows = read(path)
    closed = [r for r in rows if r.get("event") == "closed"]
    wins = [r for r in closed if float(r.get("pnl", 0)) > 0]
    losses = [r for r in closed if float(r.get("pnl", 0)) <= 0]
    gross_win = sum(float(r.get("pnl", 0)) for r in wins)
    gross_loss = abs(sum(float(r.get("pnl", 0)) for r in losses))
    n = len(closed)
    win_rate = len(wins) / n if n else 0.0
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if n else 0.0
    by_engine: dict[str, dict] = {}
    for r in closed:
        key = r.get("engine", "unknown")
        bucket = by_engine.setdefault(key, {"n": 0, "pnl": 0.0, "wins": 0})
        bucket["n"] += 1
        bucket["pnl"] += float(r.get("pnl", 0))
        bucket["wins"] += 1 if float(r.get("pnl", 0)) > 0 else 0
    for bucket in by_engine.values():
        bucket["winRate"] = round(bucket["wins"] / bucket["n"], 3) if bucket["n"] else 0.0
        bucket["pnl"] = round(bucket["pnl"], 3)
    refusals = [r for r in rows if r.get("event") == "refused"]
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "winRate": round(win_rate, 3), "avgWin": round(avg_win, 3),
        "avgLoss": round(avg_loss, 3), "expectancy": round(expectancy, 4),
        "grossWin": round(gross_win, 3), "grossLoss": round(gross_loss, 3),
        "refusals": len(refusals), "byEngine": by_engine,
    }


def lessons(path: str = DEFAULT_PATH) -> list[str]:
    """Human-readable takeaways — the self-improvement half of the desk."""
    stats = review(path)
    out: list[str] = []
    if not stats["trades"]:
        return ["No closed trades yet — nothing to learn from. Run the agent."]
    out.append(f"{stats['trades']} closed trades, win rate {stats['winRate']:.0%}, "
               f"expectancy {stats['expectancy']:+.4f} USDT/trade")
    if stats["expectancy"] <= 0:
        out.append("Expectancy is not positive: the current engine is donating to fees. "
                   "Raise the ER floor or widen the reward:risk requirement before trading more.")
    for engine, bucket in sorted(stats["byEngine"].items(), key=lambda kv: -kv[1]["pnl"]):
        verdict = "keep" if bucket["pnl"] > 0 else "review"
        out.append(f"engine {engine}: {bucket['n']} trades, {bucket['winRate']:.0%} win, "
                   f"{bucket['pnl']:+.3f} USDT -> {verdict}")
    if stats["refusals"]:
        out.append(f"{stats['refusals']} trades were refused by the risk rails — "
                   "that count is the rail doing its job, not a bug")
    return out
