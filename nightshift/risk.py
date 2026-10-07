"""Risk rails — the part of the agent that is allowed to say no.

Three hard rules, enforced in code rather than in prose:

1. **Never risk more than ``risk_pct`` of equity per trade.** Size is derived
   from the stop distance, never the other way round. A wider stop buys a
   smaller position; it never buys a bigger loss.
2. **Never place a position whose stop sits outside its liquidation price.**
   On an isolated position the stop is the plan and liquidation is the
   accident; if the accident would arrive first, the trade is refused.
3. **Never leave a naked position.** If the exchange rejects the attached
   stop, the entry is closed immediately (see ``agent.py``).

Sizing, rails and the liquidation sanity check all live here so they can be
unit-tested without touching an exchange.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class RiskRefusal(RuntimeError):
    """Raised when a trade violates a rail. Refusal is a feature."""


@dataclass
class Plan:
    symbol: str
    side: str            # "buy" | "sell"
    hold_side: str       # "long" | "short"
    size: float
    entry: float
    stop: float
    target: float
    leverage: int
    margin_usd: float
    risk_usd: float
    reward_usd: float
    rr: float
    notes: list[str]

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "side": self.side, "holdSide": self.hold_side,
            "size": self.size, "entry": round(self.entry, 4), "stop": round(self.stop, 4),
            "target": round(self.target, 4), "leverage": self.leverage,
            "marginUsd": round(self.margin_usd, 3), "riskUsd": round(self.risk_usd, 3),
            "rewardUsd": round(self.reward_usd, 3), "rr": round(self.rr, 2),
            "notes": self.notes,
        }


def round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def position_size(equity: float, risk_pct: float, entry: float, stop: float,
                  *, min_size: float = 0.0, size_step: float = 0.0,
                  confidence: float = 1.0) -> float:
    """Contracts such that |entry - stop| costs exactly ``risk_pct`` of equity."""
    distance = abs(entry - stop)
    if distance <= 0 or equity <= 0:
        return 0.0
    risk_usd = equity * (risk_pct / 100.0) * max(0.1, min(1.0, confidence))
    size = risk_usd / distance
    if size_step > 0:
        size = round_step(size, size_step)
    return max(0.0, size)


def liquidation_price(entry: float, leverage: int, hold_side: str,
                      maintenance_margin_rate: float = 0.005) -> float:
    """Approximate isolated liquidation for a 1x-collateral position.

    Bitget's exact formula also nets fees and the maintenance-margin tier; the
    approximation is deliberately conservative (slightly closer to entry) so a
    plan that passes here also passes on the venue.
    """
    adverse = entry * (1.0 / leverage + maintenance_margin_rate)
    if hold_side == "long":
        return entry - adverse
    return entry + adverse


def build_plan(symbol: str, action: str, entry: float, stop: float, target: float,
               equity: float, *, leverage: int = 50, risk_pct: float = 1.0,
               min_size: float = 0.0, size_step: float = 0.0, price_place: int = 2,
               confidence: float = 1.0, max_margin_pct: float = 40.0,
               min_rr: float = 1.5) -> Plan:
    """Turn a directional idea into an executable, rail-checked order plan."""
    if action not in ("long", "short"):
        raise RiskRefusal(f"action {action!r} is not tradeable")
    notes: list[str] = []
    hold_side = action
    side = "buy" if action == "long" else "sell"

    # A stop on the wrong side of entry is a typo, not a strategy.
    if action == "long" and stop >= entry:
        raise RiskRefusal(f"long stop {stop} must sit below entry {entry}")
    if action == "short" and stop <= entry:
        raise RiskRefusal(f"short stop {stop} must sit above entry {entry}")

    size = position_size(equity, risk_pct, entry, stop, min_size=min_size,
                         size_step=size_step, confidence=confidence)
    if size <= 0:
        raise RiskRefusal("computed size is zero — widen risk or tighten the stop")
    if min_size and size < min_size:
        raise RiskRefusal(f"size {size} below exchange minimum {min_size} — equity too small for this stop")

    risk_usd = size * abs(entry - stop)
    reward_usd = size * abs(target - entry)
    rr = reward_usd / risk_usd if risk_usd else 0.0

    notional = size * entry
    margin = notional / leverage
    if margin > equity * (max_margin_pct / 100.0):
        raise RiskRefusal(f"margin {margin:.2f} USDT exceeds {max_margin_pct}% of equity {equity:.2f}")

    liq = liquidation_price(entry, leverage, hold_side)
    if hold_side == "long" and stop <= liq:
        raise RiskRefusal(f"stop {stop} is at/below liquidation {liq:.2f} — the accident arrives first")
    if hold_side == "short" and stop >= liq:
        raise RiskRefusal(f"stop {stop} is at/above liquidation {liq:.2f} — the accident arrives first")
    notes.append(f"liq {liq:.2f} is {abs(liq - stop) / entry * 100:.2f}% beyond the stop")

    if rr < min_rr:
        raise RiskRefusal(f"reward:risk {rr:.2f} < required {min_rr}")

    return Plan(symbol=symbol, side=side, hold_side=hold_side, size=size,
                entry=round(entry, price_place), stop=round(stop, price_place),
                target=round(target, price_place), leverage=leverage,
                margin_usd=margin, risk_usd=risk_usd, reward_usd=reward_usd, rr=rr,
                notes=notes)


def protective_rails(entry: float, stop: float, target: float, atr_value: float,
                     *, direction: str | None = None, min_stop_pct: float = 0.10,
                     max_stop_pct: float = 1.20, rr: float = 2.0) -> tuple[float, float]:
    """Fill in whichever rail the signal did not supply, sized from ATR.

    Direction is resolved explicitly (or inferred from any rail already given)
    *before* any price is computed — deriving it from a zero-valued rail is how
    a long silently becomes a short.

    Stops narrower than the noise are guaranteed to be hit by the spread; stops
    wider than ``max_stop_pct`` turn a scalp into a hold.
    """
    if direction is None:
        if stop and stop < entry:
            direction = "long"
        elif stop and stop > entry:
            direction = "short"
        elif target and target > entry:
            direction = "long"
        elif target and target < entry:
            direction = "short"
        else:
            direction = "long"
    if direction not in ("long", "short"):
        raise RiskRefusal(f"direction {direction!r} is not long/short")

    pct = min(max_stop_pct, max(min_stop_pct, atr_value / entry * 100.0 * 1.5)) if entry else min_stop_pct
    if not stop:
        stop = entry * (1 - pct / 100.0) if direction == "long" else entry * (1 + pct / 100.0)
    if not target:
        distance = abs(entry - stop) * rr
        target = entry + distance if direction == "long" else entry - distance
    return stop, target
