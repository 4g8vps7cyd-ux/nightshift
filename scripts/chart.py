"""Render an equity curve from a paper-trading log as a standalone SVG.

No plotting library: the run must be reproducible on a bare VPS with nothing but
CPython, and a judge should be able to diff the output of two runs.

    python -m scripts.chart docs/paper/paper_15m_reversion.json \\
        docs/paper/equity_15m_reversion.svg
"""

from __future__ import annotations

import argparse
import json
import os
import sys

WIDTH, HEIGHT = 1000, 420
PAD_L, PAD_R, PAD_T, PAD_B = 70, 30, 40, 50
INK, GRID, LINE, PROFIT, LOSS, MUTED = ("#0d1117", "#1f2937", "#58a6ff",
                                        "#3fb950", "#f85149", "#8b949e")


def load_trades(log: dict) -> list[dict]:
    trades = [t for sym in log["perSymbol"].values() for t in sym.get("log", [])]
    trades.sort(key=lambda t: t["exitTime"])
    return trades


def build_svg(log: dict, title: str) -> str:
    trades = load_trades(log)
    start = float(log.get("convention", {}).get("pooledStartEquity")
                  or log["aggregate"].get("finalEquity", 1000.0))
    equity = [start]
    for t in trades:
        equity.append(equity[-1] + float(t["pnl"]))

    lo, hi = min(equity), max(equity)
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08

    def x(i: int) -> float:
        if len(equity) == 1:
            return PAD_L
        return PAD_L + (WIDTH - PAD_L - PAD_R) * i / (len(equity) - 1)

    def y(v: float) -> float:
        return PAD_T + (HEIGHT - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo))

    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
                 f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="ui-monospace,Menlo,monospace">')
    parts.append(f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{INK}"/>')

    # horizontal grid + labels
    for k in range(5):
        value = lo + (hi - lo) * k / 4
        gy = y(value)
        parts.append(f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{WIDTH - PAD_R}" y2="{gy:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{PAD_L - 8}" y="{gy + 4:.1f}" fill="{MUTED}" font-size="12" '
                     f'text-anchor="end">{value:,.0f}</text>')

    # start-capital reference line
    sy = y(start)
    parts.append(f'<line x1="{PAD_L}" y1="{sy:.1f}" x2="{WIDTH - PAD_R}" y2="{sy:.1f}" '
                 f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="4 4"/>')

    # equity path
    path = " ".join(("M" if i == 0 else "L") + f"{x(i):.1f},{y(v):.1f}"
                    for i, v in enumerate(equity))
    parts.append(f'<path d="{path}" fill="none" stroke="{LINE}" stroke-width="2"/>')

    # exit markers, coloured by outcome
    for i, t in enumerate(trades, start=1):
        colour = PROFIT if float(t["pnl"]) > 0 else LOSS
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(equity[i]):.1f}" r="2.6" fill="{colour}"/>')

    # header + footer stats
    agg = log["aggregate"]
    parts.append(f'<text x="{PAD_L}" y="24" fill="#e6edf3" font-size="15">{title}</text>')
    stats = (f'{len(trades)} trades · win {agg["winRate"]:.1%} · Sharpe {agg["sharpe"]:+.2f} · '
             f'max DD {agg["maxDrawdown"]:.2%} · fees {agg["feesPaid"]:.2f} USDT')
    parts.append(f'<text x="{PAD_L}" y="{HEIGHT - 14}" fill="{MUTED}" font-size="12">{stats}</text>')
    window = log.get("window", {})
    parts.append(f'<text x="{WIDTH - PAD_R}" y="{HEIGHT - 14}" fill="{MUTED}" font-size="12" '
                 f'text-anchor="end">{window.get("from", "")[:10]} → {window.get("to", "")[:10]} '
                 f'({window.get("days", 0)} days)</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", help="paper trading JSON produced by `nightshift paper --out`")
    ap.add_argument("out", help="destination .svg path")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    log = json.load(open(args.log, encoding="utf-8"))
    cfg = log.get("config", {})
    title = args.title or (f'NightShift paper equity — {cfg.get("engine", "?")} '
                           f'{cfg.get("timeframe", "?")}'
                           + (f' · stop floor {cfg["minStopPct"]}%'
                              if cfg.get("minStopPct") else ""))
    svg = build_svg(log, title)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.out} ({len(svg)} bytes, {len(load_trades(log))} trades plotted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
