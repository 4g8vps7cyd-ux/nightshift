"""Performance metrics — the numbers the judges actually score.

Sharpe, max drawdown and win rate are only meaningful if the convention behind
them is stated, so every function here documents its assumptions and the CLI
prints them alongside the result. A Sharpe without a stated period is a rumour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def sharpe(returns: list[float], periods_per_year: float) -> float:
    """Annualised Sharpe of *per-trade* returns on a risk-free rate of 0.

    ``periods_per_year`` converts the sampling frequency to a year, so the
    number is comparable across runs. With fewer than 2 observations there is no
    dispersion to divide by and the function returns 0.0 rather than infinity —
    an unmeasurable Sharpe is not a good Sharpe.
    """
    if len(returns) < 2:
        return 0.0
    sd = stdev(returns)
    if sd == 0:
        return 0.0
    return mean(returns) / sd * math.sqrt(periods_per_year)


def sortino(returns: list[float], periods_per_year: float) -> float:
    """Like Sharpe but penalising only downside deviation."""
    if len(returns) < 2:
        return 0.0
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    dd = (sum(r * r for r in downside) / len(downside)) ** 0.5
    if dd == 0:
        return 0.0
    return mean(returns) / dd * math.sqrt(periods_per_year)


def max_drawdown(equity_curve: list[float]) -> tuple[float, int, int]:
    """Largest peak-to-trough decline. Returns (fraction, peak_idx, trough_idx).

    Returned as a positive fraction: 0.12 means the account fell 12% from a peak.
    """
    if not equity_curve:
        return 0.0, 0, 0
    peak = equity_curve[0]
    peak_idx = trough_idx = 0
    worst = 0.0
    best_peak_idx = 0
    for i, value in enumerate(equity_curve):
        if value > peak:
            peak, peak_idx = value, i
        if peak > 0:
            dd = (peak - value) / peak
            if dd > worst:
                worst, best_peak_idx, trough_idx = dd, peak_idx, i
    return worst, best_peak_idx, trough_idx


def profit_factor(pnls: list[float]) -> float:
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


@dataclass
class Report:
    trades: int
    wins: int
    losses: int
    win_rate: float
    expectancy: float
    avg_win: float
    avg_loss: float
    payoff: float
    profit_factor: float
    sharpe: float
    sortino: float
    max_dd: float
    total_return: float
    final_equity: float
    fees_paid: float
    periods_per_year: float

    def as_dict(self) -> dict:
        def clean(x: float) -> float | str:
            if isinstance(x, float) and math.isinf(x):
                return "inf"
            return round(x, 4) if isinstance(x, float) else x

        return {
            "trades": self.trades, "wins": self.wins, "losses": self.losses,
            "winRate": round(self.win_rate, 4), "expectancy": round(self.expectancy, 5),
            "avgWin": round(self.avg_win, 4), "avgLoss": round(self.avg_loss, 4),
            "payoffRatio": clean(self.payoff), "profitFactor": clean(self.profit_factor),
            "sharpe": round(self.sharpe, 3), "sortino": round(self.sortino, 3),
            "maxDrawdown": round(self.max_dd, 4),
            "totalReturn": round(self.total_return, 4),
            "finalEquity": round(self.final_equity, 4),
            "feesPaid": round(self.fees_paid, 4),
            "periodsPerYear": round(self.periods_per_year, 2),
        }


def report(pnls: list[float], equity_curve: list[float], *, start_equity: float,
           periods_per_year: float, fees_paid: float = 0.0) -> Report:
    """Full performance report from a trade list plus its equity curve.

    ``periods_per_year`` must be derived from the run itself (trades/day × 365),
    never assumed — that is what makes the Sharpe comparable between the 15m and
    1H runs in ``docs/paper/``.
    """
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)
    win_rate = len(wins) / n if n else 0.0
    avg_win = mean(wins)
    avg_loss = abs(mean(losses))
    returns = [p / start_equity for p in pnls]
    dd, _, _ = max_drawdown(equity_curve)
    final = equity_curve[-1] if equity_curve else start_equity
    return Report(
        trades=n, wins=len(wins), losses=len(losses), win_rate=win_rate,
        expectancy=mean(pnls), avg_win=avg_win, avg_loss=avg_loss,
        payoff=(avg_win / avg_loss) if avg_loss else float("inf") if avg_win else 0.0,
        profit_factor=profit_factor(pnls),
        sharpe=sharpe(returns, periods_per_year),
        sortino=sortino(returns, periods_per_year),
        max_dd=dd, total_return=(final - start_equity) / start_equity if start_equity else 0.0,
        final_equity=final, fees_paid=fees_paid, periods_per_year=periods_per_year,
    )