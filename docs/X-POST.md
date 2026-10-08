# X (Twitter) submission posts

The hackathon requires **≥ 1 public X post about the build** at submission time.
Post from your own account, tag **@Bitget_AI**, and paste the repo link.

---

## Option A — the hook (recommended, 268 chars)

```
Every trading bot I've seen dies the same way: it's great at trading markets
that don't pay.

NightShift screens the REGIME before it looks for a signal — and is allowed to
say "no".

Live on Bitget, 22 tests, refusals included in the journal.

@Bitget_AI #BitgetAI
github.com/4g8vps7cyd-ux/nightshift
```

## Option B — the specific number (271 chars) — RECOMMENDED

```
Measured on Bitget perps: fees/risk = (entry × 2 × fee_rate) / stop_distance.
Size cancels. A 0.12% stop on a 0.06% taker fee = fees eat 100% of the risk.

Added a stop floor + a fee-refusal rail: same data went from Sharpe −3.50 to +3.71.

@Bitget_AI
github.com/4g8vps7cyd-ux/nightshift
```

> **Note for the published post:** the live post cites **−3.19 → +4.17**, the
> figures as they stood before the simulator's rounding bug was found and fixed
> (see `docs/paper/README.md`). The corrected pair is **−3.50 → +3.71**. Do not
> silently edit history: if the number is quoted again, quote the corrected one,
> or say which revision it comes from.

## Option C — the rail story (247 chars)

```
Built an agent for the hours humans sleep. Its most useful output so far:

"REFUSED — computed size is zero"

$8.85 equity means a 1% risk budget can't buy one lot of XAU. The bot refuses
instead of over-risking. Refusals are journaled like trades.

@Bitget_AI
github.com/4g8vps7cyd-ux/nightshift
```

---

## Optional thread (post A, then these as replies)

**2/**
```
The loop: sense → screen → decide → size → execute → report → review.

The two parts most bots skip:
• a regime gate that can veto the trade
• rails (stop + target) attached in the SAME request as the entry
```

**3/**
```
Why 1 request, not 2?

Sending the entry first and the stop a moment later leaves a real, unbounded
window where the position is naked. That is exactly the window markets like to
move in.

If the venue doesn't confirm the rails, NightShift closes the position and
journals an incident.
```

**4/**
```
Levels are graded, not drawn: touches, breaks, bounce size.

Live find: support 4123.59 — 40 touches, 0 breaks. That's structure.
A level touched once is a rumour and gets discarded.
```

**5/**
```
Sizing comes from the stop, never the other way round:

size = (equity × risk%) / |entry − stop|

A wider stop buys a smaller position. It never buys a bigger loss.
```

**6/**
```
And it refuses when the maths says no: stop beyond liquidation, reward:risk
under the floor, margin over the cap, no confirmation on the RSI extreme.

Every refusal is journaled, then `review` turns the journal into plain-language
lessons for the next session.
```

**7/**
```
What it deliberately does NOT do: no martingale, no averaging down, no price
prediction ML, and no dual-leg hedging on a small account.

On 100x an anchored leg costs 1% while a scalp target is 0.2% — one anchor
erases five winners. The maths only works at 800–2000x, which Bitget doesn't
allow. So: single direction, hard rails.

@Bitget_AI #BitgetAI
github.com/4g8vps7cyd-ux/nightshift
```

---

## Attach to the post

- screenshot of `screen` output (regime verdicts + graded levels)
- screenshot of `watch` output showing the live position with rails read back
- short screen recording of `run --cycles 2` if you want motion

## Checklist before submitting

- [ ] post is **public** (not protected) and contains `@Bitget_AI`
- [ ] repo is **public** and the README renders
- [ ] repo has no `journal.jsonl` / credentials committed (see `.gitignore`)
- [ ] `python -m unittest discover -s tests` passes on a clean clone
- [ ] X post URL copied into the submission form