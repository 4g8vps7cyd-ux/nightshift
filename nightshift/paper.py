"""Walk-forward paper trading — the validation material the judges score.

The Agentic Trading track requires a **paper trading log actually produced
during the competition window**, plus the quantitative half of the score
(Sharpe, max drawdown, win rate). This module produces both from historical
candles fetched through the same client the live agent uses:

* **No lookahead.** At bar *i* the decision sees ``candles[:i+1]`` only. The
  fill happens at the *open of bar i+1*, which is when a market order sent at
  the close of bar *i* would actually trade.
* **Stops are checked before targets inside the same bar.** When a bar's range
  contains both levels, the pessimistic ordering is used — this is the single
  most common way a simulator lies to its author.
* **Fees are charged** on both legs at the taker rate. A strategy that only
  works fee-free does not work.
* **Refusals are counted.** Trades the risk rails rejected are reported next to
  the ones taken, because a rail that never fires is decoration.
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field
from typing import Any

from .metrics import Report, report as build_report
from .regime import screen
from .risk import RiskRefusal, build_plan
from .signals import Signal, atr, evaluate

TAKER_FEE = 0.0006  # Bitget USDT-M taker, per side


@dataclass
class PaperTrade:
    symbol: str
    engine: str
    hold_side: str
    size: float
    entry_ts: float
    entry: float
    stop: float
    target: float
    exit_ts: float
    exit: float
    exit_reason: str
    pnl: float
    fees: float
    bars_held: int
    equity_after: float
    er_at_entry: float
    reason_at_entry: str

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "engine": self.engine, "holdSide": self.hold_side,
            "size": round(self.size, 6),
            "entryTime": _iso(self.entry_ts), "entry": round(self.entry, 4),
            "stop": round(self.stop, 4), "target": round(self.target, 4),
            "exitTime": _iso(self.exit_ts), "exit": round(self.exit, 4),
            "exitReason": self.exit_reason, "barsHeld": self.bars_held,
            "pnl": round(self.pnl, 5), "fees": round(self.fees, 5),
            "equityAfter": round(self.equity_after, 4),
            "erAtEntry": round(self.er_at_entry, 3),
            "decision": self.reason_at_entry,
        }


@dataclass
class PaperResult:
    symbol: str
    timeframe: str
    bars: int
    first_ts: float
    last_ts: float
    trades: list[PaperTrade] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    skipped_chop: int = 0
    skipped_flat: int = 0
    equity_curve: list[float] = field(default_factory=list)
    fees_paid: float = 0.0

    @property
    def pnls(self) -> list[float]:
        return [t.pnl for t in self.trades]

    def metrics(self, start_equity: float) -> Report:
        """Per-trade returns annualised on the run's own trade frequency.

        Using trades/day × 365 (rather than assuming daily returns) keeps the
        Sharpe comparable between the 15m and 1H runs printed side by side in
        ``docs/paper/``.
        """
        span_days = max((self.last_ts - self.first_ts) / 86_400_000, 1e-9)
        trades_per_day = len(self.trades) / span_days
        ppy = max(trades_per_day * 365.0, 1.0)
        return build_report(self.pnls, self.equity_curve or [start_equity], start_equity=start_equity,
                            periods_per_year=ppy, fees_paid=self.fees_paid)

    def as_dict(self, start_equity: float) -> dict:
        return {
            "symbol": self.symbol, "timeframe": self.timeframe, "bars": self.bars,
            "from": _iso(self.first_ts), "to": _iso(self.last_ts),
            "startEquity": start_equity,
            "trades": len(self.trades), "refusals": len(self.refusals),
            "skippedChop": self.skipped_chop, "skippedFlat": self.skipped_flat,
            "metrics": self.metrics(start_equity).as_dict(),
            "log": [t.as_dict() for t in self.trades],
            "refusalLog": self.refusals[:50],
        }


def _iso(ms: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def simulate_symbol(symbol: str, candles: list[list[float]], *, engine: str = "momentum",
                    timeframe: str = "15m", start_equity: float = 1000.0,
                    risk_pct: float = 1.0, leverage: int = 50, er_floor: float = 0.10,
                    warmup: int = 120, max_hold: int = 96, fee_rate: float = TAKER_FEE,
                    min_rr: float = 1.5, size_step: float = 0.0, min_size: float = 0.0,
                    max_margin_pct: float = 40.0, window_bars: int = 240,
                    min_stop_pct: float = 0.0, max_fee_ratio: float = 0.30) -> PaperResult:
    """Walk the candles once, deciding with only the information available then.

    ``window_bars`` bounds what the decision may look at (default 240 bars, the
    same scale the live agent fetches). Two reasons: an agent in production uses
    a bounded lookback, and screening the entire history at every step makes the
    run quadratic — six thousand bars would take longer to simulate than to
    trade for real.
    """
    if len(candles) < warmup + 5:
        raise ValueError(f"{symbol}: need at least {warmup + 5} bars, got {len(candles)}")

    result = PaperResult(symbol=symbol, timeframe=timeframe, bars=len(candles),
                         first_ts=candles[warmup][0], last_ts=candles[-1][0])
    equity = start_equity
    result.equity_curve.append(equity)

    i = warmup
    while i < len(candles) - 1:
        window = candles[max(0, i + 1 - window_bars): i + 1]   # strictly the past
        regime = screen(symbol, window, er_floor=er_floor)
        if not regime.tradeable:
            result.skipped_chop += 1
            i += 1
            continue

        signal = evaluate(window, symbol=symbol, engine=engine)
        if signal.action not in ("long", "short"):
            result.skipped_flat += 1
            i += 1
            continue

        entry_bar = candles[i + 1]
        entry = entry_bar[1]                   # next open: when the order would fill
        stop, target = signal.stop, signal.target
        if not stop:
            stop = entry - atr(window) * 1.5 if signal.action == "long" else entry + atr(window) * 1.5
        if not target:
            distance = abs(entry - stop) * 2.0
            target = entry + distance if signal.action == "long" else entry - distance

        try:
            plan = build_plan(symbol, signal.action, entry, stop, target, equity,
                              leverage=leverage, risk_pct=risk_pct, min_size=min_size,
                              size_step=size_step, confidence=signal.confidence,
                              min_rr=min_rr, max_margin_pct=max_margin_pct,
                              min_stop_pct=min_stop_pct, fee_rate=fee_rate,
                              max_fee_ratio=max_fee_ratio)
        except RiskRefusal as refusal:
            result.refusals.append({"time": _iso(entry_bar[0]), "side": signal.action,
                                    "entry": round(entry, 4),
                                    "why": str(refusal), "decision": signal.reason})
            i += 2                             # step past the would-be entry bar
            continue

        exit_price, exit_reason, exit_idx = _walk_exit(candles, i + 1, plan.hold_side,
                                                       plan.stop, plan.target, max_hold)
        notional = plan.size * entry
        fees = notional * fee_rate * 2.0
        direction = 1.0 if plan.hold_side == "long" else -1.0
        pnl = direction * (exit_price - entry) * plan.size - fees
        equity += pnl
        result.fees_paid += fees
        result.equity_curve.append(equity)
        result.trades.append(PaperTrade(
            symbol=symbol, engine=engine, hold_side=plan.hold_side, size=plan.size,
            entry_ts=entry_bar[0], entry=entry, stop=plan.stop, target=plan.target,
            exit_ts=candles[exit_idx][0], exit=exit_price, exit_reason=exit_reason,
            pnl=pnl, fees=fees, bars_held=exit_idx - (i + 1), equity_after=equity,
            er_at_entry=regime.er, reason_at_entry=f"{signal.reason} | {regime.reason}",
        ))
        i = exit_idx + 1                       # one position at a time
    return result


def _walk_exit(candles: list[list[float]], entry_idx: int, hold_side: str,
               stop: float, target: float, max_hold: int) -> tuple[float, str, int]:
    """Find the first level touched after entry. Stop wins ties inside one bar."""
    last_idx = min(entry_idx + max_hold, len(candles) - 1)
    for idx in range(entry_idx, last_idx + 1):
        _, _, high, low, close, _ = candles[idx]
        if hold_side == "long":
            hit_stop, hit_target = low <= stop, high >= target
        else:
            hit_stop, hit_target = high >= stop, low <= target
        if hit_stop:                            # pessimistic ordering on purpose
            return stop, "stop", idx
        if hit_target:
            return target, "target", idx
    return candles[last_idx][4], "time", last_idx


def run_paper(candles_by_symbol: dict[str, list[list[float]]], *, engine: str = "momentum",
              timeframe: str = "15m", start_equity: float = 1000.0,
              window_bars: int = 240, **kwargs: Any) -> dict[str, Any]:
    """Simulate every symbol independently, then aggregate for the headline panel.

    Per-symbol equity keeps each symbol's compounding honest; the aggregate is
    computed on the pooled trade list so the win rate and Sharpe reflect the
    whole portfolio of decisions rather than an average of averages.
    """
    results: list[PaperResult] = []
    for symbol, candles in candles_by_symbol.items():
        try:
            results.append(simulate_symbol(symbol, candles, engine=engine, timeframe=timeframe,
                                           start_equity=start_equity,
                                           window_bars=window_bars, **kwargs))
        except ValueError as exc:
            results.append(PaperResult(symbol=symbol, timeframe=timeframe, bars=len(candles),
                                       first_ts=candles[0][0] if candles else 0.0,
                                       last_ts=candles[-1][0] if candles else 0.0,
                                       refusals=[{"why": str(exc)}]))
    all_trades = [t for r in results for t in r.trades]
    all_pnls = [t.pnl for t in all_trades]
    # Each symbol ran its own account at ``start_equity``, so the pooled panel's
    # starting capital is the sum of them. Using the per-symbol figure here would
    # divide a four-account PnL by one account's capital and print a total return
    # that contradicts the equity curve — a quiet, embarrassing arithmetic bug.
    accounts = max(len(results), 1)
    pooled_start = start_equity * accounts
    pooled_equity = [pooled_start]
    for pnl in all_pnls:
        pooled_equity.append(pooled_equity[-1] + pnl)
    spans = [(r.last_ts - r.first_ts) / 86_400_000 for r in results if r.last_ts > r.first_ts]
    span_days = max(spans) if spans else 1.0
    trades_per_day = len(all_trades) / max(span_days, 1e-9)
    agg = build_report(all_pnls, pooled_equity, start_equity=pooled_start,
                       periods_per_year=max(trades_per_day * 365.0, 1.0),
                       fees_paid=sum(r.fees_paid for r in results))
    return {
        "engine": engine, "timeframe": timeframe,
        "window": {
            "from": _iso(min((r.first_ts for r in results), default=0)),
            "to": _iso(max((r.last_ts for r in results), default=0)),
            "days": round(span_days, 2),
        },
        "convention": {
            "accounts": accounts,
            "startEquityPerSymbol": start_equity,
            "pooledStartEquity": pooled_start,
            "note": ("per-symbol metrics use their own start equity; the aggregate "
                     "treats the panel as that many accounts and reports pooled returns, "
                     "which is why its Sharpe is smaller than any single symbol's"),
        },
        "aggregate": agg.as_dict(),
        "perSymbol": {r.symbol: r.as_dict(start_equity) for r in results},
        "totals": {
            "trades": len(all_trades),
            "refusals": sum(len(r.refusals) for r in results),
            "skippedChop": sum(r.skipped_chop for r in results),
            "skippedFlat": sum(r.skipped_flat for r in results),
        },
    }


def align_topics(candles_by_symbol: dict[str, list[list[float]]]) -> dict[str, list[list[float]]]:
    """Trim every series to the timestamps all of them share.

    Comparing symbols over different windows is how a backtest quietly becomes a
    cherry-pick; the panel must be measured on the same clock.
    """
    if not candles_by_symbol:
        return {}
    common: set[float] | None = None
    for candles in candles_by_symbol.values():
        stamps = {c[0] for c in candles}
        common = stamps if common is None else (common & stamps)
    keep = sorted(common or set())
    if not keep:
        return dict(candles_by_symbol)
    out = {}
    for symbol, candles in candles_by_symbol.items():
        index = {c[0]: c for c in candles}
        out[symbol] = [index[t] for t in keep if t in index]
    return out


def slice_window(candles: list[list[float]], now_ms: float) -> list[list[float]]:
    """Helper for callers that want a prefix by timestamp (kept for symmetry)."""
    stamps = [c[0] for c in candles]
    return candles[: bisect.bisect_right(stamps, now_ms)]