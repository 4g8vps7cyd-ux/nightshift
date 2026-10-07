"""Signal engines — deliberately small, named, and falsifiable.

Each engine answers one question and returns a :class:`Signal`. Nothing here
places an order; the agent loop decides. Keeping detection separate from
execution is what makes the strategies reviewable and back-testable.

Engines
-------
``momentum``    break of the recent range in the direction of the drift
``reversion``   fade of a stretch away from the mean (z-score)
``rsi``         RSI extremes confirmed by a reversal candle
``exhaustion``  blow-off spike (>= X% in <= N minutes) then fade the retrace
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Signal:
    symbol: str
    engine: str
    action: str          # "long" | "short" | "flat"
    confidence: float    # 0..1, used for sizing, never for skipping rails
    reason: str
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "engine": self.engine, "action": self.action,
                "confidence": round(self.confidence, 2), "reason": self.reason,
                "entry": self.entry, "stop": self.stop, "target": self.target}


def closes(candles: list[list[float]]) -> list[float]:
    return [c[4] for c in candles]


def rsi(candles: list[list[float]], period: int = 14) -> float:
    """Wilder's RSI. Returns 50.0 when there is not enough history."""
    series = closes(candles)
    if len(series) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = series[i] - series[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(series)):
        delta = series[i] - series[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles: list[list[float]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        _, _, high, low, _, _ = candles[i]
        prev_close = candles[i - 1][4]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    window = trs[-period:]
    return sum(window) / len(window)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def momentum(candles: list[list[float]], *, lookback: int = 24, atr_mult: float = 1.0,
             symbol: str = "") -> Signal:
    series = closes(candles)
    if len(series) < lookback + 2:
        return Signal(symbol, "momentum", "flat", 0.0, "not enough history")
    window = series[-(lookback + 1):-1]
    last = series[-1]
    hi, lo = max(window), min(window)
    vol = atr(candles)
    if last > hi:
        stop = max(lo, last - atr_mult * vol)
        return Signal(symbol, "momentum", "long", 0.6,
                      f"close {last:.2f} broke {lookback}-bar high {hi:.2f}", last, stop, 0.0)
    if last < lo:
        stop = min(hi, last + atr_mult * vol)
        return Signal(symbol, "momentum", "short", 0.6,
                      f"close {last:.2f} broke {lookback}-bar low {lo:.2f}", last, stop, 0.0)
    return Signal(symbol, "momentum", "flat", 0.0, f"inside {lo:.2f}-{hi:.2f} range")


def reversion(candles: list[list[float]], *, lookback: int = 48, z_entry: float = 2.0,
              symbol: str = "") -> Signal:
    series = closes(candles)
    if len(series) < lookback + 1:
        return Signal(symbol, "reversion", "flat", 0.0, "not enough history")
    window = series[-lookback:]
    m, sd = mean(window), stdev(window)
    if sd == 0:
        return Signal(symbol, "reversion", "flat", 0.0, "flat window")
    z = (series[-1] - m) / sd
    if z <= -z_entry:
        return Signal(symbol, "reversion", "long", min(0.9, abs(z) / 4),
                      f"z-score {z:.2f} below the mean — stretched down", series[-1], 0.0, m)
    if z >= z_entry:
        return Signal(symbol, "reversion", "short", min(0.9, abs(z) / 4),
                      f"z-score {z:.2f} above the mean — stretched up", series[-1], 0.0, m)
    return Signal(symbol, "reversion", "flat", 0.0, f"z-score {z:.2f} inside the band")


def rsi_signal(candles: list[list[float]], *, period: int = 14, overbought: float = 75.0,
               oversold: float = 25.0, symbol: str = "") -> Signal:
    """RSI extreme **plus** a reversal candle — the confirmation is not optional."""
    value = rsi(candles, period)
    if len(candles) < 3:
        return Signal(symbol, "rsi", "flat", 0.0, "not enough history")
    prev, last = candles[-2], candles[-1]
    close = last[4]
    if value <= oversold and close > prev[4]:
        return Signal(symbol, "rsi", "long", 0.55 + (oversold - value) / 100.0,
                      f"RSI {value:.1f} oversold + bullish close", close, last[3], 0.0)
    if value >= overbought and close < prev[4]:
        return Signal(symbol, "rsi", "short", 0.55 + (value - overbought) / 100.0,
                      f"RSI {value:.1f} overbought + bearish close", close, last[2], 0.0)
    return Signal(symbol, "rsi", "flat", 0.0, f"RSI {value:.1f} — no extreme with confirmation")


def exhaustion(candles: list[list[float]], *, spike_pct: float = 0.8, bars: int = 5,
               symbol: str = "") -> Signal:
    """Fade a vertical move once it is visibly stretched."""
    if len(candles) < bars + 2:
        return Signal(symbol, "exhaustion", "flat", 0.0, "not enough history")
    window = candles[-(bars + 1):]
    start, end = window[0][1], window[-1][4]
    move = (end - start) / start * 100.0
    last = candles[-1]
    if move >= spike_pct:
        return Signal(symbol, "exhaustion", "short", min(0.85, move / 3),
                      f"spike +{move:.2f}% in {bars} bars — fade", end, last[2], start)
    if move <= -spike_pct:
        return Signal(symbol, "exhaustion", "long", min(0.85, abs(move) / 3),
                      f"spike {move:.2f}% in {bars} bars — fade", end, last[3], start)
    return Signal(symbol, "exhaustion", "flat", 0.0, f"no spike ({move:+.2f}% in {bars} bars)")


ENGINES = {
    "momentum": momentum,
    "reversion": reversion,
    "rsi": rsi_signal,
    "exhaustion": exhaustion,
}


def evaluate(candles: list[list[float]], symbol: str = "", engine: str = "momentum") -> Signal:
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; known: {sorted(ENGINES)}")
    return ENGINES[engine](candles, symbol=symbol)


def evaluate_all(candles: list[list[float]], symbol: str = "") -> list[Signal]:
    return [fn(candles, symbol=symbol) for fn in ENGINES.values()]
