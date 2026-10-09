"""The agent loop: sense -> screen -> decide -> size -> execute -> report -> review.

This is the piece the hackathon track asks for: an LLM (or, when no LLM key is
configured, a deterministic rule set) that senses the market, reasons about
what it sees, and places **risk-managed** orders on its own — including the
decision to do nothing, which is the one most trading bots never make.

Design commitments
------------------
* **Screen before you trade.** Regime first (see ``regime.py``); a symbol in
  chop is skipped no matter how loud the signal is.
* **Rails travel with the entry.** Stop and target are attached in the same
  request as the entry. If the exchange does not confirm them, the position is
  closed immediately — a naked position is treated as an incident, not a state.
* **Every decision is journaled**, refusals included.
* **Dry-run by default.** ``execute=True`` is required to touch real money, and
  even then the agent refuses to open more than ``max_positions`` at once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import journal, notify
from .exchange import Bitget, BitgetError
from .regime import screen
from .risk import Plan, RiskRefusal, build_plan, protective_rails
from .signals import Signal, atr, evaluate, evaluate_all

DEFAULT_UNIVERSE = ["XAUUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]


@dataclass
class AgentConfig:
    universe: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    timeframe: str = "15m"
    # Defaults are the configuration that survived the 48-cell train/validation
    # sweep (see docs/paper/README.md, cell #3): 15m reversion with a 1.2% stop
    # floor and the efficiency-ratio gate on. Shipping an unvalidated default is
    # how a repo quietly contradicts its own research.
    engine: str = "reversion"
    leverage: int = 50
    risk_pct: float = 1.0
    min_rr: float = 1.5
    max_positions: int = 1
    er_floor: float = 0.10
    min_stop_pct: float = 1.2      # widen stops so fees stay a minor cost
    max_fee_ratio: float = 0.30    # refuse trades whose fees exceed this share of risk
    interval_seconds: int = 300
    journal_path: str = journal.DEFAULT_PATH


class NightShiftAgent:
    def __init__(self, client: Bitget, config: AgentConfig | None = None, execute: bool = False) -> None:
        self.client = client
        self.cfg = config or AgentConfig()
        self.execute = execute

    # ------------------------------------------------------------------ sense
    def scan(self, symbol: str) -> dict[str, Any]:
        candles = self.client.candles(symbol, self.cfg.timeframe, 200)
        regime = screen(symbol, candles, er_floor=self.cfg.er_floor)
        signal = evaluate(candles, symbol=symbol, engine=self.cfg.engine)
        return {"symbol": symbol, "regime": regime, "signal": signal,
                "price": candles[-1][4] if candles else 0.0,
                "atr": atr(candles), "candles": candles}

    def scan_universe(self) -> list[dict[str, Any]]:
        return [self.scan(s) for s in self.cfg.universe]

    # ----------------------------------------------------------------- decide
    def decide(self, scans: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Highest-confidence tradeable signal that survives the regime gate."""
        ranked = [s for s in scans if s["regime"].tradeable and s["signal"].action != "flat"]
        if not ranked:
            return None
        ranked.sort(key=lambda s: -s["signal"].confidence)
        return ranked[0]

    # ---------------------------------------------------------------- execute
    def build(self, pick: dict[str, Any]) -> Plan:
        signal: Signal = pick["signal"]
        if signal.action not in ("long", "short"):
            raise RiskRefusal(f"no actionable signal ({signal.engine}: {signal.reason})")
        candles = pick["candles"]
        equity = self.client.equity()
        spec = self.client.contract_spec(pick["symbol"])
        entry = self.client.last_price(pick["symbol"]) or signal.entry
        stop, target = protective_rails(entry, signal.stop, signal.target, pick["atr"],
                                        direction=signal.action)
        return build_plan(pick["symbol"], signal.action, entry, stop, target, equity,
                          leverage=min(self.cfg.leverage, spec["max_leverage"]),
                          risk_pct=self.cfg.risk_pct, min_size=spec["min_size"],
                          size_step=spec["size_step"], price_place=spec["price_place"],
                          confidence=signal.confidence, min_rr=self.cfg.min_rr,
                          min_stop_pct=self.cfg.min_stop_pct,
                          max_fee_ratio=self.cfg.max_fee_ratio)

    def open(self, plan: Plan, reason: str = "") -> dict[str, Any]:
        """Send entry + rails together, then verify the rails exist."""
        self.client.set_leverage(plan.symbol, plan.leverage, plan.hold_side)
        order = self.client.place_order(plan.symbol, plan.side, plan.size,
                                        trade_side="open", stop_loss=plan.stop,
                                        take_profit=plan.target)
        time.sleep(3)
        rails = self.client.rails_of(plan.symbol)
        verified = bool(rails.get("stop_loss")) and bool(rails.get("take_profit"))
        row = journal.record("opened", {"plan": plan.as_dict(), "order": order,
                                        "rails": rails, "engine": self.cfg.engine,
                                        "reason": reason, "verified": verified},
                             self.cfg.journal_path)
        if not verified:
            # A position without a stop is an incident: flatten first, explain later.
            self.client.close_position(plan.symbol, plan.hold_side)
            journal.record("aborted_naked_position",
                           {"symbol": plan.symbol, "rails": rails, "reason": reason},
                           self.cfg.journal_path)
            notify.send(f"⚠️ {plan.symbol}: rails not confirmed, position closed immediately")
        else:
            notify.send(notify.format_plan(plan.as_dict(), reason))
        return row

    def cycle(self) -> dict[str, Any]:
        scans = self.scan_universe()
        notify.send(notify.format_screen([s["regime"].as_dict() for s in scans]), silent=True)
        journal.record("screen", {"regimes": [s["regime"].as_dict() for s in scans],
                                  "signals": [s["signal"].as_dict() for s in scans]},
                       self.cfg.journal_path)

        if self.execute and len(self.client.positions()) >= self.cfg.max_positions:
            journal.record("skipped", {"why": "max_positions reached"}, self.cfg.journal_path)
            return {"action": "skip", "why": "max_positions reached"}

        pick = self.decide(scans)
        if pick is None:
            journal.record("skipped", {"why": "no tradeable signal"}, self.cfg.journal_path)
            return {"action": "skip", "why": "no tradeable signal"}

        try:
            plan = self.build(pick)
        except RiskRefusal as refusal:
            journal.record("refused", {"symbol": pick["symbol"], "why": str(refusal),
                                       "signal": pick["signal"].as_dict()},
                           self.cfg.journal_path)
            notify.send(f"🚫 {pick['symbol']} refused by risk rails: {refusal}")
            return {"action": "refused", "why": str(refusal)}

        if not self.execute:
            journal.record("would_open", {"plan": plan.as_dict(), "reason": pick["signal"].reason},
                           self.cfg.journal_path)
            return {"action": "would_open", "plan": plan.as_dict()}

        return {"action": "opened", "row": self.open(plan, pick["signal"].reason)}

    # ------------------------------------------------------------------ watch
    def watch(self) -> list[dict[str, Any]]:
        """Report open positions against their rails — the 24/7 half of the job."""
        out = []
        for pos in self.client.positions():
            symbol = pos.get("symbol", "")
            mark = float(pos.get("markPrice") or 0)
            entry = float(pos.get("openPriceAvg") or 0)
            total = float(pos.get("total") or 0)
            rails = self.client.rails_of(symbol)
            distance = None
            if rails.get("stop_loss") and mark:
                distance = abs(mark - rails["stop_loss"]) / mark * 100.0
            row = {"symbol": symbol, "holdSide": pos.get("holdSide"), "size": total,
                   "entry": entry, "mark": mark, "uPnl": float(pos.get("unrealizedPL") or 0),
                   "liq": float(pos.get("liquidationPrice") or 0), "rails": rails,
                   "stopDistancePct": round(distance, 3) if distance is not None else None}
            out.append(row)
            journal.record("position", row, self.cfg.journal_path)
        return out

    # ------------------------------------------------------------------- loop
    def run(self, cycles: int | None = None) -> None:
        notify.send(f"🌙 NightShift online — {self.cfg.engine} engine, "
                    f"{'LIVE' if self.execute else 'DRY-RUN'}, universe {', '.join(self.cfg.universe)}")
        done = 0
        while cycles is None or done < cycles:
            try:
                result = self.cycle()
                print(f"[cycle {done}] {result.get('action')} :: {result.get('why', result.get('plan', ''))}")
            except BitgetError as exc:
                if self._readapt_mode(exc):
                    # Retry immediately with the other API family instead of
                    # burning the next tick on the same error.
                    print(f"[cycle {done}] mode akun berubah → {self.client.api_version}; ulangi siklus")
                    continue
                journal.record("error", {"error": str(exc)}, self.cfg.journal_path)
                print(f"[cycle {done}] exchange error: {exc}")
            done += 1
            if cycles is not None and done >= cycles:
                break
            time.sleep(self.cfg.interval_seconds)

    _MODE_ERRORS = ("40084", "40085")

    def _readapt_mode(self, exc: BitgetError) -> bool:
        """Re-probe the account generation after a mode-mismatch error.

        Bitget can move an account between Classic and Unified without asking,
        and each family rejects the other's endpoints. Without this the loop
        would log the same error on every tick until a human restarted it —
        which is exactly what happened the first time the account was switched
        mid-session. Returns True only when the detected family actually changed,
        so a persistent failure still surfaces as an error rather than looping.
        """
        if exc.code not in self._MODE_ERRORS or getattr(self.client, "creds", None) is None:
            return False
        before = getattr(self.client, "api_version", "classic")
        try:
            now = self.client.detect_api_version()
        except BitgetError:
            return False
        if now == before:
            return False
        journal.record("mode_changed", {"from": before, "to": now}, self.cfg.journal_path)
        notify.send(f"🔁 Mode akun Bitget berubah: {before} → {now}. "
                    f"Loop lanjut pakai API {now}.")
        return True


def summarize(scans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"symbol": s["symbol"], "regime": s["regime"].as_dict(),
             "signals": [sig.as_dict() for sig in evaluate_all(s["candles"], symbol=s["symbol"])]}
            for s in scans]
