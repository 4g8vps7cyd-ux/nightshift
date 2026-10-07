# Architecture & failure modes

## The loop

```
candles (oldest-first, sorted defensively)
        │
        ├─► regime.screen()          ── ER gate + range floor + graded S/R
        │        │
        │        └─ untradeable ──────► journal "screen" ──► skip (no order)
        │
        ├─► signals.evaluate()       ── one engine, or evaluate_all() for a panel
        │        │
        │        └─ action "flat" ────► journal "skipped" (reason recorded)
        │
        ▼
  risk.build_plan()                  ── size from stop distance, RR floor,
        │                                margin cap, liquidation sanity check
        │
        ├─ RiskRefusal ───────────────► journal "refused" + Telegram notice
        │
        ▼
  agent.open()
        ├─ set_leverage(hold_side)
        ├─ place_order(entry + presetStopLoss + presetStopSurplus)   ← one request
        ├─ read back plan orders  (rails_of)
        ├─ rails confirmed? ── no ──► close_position() + journal "aborted_naked_position"
        └─ yes ─────────────────────► journal "opened" + Telegram card
```

## Why each module is isolated

| Split | Reason |
|---|---|
| `regime` ≠ `signals` | "Should I trade this market?" and "which way?" fail independently. Conflating them hides the more expensive mistake. |
| `signals` ≠ `risk` | A detection bug should not be able to bypass a rail. Risk has no access to signal internals — it receives a stop and a target. |
| `risk` ≠ `agent` | All the money maths is unit-testable with zero network. `tests/` proves it. |
| `journal` ≠ everything | Written from the outside; the agent cannot silently drop an inconvenient record. |

## Sizing maths

```
risk_usd  = equity × risk_pct × clamp(confidence, 0.1, 1.0)
size      = floor( risk_usd / |entry − stop| , size_step )
margin    = size × entry / leverage           must be ≤ max_margin_pct × equity
```

Consequences worth knowing:

* **Wider stop ⇒ smaller position.** Never a larger loss.
* **Small equity hits the lot floor.** With $8.85 equity and `--risk 1`, the budget is
  $0.088; against a 5–13 point XAU stop that buys **0.01 lot** — or nothing at all.
  `plan` then reports `computed size is zero — widen risk or tighten the stop`
  instead of quietly over-risking. Set `--risk` deliberately, not by hope.
* **Confidence scales risk**, not leverage. Conviction changes how much you risk, never how much you can lose.

## Liquidation sanity

```
long  : liq ≈ entry − entry × (1/leverage + mmr)      stop must be > liq
short : liq ≈ entry + entry × (1/leverage + mmr)      stop must be < liq
```

The approximation is deliberately conservative (it puts liquidation slightly
closer to entry than Bitget's exact tiered formula). A plan that passes here
passes on the venue. The rail exists because at 100x on XAU a naive 5% stop is
five times further than liquidation — the position would die before the stop was
ever consulted.

## Exchange quirks encoded here

1. **Candle ordering.** Bitget has returned candles in different orders across
   endpoints and releases. `exchange.candles()` parses every row and **sorts
   explicitly**; a silently reversed series is how a strategy backtests a
   fantasy. (A previous iteration read a price from 16 days ago as "now".)
2. **Preset TP/SL appears as plan orders**, not in the position's
   `takeProfit`/`stopLoss` fields. Verified on a live account:
   `loss_plan`/`profit_plan` entries carry the trigger prices. `rails_of()` reads
   those, which is why `watch` shows rails that the position object does not.
3. **Classic vs UTA.** This client speaks `v2` (Classic) and works today; `v3`
   (Unified) returns `40084 — "You are in Classic Account mode"` on accounts
   that have not upgraded. The version is a constructor concern, not a rewrite.
4. **Rate limits.** One API call per symbol per cycle, cached inside the agent
   loop; a panel of engines shares the same candle fetch.

## Known failure modes

| Failure | Handling |
|---|---|
| Exchange rejects the entry | `BitgetError` journaled, cycle aborts, loop continues next interval |
| Entry fills but rails rejected | position closed immediately, `aborted_naked_position` journaled, Telegram alert |
| Telegram unreachable | `notify` degrades to stdout — never to silence |
| Journal on a read-only volume | `record()` fails loudly at startup rather than silently losing the audit trail |
| Candle fetch returns fewer bars than the lookback | engines return `flat` with reason `not enough history`; no trade |
| Position closed manually by the operator | `watch` reports "no open positions"; no phantom state is kept locally |

## Extending to an LLM engine

`signals.ENGINES` is a registry of `fn(candles, symbol=...) -> Signal`. An
LLM-driven engine only has to return the same dataclass:

```python
def llm_event_engine(candles, symbol="", headline=""):
    verdict = ask_qwen(headline)          # your model call
    return Signal(symbol, "llm_event", verdict.action, verdict.confidence,
                  verdict.reasoning, price, verdict.stop, verdict.target)
```

Execution, sizing, rails, journaling and review are unchanged — the rail does not
care whether the idea came from an indicator or a language model. That separation
is the whole point of the layout.