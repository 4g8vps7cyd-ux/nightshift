# NightShift

**An agentic trading desk that keeps working while humans sleep — and refuses to trade when the market doesn't pay.**

Built for the **Bitget AI · Genesis Season 2** hackathon — track **Agentic Trading** (sub-theme: *Cross-asset execution Agent*).

Tokenized US stocks made 24/7 trading normal. Humans still sleep. NightShift is the agent that owns the hours nobody is watching: it senses the market, screens whether the market is worth trading at all, sizes the trade against a hard risk budget, fires the entry **with its stop and target already attached**, then reports every decision — including the refusals.

> **Live, not a mock-up.** The `watch` command below is reading a real isolated XAUUSDT position on Bitget with real TP/SL rails, fetched through the same client this repo ships.

---

## The idea in one line

Most trading agents fail for a reason that has nothing to do with prediction:

> **They are competent at trading markets that do not pay.**

Measured across Bitget USDT-M perpetuals at 15m/1H, intraday **efficiency ratios** (Kaufman) sit between **0.00 and 0.06** for XAU, BTC, ETH, SOL and XAG — pure chop. In chop, a directional strategy does not lose because it predicted wrong; it loses because it paid the spread and the fee several times to end up where it started.

So NightShift inverts the usual order. Before it asks *"which way?"* it asks *"is this worth trading at all?"* — and it is allowed to answer *no*.

| Component | Question it answers | Where |
|---|---|---|
| **Regime gate** | Is this market tradeable, or is it chop? | `nightshift/regime.py` |
| **Signal engines** | Which way, and with what conviction? | `nightshift/signals.py` |
| **Risk rails** | How big, and where does the accident start? | `nightshift/risk.py` |
| **Execution** | Entry + stop + target, atomically | `nightshift/agent.py` |
| **Journal & review** | Was that any good? What should change? | `nightshift/journal.py` |

---

## Design commitments (the parts that are opinions, not features)

**1. Screen before you trade.**
`efficiency_ratio = |net move| / |total path|`. Near 0 is chop, near 1 is trend. Symbols below the floor are skipped no matter how loud a signal is. In live testing this correctly flagged SOL as chop (ER 0.08) while XAU passed (ER 0.42).

**2. Levels are graded, not drawn.**
Every support/resistance is scored by **touches**, **breaks**, and **bounce size**. The live demo found a support at 4123.59 with **40 touches and 0 breaks** — that is structure. A level touched once is a rumour and is discarded.

**3. Rails travel with the entry.**
The stop and target go in the **same request** as the entry (`presetStopLossPrice` / `presetStopSurplusPrice`). Sending the entry first and the stop a moment later leaves a real, unbounded window where the position is naked — exactly the window in which markets like to move. If the venue does not confirm the rails, NightShift **closes the position immediately** and journals an incident.

**4. Size comes from the stop, never the other way round.**
`size = (equity × risk%) / |entry − stop|`. A wider stop buys a *smaller* position. It never buys a bigger loss. Refusals are normal output:

```
XAUUSDT: REFUSED — no actionable signal (rsi: RSI 29.6 — no extreme with confirmation)
XAUUSDT: REFUSED — computed size is zero — widen risk or tighten the stop
```

**5. Never a stop beyond liquidation.**
On an isolated position the stop is the plan and liquidation is the accident. If the accident would arrive first, the trade is refused. (With $8.85 equity and a 1% risk budget, the budget is **$0.088** — below one lot for most XAU stops. The agent refuses rather than over-risking. That is the rail working, and it is the single most useful thing this bot has told its owner.)

**6. Fees are a strategy input, not a footnote.**
`fees / risk = (entry × 2 × fee_rate) / stop_distance` — the position size
cancels. With a 0.06% taker fee, a 0.12% stop means **fees equal the entire risk
budget**: every trade must win twice to break even. So `risk.py` enforces a
floor on stop distance (`--min-stop-pct`, widening the stop and shrinking size to
keep risk constant) and refuses any setup where fees would still exceed
`--max-fee-ratio` of the risk. This single rail turned a −3.50 Sharpe drift into
**+3.71** on the same data — see [`docs/paper/`](docs/paper/README.md).

