# Demo — real orders, real market, real refusals

Everything below was executed against a **live Bitget account with real trading
keys** (`USDT-FUTURES`, Classic v2), on **7 October 2026**, from a Linux VPS
(`Linux 6.8.0 / Python 3.12.3`). The `watch` output at the end is a position
this agent was actually managing at the time, with TP/SL rails read back from
the exchange.

Raw, unedited transcript: [`DEMO-raw.txt`](DEMO-raw.txt).

---

## 1. Unit tests — no network required

```
$ python3 -m unittest discover -s tests
Ran 22 tests in 0.005s

OK
```

The same 22 tests pass on Windows (Python 3.14) and Linux (3.12). Risk maths is
testable in isolation by design — see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 2. `screen` — the gate that decides whether to trade at all

```
   XAUUSDT     4123.33  ER 0.42  range  1.54%  RSI  29.1  [TRADEABLE] down with structure
              support       4123.59  touches  40  breaks  0  bounce  0.08%
              resistance    4167.25  touches  64  breaks  0  bounce  0.22%
   BTCUSDT    83649.60  ER 0.32  range  3.75%  RSI  24.4  [TRADEABLE] down with structure
              resistance   84205.53  touches  24  breaks  1  bounce  0.35%
   ETHUSDT     2594.92  ER 0.24  range  5.22%  RSI  22.8  [TRADEABLE] down with structure
   SOLUSDT      117.59  ER 0.14  range  4.30%  RSI  29.9  [TRADEABLE] down with structure
```

A later cycle of the same command:

```
   SOLUSDT      117.77  ER 0.10  range  4.30%  RSI  32.2  [SKIP] chop (ER 0.10 < 0.1)
                                                          — fees would eat the edge
```

**Read that carefully**: the agent's first output is not a trade idea, it is a
verdict on whether the market pays at all. Levels are graded by evidence —
a support with **40 touches and 0 breaks** is structure; the ones with 1–2
touches are discarded as rumours.

---

## 3. `signals` — every engine states its reasoning

```
== XAUUSDT @ 4123.33 ==
  momentum    short  conf 0.60  close 4123.33 broke 24-bar low 4125.67
  reversion   flat   conf 0.00  z-score -1.74 inside the band
  rsi         flat   conf 0.00  RSI 29.1 — no extreme with confirmation
  exhaustion  flat   conf 0.00  no spike (-0.32% in 5 bars)
```

Note the `rsi` engine refusing to fire at RSI 29.1: an extreme **plus** a
reversal candle is required. An oversold reading alone is a coin flip with a
fee attached.

---

## 4. `plan` — what it would send, or why it refuses

```
$ python3 -m nightshift.cli plan --symbol XAUUSDT --engine rsi
XAUUSDT: REFUSED — no actionable signal (rsi: RSI 29.6 — no extreme with confirmation)

$ python3 -m nightshift.cli plan --symbol XAUUSDT --engine momentum
XAUUSDT: REFUSED — computed size is zero — widen risk or tighten the stop
```

Both refusals are correct and instructive:

* the first: the rule demanded confirmation and did not get it;
* the second: the account held **$8.85** equity, so a 1% risk budget is
  **$0.088** — against a 5–13 point XAU stop that is below one 0.01 lot. The
  agent refuses rather than rounding up into an oversized position.

Orchestration detail from the same run — the `--symbols` option must not eat the
subcommand, so `--symbols XAUUSDT screen` would have failed with
`the following arguments are required: command` under a greedy `nargs="+"`. The
CLI now accepts comma-separated or repeated `--symbols` in either position:

```
$ python -c "... build_parser().parse_args(['--symbols','XAUUSDT','screen']) ..."
before: screen ['XAUUSDT']
after : screen ['XAUUSDT', 'BTCUSDT']
```

---

## 5. `watch` — the live position, rails read back from the exchange

```
$ python3 -m nightshift.cli watch
{
  "symbol": "XAUUSDT",
  "holdSide": "long",
  "size": 0.05,
  "entry": 4128.61,
  "mark": 4123.26,
  "uPnl": -0.2675,
  "liq": 4068.823209975865,
  "rails": {
    "stop_loss": 4120.0,
    "take_profit": 4158.0
  },
  "stopDistancePct": 0.079
}
```

Why this matters: those rails were attached **in the same request as the entry**
(`presetStopLossPrice=4120`, `presetStopSurplusPrice=4158`) and then **read back
from Bitget** as plan orders (`loss_plan` / `profit_plan`). The position object
itself reports empty `takeProfit`/`stopLoss` fields — a detail that cost real
debugging time and is now encoded in `exchange.rails_of()`.

`stopDistancePct: 0.079` is the agent noticing, unprompted, that its stop was
$3.26 away.

---

## 6. `review` — the agent grading itself

```
{
  "trades": 1, "wins": 1, "losses": 0, "winRate": 1.0,
  "avgWin": 0.57, "avgLoss": 0.0, "expectancy": 0.57,
  "grossWin": 0.57, "grossLoss": 0, "refusals": 0,
  "byEngine": { "manual": { "n": 1, "pnl": 0.57, "wins": 1, "winRate": 1.0 } }
}

- 1 closed trades, win rate 100%, expectancy +0.5700 USDT/trade
- engine manual: 1 trades, 100% win, +0.570 USDT -> keep
```

One closed trade is a sample size of one — which is the point of shipping the
review: the agent will keep printing its own record, and `lessons()` already
knows what to say when expectancy turns negative:

> *"Expectancy is not positive: the current engine is donating to fees. Raise
> the ER floor or widen the reward:risk requirement before trading more."*

---

## Reproduce it

```bash
export BITGET_API_KEY=... BITGET_API_SECRET=... BITGET_API_PASSPHRASE=...
python -m nightshift.cli screen
python -m nightshift.cli signals --symbols XAUUSDT
python -m nightshift.cli plan --symbol XAUUSDT --engine rsi
python -m nightshift.cli watch
python -m nightshift.cli review
```

All five commands are read-only. Nothing in this transcript places an order;
the position shown was opened deliberately as a separate, auditable event.