"""Trade journal + post-trade review.

Every decision is appended as one JSON line — including the refusals. A journal
that only records winners is marketing; recording *why* a trade was skipped is
what lets the next session learn anything.

Each record is **hash-chained** to the one before it (``prev`` / ``hash``), so a
journal that has been edited after the fact no longer verifies. That matters the
moment real money is involved: an audit trail nobody can rewrite is the only
kind worth showing. ``verify()`` walks the chain and returns the first index that
does not add up. Records written before the chain existed are counted as
``unchained`` rather than being silently blessed or treated as tampering.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from typing import Any, Iterable

DEFAULT_PATH = os.environ.get("NIGHTSHIFT_JOURNAL", "journal.jsonl")
GENESIS = "0" * 64


def _digest(row: dict[str, Any]) -> str:
    """SHA-256 over the canonical form of a record, ignoring its own ``hash``."""
    body = {k: v for k, v in row.items() if k != "hash"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _last_hash(path: str) -> str:
    """Hash of the newest chained record, or GENESIS when the chain starts here."""
    if not os.path.exists(path):
        return GENESIS
    tail = ""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 8192))
        tail = fh.read().decode("utf-8", "ignore")
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("hash"):
            return row["hash"]
        break                      # newest row is legacy -> chain starts fresh
    return GENESIS


def record(event: str, payload: dict[str, Any], path: str = DEFAULT_PATH) -> dict:
    row = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **payload}
    row["prev"] = _last_hash(path)
    row["hash"] = _digest(row)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return row


def verify(path: str = DEFAULT_PATH) -> dict:
    """Walk the hash chain. Returns ok + where it first disagrees."""
    rows = read(path)
    prev = GENESIS
    unchained = 0
    chained = 0
    for index, row in enumerate(rows):
        if not row.get("hash"):
            unchained += 1
            prev = GENESIS          # pre-chain records carry no link to inherit
            continue
        if row.get("prev") != prev:
            return {"ok": False, "index": index, "event": row.get("event"),
                    "reason": "prev does not match the previous record's hash — "
                              "a record was inserted, removed or reordered",
                    "records": len(rows), "unchained": unchained}
        if _digest(row) != row["hash"]:
            return {"ok": False, "index": index, "event": row.get("event"),
                    "reason": "content hash mismatch — this record was edited",
                    "records": len(rows), "unchained": unchained}
        prev = row["hash"]
        chained += 1
    return {"ok": True, "records": len(rows), "chained": chained,
            "unchained": unchained, "head": prev if chained else None}


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
