# What we got wrong

Every number in this repository was wrong at least once. This page lists the
corrections we found ourselves, in the order we found them, because a project
that only publishes its final figures is asking to be believed rather than
checked. Where a claim changed, the old claim is stated first and the corrected
one second.

---

## 1. A rounding bug manufactured a whole strategy

**Claimed:** Sharpe **+13 to +17** on cheap coins (DOGE, ALGO, HBAR), win rates
of 69–75%, and a positive average PnL on exits labelled `stop`.

**Wrong because:** the simulator left price precision at the default of 2
decimals. At DOGE 0.088 that rounds the stop to 0.09 — *above* the entry — and
the target to 0.09, *below* it, so "stop-loss" exits were recorded as winners.
At XAU 4 130 the same rounding is 0.0002% of price and harmless, which is why the
majors barely moved and the bug hid for so long.

**Found by:** auditing the exit accounting — 267 exits labelled `stop` with 58%
of them profitable is arithmetically impossible.

**Fixed:** full precision inside simulations (`price_place=None`); the venue's
tick size applies only when sending a real order. A regression test now asserts
that every stop exit loses and every target exit wins.

**Corrected result:** the cheap-coin panel is **negative** — aggregate Sharpe
**−0.47**, expectancy −0.073 USDT/trade over 366 trades. Reported as a negative
result, not quietly dropped.

## 2. The first grid failed for a reason we initially blamed on the market

**Claimed:** the strategy has no edge; almost every one of 12 configurations lost.

**Wrong because:** the losses were fee arithmetic, not direction. Position size
cancels out of `fees / risk = (entry × 2 × fee_rate) / stop_distance`, so at a
0.06% taker fee a 0.12% stop means fees equal the entire risk budget — every
trade must win twice to break even.

**Fixed:** a stop-distance floor (`min_stop_pct`) and a refusal rail
(`max_fee_ratio`, default 30% of risk). The same data moved from Sharpe −3.50 to
**+3.71**.

## 3. The headline improved *because* we stopped cheating ourselves

The two fixes above were not "tuning". The pre-rail momentum baseline, kept in
`docs/paper/paper_15m_momentum_baseline.json`, pays **245.14 USDT of fees on 176
trades** and refuses 744 setups. That number is published next to the good one
on purpose.

## 4. A verification run that disagreed with the sweep — the bug was in the CLI

**Claimed:** the grid said positive; running the same configuration through the
CLI said negative.

**Wrong because:** `cmd_paper` never forwarded `min_stop_pct` / `max_fee_ratio`,
so the CLI ran a different experiment than the one that had been validated. The
lesson we now enforce as a rule: always re-run the headline through the path a
user actually types.

## 5. An aggregate return that did not match its own equity curve

**Claimed:** `totalReturn` −0.71 while the equity curve ended +15%.

**Wrong because:** PnL was divided by one account's capital while the run traded
four. Fixed with an explicit `pooledStartEquity` convention and a test that locks
it.

## 6. The sweep's winners were fewer than they first appeared

After the rounding fix, the 48-cell sweep was re-run: **6 of 48** cells stayed
positive on both halves (down from 5 before the fix — the survivors moved, and
one of the original five disappeared). The cheap-coin cells that looked
spectacular did not survive at all. Two of the six have fewer than 15 validation
trades, and that is stated next to their Sharpe rather than hidden by a
leaderboard.

## 7. Ten days of "1H exhaustion is the best strategy" survived exactly one test

The sweep's highest worst-half Sharpe is `1H exhaustion, stop ≥ 1.2%` (train
+4.89 / validation +6.69). We did **not** ship it as the default: 13 validation
trades is not evidence, and 1H holds are exposed to a funding cost this simulator
does not model. The default stayed on `15m reversion` for the most trades
(107/34) and the least unmodelled cost — a decision made against the prettiest
number in the table.

## 8. The published X post quotes pre-fix figures

The live post says the fee rail moved the data "from Sharpe −3.19 to +4.17".
Those were the numbers before the rounding fix. The corrected pair is **−3.50 →
+3.71**. `docs/X-POST.md` carries a note saying which revision the published post
came from, and the corrected numbers are the ones in this repo. The submission
form was already submitted with +4.17; the correction is documented in
`docs/SUBMISSION.md` rather than edited out.

## 9. A client that only spoke one of Bitget's two API dialects

**Claimed:** the live path works (it had opened a real XAU position with rails).

**Wrong because:** it worked on a Classic account. Once the account was upgraded
to a Unified Trading Account, the same key answered `40085` on every v2 endpoint,
and the v3 endpoints answered `40014` until the key's UTA permissions were
enabled. Both were needed: the account mode *and* the key's permission set are
separate failure modes that look identical from the outside.

**Fixed:** both API families in one client (`--api-version auto` probes and
stores the answer), v3 order placement with hedge-mode `posSide` and preset
rails, and a rail read-back that no longer mistakes an ordinary limit order for a
take-profit.

## 10. And the one that is not a bug: the agent refuses to trade a small account

The validated configuration risks **1%** per trade. On a 9 USDT account the
exchange's minimum order size is larger than that risk budget allows, so the
sizing function returns zero and the agent stops:

```
BTCUSDT: REFUSED — computed size is zero — widen risk or tighten the stop
```

That is the design working. The honest options are to trade a smaller universe at
a slightly higher risk (BTC ≈ 1.0%, SOL ≈ 1.4% — see `docs/paper/README.md`), or
to fund the account to a size where 1% is executable. What we will not do is
round the risk up and call it strategy.

---

## How to check any of this yourself

```bash
python -m unittest discover -s tests     # 64 tests, no network needed
python -m nightshift.cli audit           # verify the journal's hash chain
```

The journal is hash-chained: each record carries the hash of the one before it,
so a record edited or removed after the fact fails `audit` at the exact index.
Pre-chain records are reported as `unchained` rather than blessed. Every claim
above is reproducible from the committed logs in `docs/paper/`.