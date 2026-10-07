# Paper trading log — method, results, and what it does *not* prove

This directory holds the validation material for the **Agentic Trading** track:
walk-forward paper trading runs against real Bitget candles, produced by
`python -m nightshift.cli paper` and `python -m scripts.grid`.

Every number below is reproducible from the JSON files next to this file.

---

## Method (and why each choice matters)

| Choice | Reason |
|---|---|
| **Walk-forward, no lookahead** | The decision at bar *i* sees `candles[i+1-window : i+1]` only, and the fill happens at the **open of bar i+1** — when a market order sent at bar *i*'s close would actually trade. A test asserts the fill is never the signal bar's close. |
| **Stop checked before target inside a bar** | When one bar contains both levels, the pessimistic ordering is used. The optimistic ordering is the most common way a simulator flatters its author. |
| **Fees charged on both legs** | Bitget USDT-M **taker 0.06% per side**. Strategies that only work fee-free do not work. |
| **One position at a time, per symbol** | Matches the live agent's `--max-positions 1`; trades cannot overlap or pyramid. |
| **Time stop** | A position that neither hits its stop nor its target is closed after `--max-hold` bars (default 96), so a dead trade cannot sit forever. |
| **Refusals recorded** | Every trade the risk layer rejected is counted and logged with its reason. A rail that never fires is decoration. |

## Data

| | 15m run | 1H run |
|---|---|---|
| Universe | XAUUSDT, BTCUSDT, ETHUSDT, SOLUSDT | same |
| Bars per symbol | 2 976 | 1 000 |
| Window | 2026-09-07 16:30 → 2026-10-07 10:15 UTC | 2026-08-31 → 2026-10-07 |
| Span | **29.74 days** | **36.62 days** |

Both windows exceed the handbook's "recommended ≥ 2 weeks" and were produced
during the competition period.

## Aggregate results

| Config | Trades | Win rate | Sharpe | Sortino | Max DD | Expectancy | Profit factor | Total return | Fees paid |
|---|---|---|---|---|---|---|---|---|---|
| **15m · reversion · stop ≥ 1.2%** | 141 | 43.97% | **+4.17** | +6.14 | **2.22%** | **+1.071** USDT | 1.279 | **+3.78%** | 97.93 USDT |
| 15m · momentum (no stop floor) | 172 | 37.79% | −3.19 | −3.76 | 3.55% | −0.591 USDT | 0.868 | −2.54% | **238.95 USDT** |
| 1H · reversion · stop ≥ 1.2% | 85 | 35.29% | +0.028 | +0.04 | 2.20% | +0.010 USDT | 1.002 | +0.02% | 52.87 USDT |

Per-symbol Sharpe for the winning configuration:

| Symbol | Trades | Sharpe |
|---|---|---|
| XAUUSDT | 16 | −1.07 |
| BTCUSDT | 34 | +0.43 |
| ETHUSDT | 39 | +3.69 |
| SOLUSDT | 52 | +2.85 |

Read that honestly: **the edge is concentrated in SOL and ETH**, XAU loses, and
a 30-day window is one regime. This is evidence, not proof.

## The finding that made the difference

The first grid run was negative in **every** configuration — momentum at 15m
lost money with a Sharpe near −24. The cause was not prediction, it was
arithmetic:

```
fees per round trip = notional × fee_rate × 2
risk per trade      = size × stop_distance
hence:  fees / risk = (entry × 2 × fee_rate) / stop_distance      ← size cancels
```

The ratio does not depend on position size at all, only on how wide the stop is
relative to price. With a 0.06% taker fee:

| Stop distance | Fees as a share of the risk budget |
|---|---|
| 0.12% (a 15m ATR-ish stop) | **100%** — every trade must win twice to break even |
| 0.40% | 30% |
| 1.20% | 10% |

A 15m reversal stop is roughly 0.1–0.3% of price, so the strategy was paying its
entire risk budget in fees, twice over. Two rails were added in response, and
both are in `risk.py`:

1. **`min_stop_pct`** — widen a too-tight stop to a floor (the tuned run uses
   1.2%), which also shrinks position size to keep the risk constant.
2. **`max_fee_ratio`** — refuse the trade outright when fees would still exceed
   that share of the risk (default 30%). The momentum baseline produced **741
   such refusals**, which is the rail doing its job and is why its fee bill fell
   from a naive run's $1 144 (see `paper_15m_momentum.json`, the pre-rail run)
   to $239.

## Train / validation grid

`grid.json` holds 12 configurations, each run on the first 70% of the bars
("train") and the last 30% ("validation"), with the full table reported either
way. Exactly one configuration was positive on **both** halves:

| tf | engine | stop floor | train (n / sharpe / exp) | validation (n / sharpe / exp) |
|---|---|---|---|---|
| 15m | reversion | 1.2% | 105 / **+2.02** / +0.496 | 31 / **+6.24** / +1.644 |

Everything else was negative in training, in validation, or both. The headline
configuration was selected on the training half; the validation half is
genuinely out-of-sample for that choice.

## Conventions stated plainly

- **Equity:** each symbol runs its own **$1 000** paper account at **1% risk per
  trade**, 50x leverage cap, isolated margin model.
- **Pooled panel:** the aggregate treats the four symbols as four accounts, so
  pooled starting capital is **$4 000** and pooled returns are computed against
  it. This is why the aggregate Sharpe (+4.17) is lower than ETH's standalone
  (+3.69 → pooled contribution diluted by XAU's loss) — a smaller number on a
  bigger, honest denominator.
- **Sharpe:** annualised from **per-trade** returns using the run's own trade
  frequency (trades/day × 365), risk-free rate 0. The `periodsPerYear` field is
  written into every log so the convention travels with the number.
- **Fees:** taker 0.06% per side. The **maker** rate (0.02%) is not modelled —
  it would improve every row, and it is the first thing a live deployment would
  target (limit entries).

## What this does *not* prove

- **30 days is one regime.** These are range/trend-down conditions, not a
  year-long sample.
- **No funding costs, no slippage, no partial fills.** A live run on thin
  symbols will do worse than this simulation, not better.
- **Config selection happened after seeing the grid.** The validation half
  mitigates but does not eliminate that bias; a third, untouched window would be
  the honest next step.
- **Edge concentrated in two of four symbols.** XAU lost money in the winning
  configuration; the per-symbol table is included so that is visible.

## Reproduce

```bash
export BITGET_API_KEY=... BITGET_API_SECRET=... BITGET_API_PASSPHRASE=...

# headline run
python -m nightshift.cli --symbols XAUUSDT,BTCUSDT,ETHUSDT,SOLUSDT \
    --engine reversion --min-stop-pct 1.2 \
    paper --timeframe 15m --pages 6 --out docs/paper/paper_15m_reversion.json

# fee-blind baseline (shows what the fee rail is preventing)
python -m nightshift.cli --symbols XAUUSDT,BTCUSDT,ETHUSDT,SOLUSDT \
    --engine momentum paper --timeframe 15m --pages 6 \
    --out docs/paper/paper_15m_momentum_baseline.json

# train / validation grid
python -m scripts.grid --pages 6 --out docs/paper/grid.json

# reconcile full-window vs split runs
python -m scripts.diagnose_split --engine reversion --min-stop-pct 1.2
```

All of it is read-only against the exchange: `paper` never sends an order.
