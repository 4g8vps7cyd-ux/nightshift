# Submission pack — Bitget AI · Genesis Season 2

Copy-paste sheet for the Google Form: **https://forms.gle/GyWZCMCPocgJdJon6**
Track: **Agentic Trading** · Sub-theme: **Cross-Asset Execution Agent**

> The handbook is explicit: the project description **must be written in the form
> itself** — a GitHub README or an X thread cannot substitute. Everything needed
> is below.

---

## Field-by-field

| Form field | What to put |
|---|---|
| **Project name** | `NightShift` |
| **Track** | Agentic Trading |
| **Competition sub-theme** | Cross-Asset Execution Agent |
| **Team members** | `Individual` (solo — handbook: enter "Individual" for solo) |
| **Bitget UID** | `1865834269` |
| **University Name** | *(leave blank — not a university team)* |
| **X post link** | *(paste the post URL — must quote https://x.com/Bitget_AI/status/2100519318824055159)* |
| **Submission Materials Link** | `https://github.com/4g8vps7cyd-ux/nightshift` + `docs/DEMO.md` (live demo transcript) + `docs/paper/` (paper trading logs) |
| **Apply for Demo Day** | Yes |
| **Apply for K3 Token Subsidy** | Yes (up to 30U in K3 credits for valid entries) |

---

## Project Description

> **Note:** the form requires a **five-part** answer with observed / estimated /
> targeted labels on every figure. The text **actually submitted** lives in
> [`PROJECT-DESCRIPTION.md`](PROJECT-DESCRIPTION.md), together with the standalone
> "Role of the LLM / AI in Your Project" field.
>
> The three-part sketch **below this note is an earlier draft**, kept for the
> record only. Where the two disagree, `PROJECT-DESCRIPTION.md` is what was sent.

### 1. What it is

NightShift is an agentic trading desk that runs while humans sleep. It senses
the market, decides whether the market is worth trading at all, sizes a
position against a hard risk budget, and places the entry **with its stop and
target attached in the same request** — then journals every decision,
including the ones it refuses to make.

The core thesis is a measurement, not an opinion. Across Bitget USDT-M
perpetuals the intraday **efficiency ratio** (Kaufman) sits between **0.00 and
0.06** on XAU, BTC, ETH, SOL and XAG. That is chop. In chop a directional
strategy does not lose because it predicted wrong — it loses because it paid
spread and fees to finish where it started. So NightShift gates on regime
before it looks for a signal, and it is allowed to answer "no".

It runs on tokenized US stocks and crypto through one client: the same signed
REST surface (Bitget Classic v2 today, UTA v3 ready) handles XAU, BTC, ETH,
SOL and rToken-style symbols, so a cross-asset rotation is a config change, not
a rewrite.

### 2. Target user and product value

**Who:** retail traders and small desks trading 24/7 markets with accounts
small enough that a single oversized position is fatal — the people whose
agents keep trading at 3am when nobody is watching a stop.

**Value:**
- *It can decline.* Most bots are built to find trades; NightShift is built to
  reject them. Its most useful output in live operation was
  `REFUSED — computed size is zero — widen risk or tighten the stop`, when an
  $8.85 equity account could not buy one lot of XAU within its 1% risk budget.
  Refusing there is the difference between a bad trade and no trade.
- *Rails travel with the entry.* Stop and target are attached in the same API
  request as the entry. Sending the entry first and the stop a moment later
  leaves a real, unbounded window where the position is naked — exactly the
  window markets like to move in.
- *Auditable by design.* Every cycle, refusal and rail check is written to a
  JSONL journal; `review` turns that journal into expectancy, per-engine
  attribution and plain-language lessons.
- *Deliberately boring infrastructure.* Zero dependencies, pure stdlib, runs on
  a $5 VPS, dry-run by default.

### 3. Validation data and key metrics

Filled from `docs/paper/` (walk-forward, no lookahead, taker fees charged on
both legs, stops checked before targets inside a bar):

| Metric | Value |
|---|---|
| Paper window | **2026-09-08 → 2026-10-08, 29.74 days**, 2 976 bars per symbol, 4 symbols |
| Trades / refusals | **144 trades**, 44 refusals by the risk rails |
| Sharpe (annualised, run's own trade frequency) | **+3.71** (Sortino +5.29) |
| Max drawdown | **2.24%** |
| Win rate / profit factor | **45.14%** / 1.241 |
| Expectancy | **+0.93 USDT per trade** on 1% risk of a $1 000 account |
| Fees paid | 99.80 USDT (vs 245.14 USDT for the fee-blind momentum baseline) |
| Per-symbol Sharpe | ETH +3.35 · SOL +2.69 · BTC −0.21 · XAU −0.30 |
| Live evidence | one real isolated XAUUSDT position with rails (SL 4120 / TP 4158) attached at entry and read back from the exchange — `docs/DEMO.md` |
| Unit tests | **46 passing**, no network required |

> **Post-submission correction (honesty note).** The form was submitted while the
> figures stood at Sharpe **+4.17** / 43.97% win / 2.22% DD on 141 trades. A
> rounding bug in the simulator was found afterwards (see below) and the
> corrected run is **+3.71** / 45.14% / 2.24% on 144 trades. The deployed default
> was validated with the corrected code; this file carries the corrected numbers,
> and every log in `docs/paper/` was regenerated from it.

The negative results are reported too — that is the point of the exercise:

- **A grid of 12 configurations was negative in almost every cell.** The
  diagnosis was arithmetic, not directional: `fees / risk = (entry × 2 ×
  fee_rate) / stop_distance`, so a 0.12% intraday stop on a 0.06% taker fee
  means fees equal the entire risk budget. The fix — a stop-distance floor plus
  a refusal rule for fee-dominated setups — is what moved the same data from
  Sharpe −3.50 to **+3.71**.
- **Once the simulator's own accounting was audited, the survivors got fewer,
  not more.** A rounding bug (2 decimals) had been manufacturing profitable
  "stop" exits on cheap coins; after the fix their edge vanished (Sharpe −0.47)
  and only **6 of 48** swept configurations still held up on both halves of the
  data. Both the bug and the negative result are in `docs/paper/README.md`.
- **Dual-leg hedging does not work on a small account.** At 100x an anchored
  losing leg costs `1/leverage = 1%`, while a realistic scalp target is 0.2%:
  one anchor erases five winners. The maths only closes at the 800–2000x that
  MT5 offers, not at Bitget's 100x cap. So NightShift trades one direction with
  hard rails instead.

---

## "Role of the LLM in Your Project" (standalone form field)

The LLM is the decision layer, not an advisory layer — but it reasons inside
rails it cannot move.

- **Where it decides:** `signals.ENGINES` is a registry of decision engines.
  Deterministic engines (momentum, mean-reversion, RSI-with-confirmation,
  exhaustion-fade) ship in the repo; the LLM slots in beside them by returning
  the same `Signal` dataclass (action, confidence, reasoning, stop, target).
  The regime gate, position sizing, rail construction, execution and journaling
  are shared — an LLM opinion gets no shortcut past the risk layer.
- **What it reasons about:** market context (regime, graded support/resistance),
  and on the roadmap events, sentiment and earnings, which is where an LLM has
  a genuine edge over fixed rules.
- **Why the split matters:** the LLM proposes; `risk.py` disposes. Confidence
  scales the risk taken, never the leverage used or the rails attached. A
  language model that cannot be *refused by code* is a liability with a
  compute bill.
- **Explainability:** every decision is journaled with its reason string, so a
  judge can replay what the agent knew at the moment it acted — and see the
  refusals it produced.

---

## Pre-flight checklist

- [ ] X post is **public** and quotes the official post; copy its URL
- [ ] repo is public: https://github.com/4g8vps7cyd-ux/nightshift
- [ ] `docs/paper/` logs committed (paper trading log = required material)
- [ ] `python -m unittest discover -s tests` → **46 passing** on a clean clone
- [ ] no credentials or journals committed (`.gitignore` covers `*.jsonl`)
- [ ] form submitted **before 8 Oct (UTC+8)** — deadline was extended twice