**7. Dry-run by default.**
`--live` is required to touch money. Even then, `--max-positions` is enforced.

**8. Speaks both of Bitget's account generations.**
Bitget serves Classic (`/api/v2/mix/*`) and Unified (`/api/v3/*`) accounts, and
each family rejects the other's endpoints, so a client that knows only one
stops working the day the account is upgraded. `--api-version auto` probes the
account (one read-only call), stores the answer, and the same rails run on
either: v2 `tradeSide` or v3 hedge-mode `posSide` + `presetStopLossPrice`, with
the same read-back verification after entry.

**9. The journal is tamper-evident.**
Each record carries the hash of the one before it, so a record edited or removed
after the fact fails `audit` at the exact index it broke — the same idea as the
hash-chained verdicts the strongest entries in this hackathon shipped. Pre-chain
records are reported as `unchained`, never silently blessed.

Everything we got wrong, and how we found it, is in
[`docs/WRONG.md`](docs/WRONG.md).

---

## Quickstart

No dependencies — pure Python stdlib (3.10+). It runs on a $5 VPS.

```bash
git clone https://github.com/4g8vps7cyd-ux/nightshift && cd nightshift

export BITGET_API_KEY=...        # or --credentials creds.json
export BITGET_API_SECRET=...
export BITGET_API_PASSPHRASE=...

python -m nightshift.cli screen                          # regime + levels, no orders
python -m nightshift.cli signals --symbols XAUUSDT       # every engine's opinion
python -m nightshift.cli plan --symbol XAUUSDT --engine rsi
python -m nightshift.cli once                            # one cycle (dry-run)
python -m nightshift.cli run --cycles 12 --live           # the 24/7 loop
python -m nightshift.cli audit                            # verify the journal's hash chain
python -m nightshift.cli watch                            # positions vs their rails
python -m nightshift.cli review                           # expectancy + lessons
python -m nightshift.cli paper --timeframe 15m --pages 6   # walk-forward paper run
python -m unittest discover -s tests                     # 39 tests, no network
```

Shared options work **before or after** the subcommand (`nightshift --symbols XAUUSDT screen`
and `nightshift screen --symbols XAUUSDT,BTCUSDT` are both valid).

### Real output

```
$ python -m nightshift.cli watch
{
  "symbol": "XAUUSDT",  "holdSide": "long",  "size": 0.05,
  "entry": 4128.61,     "mark": 4123.26,    "uPnl": -0.2675,
  "liq": 4068.82,
  "rails": { "stop_loss": 4120.0, "take_profit": 4158.0 },
  "stopDistancePct": 0.079
}
```

Full transcript with timestamps and hosts: [`docs/DEMO.md`](docs/DEMO.md).

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
   Bitget REST  ◄───┤ exchange.py    signed v2 (Classic) / v3 (UTA) │
   (crypto,         │  candles · ticker · contract spec · positions │
    gold,           │  place_order(+preset TP/SL) · plan orders     │
    tokenized       └───────┬──────────────────────────────────────┘
    US stocks)              │
                            ▼
   ┌────────────────────────────────────────────────────────────────┐
   │ agent.py   sense ─► screen ─► decide ─► size ─► execute ─► log │
   └───┬────────────┬───────────────┬──────────────┬───────────────┘
       │            │               │              │
   regime.py    signals.py      risk.py       journal.py
   ER gate +    momentum/       sizing +      JSONL + expectancy
   graded S/R   reversion/      rail checks   per engine + lessons
                rsi/exhaustion  + liq sanity
       │            │               │              │
       └────────────┴───────┬───────┴──────────────┘
                            ▼
                     notify.py   (Telegram, or stdout — never silence)
