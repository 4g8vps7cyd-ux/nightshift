# Project Description — 5-part answer for the submission form

Paste-ready text for the Google Form's **"Project Description"** field
(https://forms.gle/GyWZCMCPocgJdJon6). The form requires five parts and labels
(**observed / estimated / targeted**) on every figure; both are respected below.

Field-by-field values (project name, track, UID, materials links) are in
[`SUBMISSION.md`](SUBMISSION.md).

---

## Part 1 · Thesis

I built NightShift after measuring something uncomfortable: on Bitget USDT-M
perpetuals, intraday efficiency ratios (Kaufman) sit between **0.00 and 0.06** on
XAU, BTC, ETH and SOL. That is chop, not trend. In chop a directional agent does
not lose because it predicted wrong — it loses because it paid spread and fees to
finish where it started.

**Core hypothesis:** an agent's edge on 24/7 markets comes less from prediction
than from *refusal* — from trading only when the arithmetic pays, and from
guaranteeing its own risk rails. So NightShift asks "is this market worth trading
at all?" **before** it asks "which way?".

**Signal sources (implemented):** momentum (break of the 24-bar range),
reversion (z-score stretch from the mean), RSI extremes **with a reversal candle
required** (an oversold reading alone is a coin flip with a fee attached), and
exhaustion (a spike of ≥ threshold within ≤ N bars, faded). Every engine is a
small, named, testable function.

**Decision logic:** a regime gate comes first — efficiency ratio below the floor,
or a range too tight, means **no trade**, however loud the signal. Level quality
is graded empirically (touches, breaks, bounce size): a support with 40 touches
and 0 breaks is structure; a level touched once is a rumour and is discarded.

**Risk-control design (the part I care most about):** position size is derived
from the stop distance, never the reverse (`size = equity × risk% / |entry −
stop|`) — a wider stop buys a *smaller* position, never a bigger loss. Stop and
target are attached **in the same API request as the entry**; if the venue does
not confirm both rails, the position is closed immediately and the incident is
journaled. A stop beyond liquidation, an RR below floor, or a fee-dominated setup
is refused in code.

That last rail came out of the data: `fees / risk = (entry × 2 × fee_rate) /
stop_distance` — position size cancels, so a 0.12% intraday stop on a 0.06% taker
fee means **fees equal the entire risk budget**. Adding a stop-distance floor
plus a fee refusal moved the same data from Sharpe −3.19 to **+4.17**.

**Why existing solutions fall short:** most retail bots are built to *find*
trades and to look busy; almost none ship a rail that can veto the trade, and
almost none treat their own fee bill as a first-class constraint. Broker-side
"TP/SL" is usually a second request after the entry — a real, unbounded window in
which the position is naked.

## Part 2 · Target user and product value

**Concrete segment:** retail futures traders on 24/7 venues holding
**$100–$2,000** of equity, high risk appetite, **50–150x leverage**, **1–10
trades a day**, trading mainly **gold (XAU)** and **large-cap crypto perps**
(BTC/ETH/SOL), plus the tokenized US stocks they can now reach around the clock.
Use case: an agent that keeps a small account alive overnight and at weekends,
when the trader is asleep and cannot watch a stop.

Not "all traders". This segment specifically: their accounts are small enough
that **one oversized position is fatal**, and they are the group copy-trading
products quietly farm for volume.

**Core pain point:** with a stop tight enough to scalp, fees are 50–100% of the
risk budget. They are not losing to bad signals; they are losing to arithmetic
nobody shows them, because no retail tool displays the fee/risk ratio.

**What NightShift does differently:** it computes that ratio, refuses the trades
that fail it, and journals every refusal with its reason — so the trader can see
*why* the agent stood down, the opposite of a black box that always has a signal.

**Where existing solutions still fail:** (a) they optimise for volume, which is
exactly what destroys fee-based edge; (b) they place the stop after the entry;
(c) they hide the cost model; (d) they cannot say "no" in a way the user can
inspect afterwards.

## Part 3 · Validation data and key metrics

Walk-forward paper trading, **no lookahead**, taker fees charged on both legs,
stops checked before targets inside a bar, one position at a time per symbol.
4 symbols (XAU, BTC, ETH, SOL), 2,976 bars each at 15m, **29.74 days**, 1% risk
per trade, $1,000 per symbol ($4,000 pooled).

| Metric | Value | Label |
|---|---|---|
| Test period | 2026-09-07 → 2026-10-07, 29.74 days, 4 symbols | **observed** |
| Trades | 141 (4.7/day), plus 44 setups refused by the risk rails | **observed** |
| Returns | +3.78% on pooled $4,000 | **observed** |
| Sharpe / Sortino | +4.17 / +6.14 (per-trade returns, annualised on the run's own trade frequency) | **observed** |
| Max drawdown | 2.22% | **observed** |
| Win rate / profit factor | 43.97% / 1.279 | **observed** |
| Expectancy | +1.07 USDT per trade at 1% risk | **observed** |
| Turnover | ≈ $82k notional (≈ $579 average position, derived from the fee bill) | **estimated** |
| Costs — fees | 97.93 USDT paid, 2.4% of pooled capital over 30 days | **observed** |
| Costs — slippage | not modelled; market entries assumed to fill at the next bar's open | **estimated** |
| Costs — funding | not modelled; perp funding would reduce the numbers | **estimated** |
| Live evidence | one real isolated XAUUSDT position with SL 4120 / TP 4158 attached at entry and read back from the exchange, stopped out at 4121.31 for −0.365 USDT — the planned risk | **observed** |
| Concentration | per-symbol Sharpe: ETH +3.69, SOL +2.85, BTC +0.43, XAU −1.07 | **observed** |
| Negative control | same engine without the fee rail: Sharpe −3.19 and 238.95 USDT of fees | **observed** |

**How I will prove the product is effectively used and distributed** (targets —
this is a pre-launch build):

| Distribution metric | Target | Label |
|---|---|---|
| Activation | 60% of new users run `nightshift screen` and see a verdict on their own market within 5 minutes | **targeted** |
| Trading volume | $250k cumulative notional across 25 accounts within 60 days of listing | **targeted** |
| AUM | $25k of capital under the agent across those accounts — deliberately small, because the promise is survival, not leverage | **targeted** |
| Retention | 40% of activated accounts still running the loop at day 30 | **targeted** |
| Incremental fee | +$300 of Bitget fee revenue from 90 days of listed usage, at the observed fee/notional ratio | **targeted** |
| Risk | 0 accounts exceeding the configured per-trade risk budget; every breach logged as an incident | **targeted** |

## Part 4 · Progress

**Built and verified:** regime gate + graded S/R (`regime.py`); four signal
engines (`signals.py`); fee-aware rails, sizing and liquidation sanity check
(`risk.py`); stdlib-only signed Bitget client with preset TP/SL (`exchange.py`);
sense → screen → decide → size → execute → journal loop, dry-run by default
(`agent.py`); walk-forward paper simulator (`paper.py`); Sharpe/Sortino/drawdown
metrics (`metrics.py`); journal + expectancy review; Telegram alerts; a
seven-command CLI; **39 unit tests passing** on Windows and Linux from a clean
clone; a 30-day paper log, a 12-configuration train/validation grid, and two
equity-curve charts.

**Not built yet:** an LLM-driven decision engine (the engine registry makes it a
drop-in, but today's engines are deterministic); Agent Hub / MCP transport;
maker (limit) entries to cut the fee bill to a third; slippage and funding in the
simulator; multi-position portfolio caps.

**Problems hit during development, and how they were solved:**

1. **Bitget returned candles in an inconsistent order across endpoints.** An
   earlier script read a price from 16 days ago as "now". Fix: parse every row
   and sort explicitly — never trust the venue's ordering.
2. **Error `31008` on TP/SL attachment.** We tried full/partial mode, with and
   without `qty`, `reduceOnly` variants and `tradeSide=close`; all failed
   identically. There were two separate causes: the account sat in **Classic
   mode**, so the UTA v3 endpoint could not see the position at all; and on v3
   `marginMode` must be sent **explicitly** (`isolated`), because the default is
   `crossed` — a parameter Bitget's own documentation omits. Fix: prove the
   account mode with two probes (`40084` vs `40085`) before touching parameters,
   and send `marginMode` explicitly.
3. **Preset TP/SL do not appear in the position object.** They surface as *plan
   orders* (`loss_plan` / `profit_plan`). Fix: read them back from
   `orders-plan-pending` and treat "rails not confirmed" as an incident.
4. **Everything lost to fees.** The first 12-configuration grid was negative in
   every cell. The diagnosis was arithmetic, not directional (Part 1). Fix: the
   stop-distance floor plus the fee-refusal rule.
5. **A silent CLI/library divergence.** A new parameter was not forwarded from
   the CLI, so one configuration produced 77 trades through the CLI and 141
   through the library — flipping the Sharpe sign. Fix: an invariant test on the
   pooled basis, and always re-running the headline through the CLI path a user
   actually types.

**Next:** an LLM engine for event/sentiment reasoning (Qwen credits applied for),
maker-only entries, funding and slippage in the simulator, and a per-account risk
dashboard.

**Frameworks, models and APIs used:** Python 3.10+ **standard library only** (no
runtime dependencies); **Bitget USDT-M Futures REST API v2 (Classic)** for market
data, contract specs, account state, order placement with preset TP/SL,
plan-order readback and position close; **Bitget UTA v3 API** for probing and
evaluating the Agent Hub path; **Bitget Agent Hub / Agentic account** as the
planned transport for tokenized US stocks. No ML model decides today — the
deterministic engines do, with an LLM engine slotted for the event/sentiment role.

## Part 5 · Your take on AI Trading (optional)

Building this changed my view of what an agent is *for*. Everyone is racing to
build agents that predict better; the measurements say the binding constraint for
retail sits elsewhere — in costs, in discipline, and in the ability to do nothing.

Three beliefs, each paid for with a failed backtest:

1. **The most valuable output of a trading agent is a refusal.** Our most useful
   line of output was `REFUSED — computed size is zero — widen risk or tighten
   the stop`, on an account that could not buy one lot inside its risk budget. A
   system that cannot be *refused by code* is a liability with a compute bill.
2. **LLMs should own context, not arithmetic.** Where a language model genuinely
   beats fixed rules is events, sentiment, earnings and cross-market narrative;
   where it must never be trusted alone is position sizing, liquidation distance
   and order integrity. NightShift's split is deliberate: the LLM proposes,
   `risk.py` disposes. Confidence scales the risk taken, never the leverage used.
3. **24/7 markets make rails non-optional.** Once tokenized US stocks removed the
   closing bell, the failure mode stopped being "I missed a move" and became "the
   position was alone all night". An agent that attaches its own stop in the same
   request as its entry — and refuses to exist without it — is the only kind I
   would let near real money.

Suggestions for Bitget's tooling: publish the fee/risk ratio in the API docs (a
two-line formula that would spare thousands of small accounts); document
`marginMode` on `place-strategy-order`; and surface plan orders — where preset
TP/SL actually live — in the same object as the position, because every
integrator looks for them there first.

---

## Standalone field: Role of the LLM / AI in Your Project

Two layers, deliberately separated.

**1 · Runtime: the agent that runs the loop.** NightShift is driven by an LLM
agent (Hermes Agent) running on **DeepSeek V4.1 Flash** via Nous Research, with a
second instance on **DeepSeek V4 Pro** hosted on a VPS for 24/7 operation. The
LLM is what actually operates the desk: it senses market state through tools
(candles, contract specs, account state), calls the regime screen, decides
whether any engine's signal is worth acting on, sizes the trade through the risk
layer, executes the order **with its stop and target attached in the same
request**, reads the rails back to confirm they exist, then reports and monitors.
It also does the task orchestration and monitoring cadence — checking position
distance to stop, and standing down when the market fails the gate.

**2 · Development: the LLM as engineer.** The same agent stack wrote the code,
diagnosed three exchange-level bugs (inconsistent candle ordering, `31008` on
TP/SL attachment, preset TP/SL surfacing as plan orders instead of position
fields), produced the fee/risk diagnosis that turned a negative grid into a
positive one, and wrote the documentation and tests. Model used: **DeepSeek
V4.1 Flash**. **Alibaba Qwen** credits have been applied for (the hackathon's
token sponsor) to build the event/sentiment engine described below.

**What the LLM does not do — on purpose.** It cannot bypass the risk layer. Sizing,
the liquidation-distance check, the fee-refusal rule and rail construction live in
deterministic code; an LLM opinion is just one more entry in the engine registry,
and confidence scales the risk taken, never the leverage used. That boundary is
the design: language models are strong at context — events, sentiment, earnings,
cross-market narrative — and must never be the last line of defence on arithmetic.
Today's decision engines are deterministic rules for exactly that reason; the Qwen
engine is the next addition, and it will face the same rails.

**Conversational interaction** exists as a first-class path: the same agent
answers a trader in natural language ("is XAU worth trading tonight?", "why did
you refuse that setup?"), which is how the journal's refusals become something a
human actually reads.
