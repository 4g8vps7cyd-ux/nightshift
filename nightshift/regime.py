"""Regime screening: decide *whether* a market is worth trading before asking *how*.

The single most expensive mistake an autonomous agent can make is to be
competent at trading a market that does not pay. Measured across Bitget USDT-M
perpetuals at 15m/1H, intraday efficiency ratios sit between 0.00 and 0.06 for
XAU, BTC, ETH, SOL and XAG — i.e. pure chop, where every directional strategy
donates its edge to the fee schedule.

So the agent screens first:

* **Efficiency Ratio (ER)** — |net move| / |total path|. Near 0 = chop,
  near 1 = trend. Kaufman's classic, used here as a hard gate.
* **Level quality** — for each candidate support/resistance, how often price
  touched it, how often it broke, and how far it bounced. A level that has been
  touched 11 times and broken 0 times is tradeable structure; one that has been
  touched once is a rumour.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Regime:
    symbol: str
    er: float
    range_pct: float
    trend: str            # "up" | "down" | "flat"
    tradeable: bool
    reason: str
    levels: list["Level"] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "er": round(self.er, 3),
            "range_pct": round(self.range_pct, 2), "trend": self.trend,
            "tradeable": self.tradeable, "reason": self.reason,
            "levels": [lv.as_dict() for lv in self.levels],
        }


@dataclass
class Level:
    price: float
    kind: str          # "support" | "resistance"
    touches: int
    breaks: int
    bounce_pct: float

    def as_dict(self) -> dict:
        return {"price": round(self.price, 2), "kind": self.kind, "touches": self.touches,
                "breaks": self.breaks, "bounce_pct": round(self.bounce_pct, 3)}


def closes(candles: list[list[float]]) -> list[float]:
    return [c[4] for c in candles]


def efficiency_ratio(candles: list[list[float]], lookback: int = 24) -> float:
    """Kaufman ER over the last ``lookback`` bars. 0 = chop, 1 = clean trend."""
    series = closes(candles)
    if len(series) < lookback + 1:
        return 0.0
    window = series[-(lookback + 1):]
    net = abs(window[-1] - window[0])
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    return 0.0 if path == 0 else net / path


def range_pct(candles: list[list[float]], lookback: int = 96) -> float:
    window = candles[-lookback:]
    if not window:
        return 0.0
    hi = max(c[2] for c in window)
    lo = min(c[3] for c in window)
    return 0.0 if lo == 0 else (hi - lo) / lo * 100.0


def find_levels(candles: list[list[float]], tolerance_pct: float = 0.15,
                max_levels: int = 3) -> list[Level]:
    """Cluster swing highs/lows into candidate S/R and grade them empirically."""
    highs = [(c[0], c[2]) for c in candles]
    lows = [(c[0], c[3]) for c in candles]
    last = closes(candles)[-1]

    def cluster(points: list[tuple[float, float]], kind: str) -> list[Level]:
        out: list[Level] = []
        for _, price in points:
            for lv in out:
                if abs(lv.price - price) / lv.price * 100.0 <= tolerance_pct:
                    lv.touches += 1
                    lv.price = (lv.price + price) / 2
                    break
            else:
                out.append(Level(price=price, kind=kind, touches=1, breaks=0, bounce_pct=0.0))
        return out

    def grade(levels: list[Level]) -> list[Level]:
        graded = []
        for lv in levels:
            if lv.touches < 2:
                continue
            breaks = bounces = 0
            best_bounce = 0.0
            for c in candles:
                _, o, h, l, c_close, _ = c
                if h >= lv.price >= l and (h - l) > 0:
                    if lv.kind == "resistance":
                        beyond = (h - lv.price) / lv.price * 100.0
                        back = (lv.price - c_close) / lv.price * 100.0
                    else:
                        beyond = (lv.price - l) / lv.price * 100.0
                        back = (c_close - lv.price) / lv.price * 100.0
                    if beyond > tolerance_pct and back <= 0:
                        breaks += 1
                    else:
                        bounces += 1
                        best_bounce = max(best_bounce, back)
            lv.breaks = breaks
            lv.bounce_pct = best_bounce
            graded.append(lv)
        graded.sort(key=lambda x: (x.breaks, -x.touches))
        return graded[:max_levels]

    res = [lv for lv in grade(cluster(highs, "resistance")) if lv.price > last]
    sup = [lv for lv in grade(cluster(lows, "support")) if lv.price < last]
    return sup + res


def screen(symbol: str, candles: list[list[float]], *, er_floor: float = 0.10,
           range_floor: float = 1.5) -> Regime:
    """Gate a symbol: trend quality + enough range to pay the fees."""
    er = efficiency_ratio(candles, 24)
    rng = range_pct(candles, 96)
    series = closes(candles)
    drift = (series[-1] - series[-25]) / series[-25] * 100.0 if len(series) > 25 else 0.0
    trend = "up" if drift > 0.1 else "down" if drift < -0.1 else "flat"

    if er < er_floor:
        ok, why = False, f"chop (ER {er:.2f} < {er_floor}) — fees would eat the edge"
    elif rng < range_floor:
        ok, why = False, f"range too tight ({rng:.2f}% < {range_floor}%) — no room for a stop"
    else:
        ok, why = True, f"{trend} with structure (ER {er:.2f}, range {rng:.2f}%)"

    return Regime(symbol=symbol, er=er, range_pct=rng, trend=trend, tradeable=ok,
                  reason=why, levels=find_levels(candles))


def best_range_market(regimes: list[Regime]) -> Regime | None:
    """Pick the tightest, most-respected range: the grid/mean-reversion candidate."""
    candidates = [r for r in regimes if r.tradeable and r.levels]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.range_pct)