```

Detail and failure modes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## What it does *not* do (deliberate)

- **No price prediction ML.** Nothing here forecasts; it filters regimes and manages risk. A model that cannot say "no" is a liability with a compute bill.
- **No martingale / no averaging down.** Widening a loser is how small accounts die.
- **No grid or dual-leg hedging on a small account.** Verified arithmetic: on 100x, an anchored losing leg costs `1/leverage = 1%` while a realistic scalp target is `0.2%` — one anchor erases five winners. The maths only works at the 800–2000x that venues like MT5 allow, not at Bitget's 100x cap. So the strategy is single-direction with rails.
- **No claim of profitability.** The journal exists so the agent can be judged on its record, not its intentions.

---

## Why it fits the Agentic Trading track

The track asks for an agent whose LLM **senses market conditions, reasons independently, and places risk-managed orders on its own**. NightShift ships exactly that loop with the reasoning made explicit and auditable:

- every decision is a named, testable function (22 unit tests, no network needed);
- every order carries its own risk contract (stop, target, size, liquidation check);
- every cycle is journaled, **refusals included**, and `review` turns the journal into plain-language lessons the agent acts on next session;
- the observation layer is swappable: `signals.ENGINES` is a registry, so an LLM-driven engine can be dropped in beside the deterministic ones without touching execution or risk.

---

## Validation — 30 days of walk-forward paper trading

Real Bitget candles, no lookahead, taker fees charged on both legs, stops
checked before targets inside a bar. Full method and caveats:
[`docs/paper/`](docs/paper/README.md).

| Config | Trades | Win rate | Sharpe | Max DD | Expectancy | Fees paid |
|---|---|---|---|---|---|---|
| **15m · reversion · stop ≥ 1.2%** | 144 | 45.14% | **+3.71** | **2.24%** | **+0.93 USDT** | 99.80 USDT |
| 15m · momentum (no stop floor) | 176 | 37.50% | −3.50 | 3.43% | −0.64 USDT | 245.14 USDT |
| 1H · reversion · stop ≥ 1.2% | 89 | 35.96% | −0.67 | 2.26% | −0.24 USDT | 55.27 USDT |

Two honest notes that matter more than the headline: the edge is concentrated in
**SOL and ETH** (XAU lost money in the winning configuration), and one of twelve
grid configurations was positive on **both** the training and the out-of-sample
half — the rest were negative, which is what the fee maths predicted before any
of it was measured.

### Shipped defaults = the configuration the research validated

A wider 48-cell sweep (2 timeframes × 4 engines × 3 stop floors × 2 ER floors,
each split train/validation) left **six** survivors, and the best-evidenced one is
the default the CLI and agent now ship:

```
engine reversion · 15m · stop floor 1.2% · efficiency-ratio gate 0.10
```

A test asserts that the shipped defaults match that survivor, so the code cannot
quietly drift away from the result it is based on. Running it right now:

```
$ nightshift screen
   XAUUSDT  4114.52  ER 0.03  range 2.44%  RSI 50.5  [SKIP] chop (ER 0.03 < 0.1)
$ nightshift plan --symbol XAUUSDT
XAUUSDT: REFUSED — no actionable signal (reversion: z-score 0.41 inside the band)
```

Both answers are the feature: with a validated configuration and a chopping
market, the correct action is to stand down.

## Status & roadmap

| | |
|---|---|
| Working today | regime gate, 4 signal engines, fee-aware rail-checked execution with preset TP/SL, live position/rails readback, walk-forward paper trading with Sharpe/Sortino/drawdown, journal + review, Telegram alerts, 39 tests |
| Next | LLM-as-engine (Qwen) for event/sentiment reasoning, Agent Hub / MCP transport for tokenized US stocks, maker (limit) entries to cut the fee bill to a third, multi-position portfolio caps, rail-proximity alerts |

## Safety notice

This is real-money trading software. Run it with `--live` only on an account you can afford to lose, start with `--risk 0.5`, and keep `--max-positions 1`. Nothing in this repository is financial advice.

MIT licensed — see [LICENSE](LICENSE).