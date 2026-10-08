# Fixing the two confounds — folds that reach the end, and a split that stops sliding

**Run date:** 2026-10-08 · **Command:** `uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets`
**Supersedes:** §4 of [`2026-10-08-longer-history-comparison.md`](./2026-10-08-longer-history-comparison.md), whose
two findings this work removes.
**Frozen "before" reports:** [`archive/2026-10-08-campaign-11999bars-unanchored-6folds.json`](./archive/2026-10-08-campaign-11999bars-unanchored-6folds.json)
and [`archive/2026-10-08-campaign-3999bars.json`](./archive/2026-10-08-campaign-3999bars.json).
**Controlled "after" report on the short tape:** [`archive/after-3999bars/2026-10-08-campaign.json`](./archive/after-3999bars/2026-10-08-campaign.json).

No threshold was lowered and no threshold value was touched: the nine thresholds of §49 are byte-identical
(`thresholds.json` digest `a2de0101d321…`, unchanged before and after).

---

## 1. What was wrong, and what changed

| | defect | fix |
|---|---|---|
| **1** | `DEFAULT_WALK_FORWARD_PLAN` set `max_folds=6`, so `walk_forward` started at `start=0` and `break`-ed after six folds on **every** tape. Six folds cover bars 0–1 600: the oldest slice, whatever the history length. | `DEFAULT_WALK_FORWARD_PLAN = WalkForwardPlan(350, 250, 200)` — **no ceiling**. The origin now rolls until the newest window no longer fits. `walk_forward` itself is unchanged: a cap set by a caller still stops the walk. |
| **2** | `split_dataset` cut 60/20/20 **from the start of the series**, so prepending history slid train, validation and sealed windows backwards in time; two campaigns then measured different market periods. | `split_dataset(..., anchor=True)`: the tail is pinned to the **end** of the tape (`train_end = count - (count - int(count * train_fraction))`), so the newest windows stay where they are and the **training window is what grows**. `anchor=False` — the default of `split_dataset`, and what `discovery.py` uses — is the historical cut, bar for bar. |

**What is unchanged for existing callers.** `split_dataset`'s new parameter defaults to `False` and its new
`anchored` field defaults to `False`; `DataSplit` and `MarketReport` only gained defaulted fields; `run_campaign`
and `MarketReport` gained no new required argument. `research/discovery.py` keeps `max_folds=6` in
`DiscoveryProtocol.walk_forward` and keeps the unanchored cut, so the discovery laboratory behaves exactly as
before this change — deliberately: it is the neighbouring defect already recorded in `README.md` and it is not
this task. Only the campaign opts into the anchor (`anchor=True`) and into the uncapped plan.

The campaign now also **publishes the windows it measured** (`market.anchored`, `market.dataset_window`,
`market.split_windows`, and `gate_protocol.split` / `gate_protocol.walk_forward.fold_ceiling` in the JSON):
a comparison that cannot name its periods is not a controlled comparison.

## 2. Fold count, and what it costs

`WalkForwardPlan(350, 250, 200)`, measured on the frozen tapes:

| tape | rolling window (train + validation, sealed set excluded) | folds, `max_folds=6` | folds, no ceiling | last validation block |
|---|---|---|---|---|
| 3 999 bars (archive) | 3 198 | **6** | **13** | 2026-09-17 → 09-22 (XAUUSD); 09-24 → 09-27 (BTCUSD) |
| 11 999 bars (current) | 9 598 | **6** | **45** | 2026-08-25 → 08-28 (XAUUSD); 09-08 → 09-11 (BTCUSD) |

The ceiling made the fold count a **constant** — 6 folds on 3 999 bars and 6 on 11 999 — and those six folds
covered bars 0–1 600 of each tape, i.e. **the oldest slice and nothing else**. Without it the fold count follows
the history and the last fold's validation block is the newest block the rolling window can offer.

**Cost, measured, not estimated.** The campaign grew in wall-clock on the same 11 999-bar datasets because each
fold is a real backtest of its own 250-bar validation block; the direct measurements are:

- `run_campaign.py --datasets docs/research/datasets` (11 999 bars, 2 markets, 3 candidates, **45
  folds/candidate**): **63.6 s** end to end.
- `run_campaign.py --datasets docs/research/datasets/archive-3999bars` (3 999 bars, same everything, **13
  folds/candidate**): **16.4 s**.

The extra cost is real but small in absolute terms: the folds replay 45 × 250 = 11 250 bars per candidate per
market, against the 7 199 + 2 399 bars already replayed for the in-sample and validation windows. A tape ten times
longer would cost roughly ten times more, which is the honest price of "the folds cover the history" — and the
pre-fix run could not be timed in isolation (its ceiling of 6 folds was already reflected in the published
reports, not in a controlled stopwatch), so no fake "before" duration is quoted here.

## 3. The nine gates and the p-values

### 3.1 The p-values did not move — only the gate that reads the folds did

`monte_carlo_p_value` is measured on the **validation window** of the split, so a change to the fold count cannot
touch it. On the 11 999-bar datasets the six p-values are **identical before and after**, to the last digit:

| hypothesis | p BEFORE (6 folds) | p AFTER (45 folds) |
|---|---|---|
| BTCUSD:fast-1.5R | 0.6344 | 0.6344 |
| BTCUSD:balanced-2R | 0.5814 | 0.5814 |
| BTCUSD:slow-2.5R | 0.8631 | 0.8631 |
| XAUUSD:fast-1.5R | 0.4565 | 0.4565 |
| XAUUSD:balanced-2R | 0.7502 | 0.7502 |
| XAUUSD:slow-2.5R | **0.3497** | **0.3497** |
| **survivors after BH (α = 0.10)** | **0 / 6** | **0 / 6** |
| **Bonferroni line** | 0.016667 | 0.016667 |

Minimum p = **0.3497**, i.e. **×21 above** the Bonferroni line, before and after. No candidate cleared the
correction in either run: the selection still finds nothing that stands out from the number of attempts.

### 3.2 The gate table

**BEFORE**, 11 999 bars, `max_folds=6`, unanchored split → **2 passed · 5 failed · 2 not evaluable**

| # | gate | threshold | figure | verdict |
|---|---|---|---|---|
| 1 | `backtest` | ≥ 30 trades | 208 / 185 | **passed** |
| 2 | `costs` | PF ≥ 1.2 | worst PF **0.81** (BTCUSD) | failed |
| 3 | `walk_forward` | ratio ≥ 0.5 | worst **0.50** — 3/6 folds, BTCUSD **and** XAUUSD | **passed, exactly on the line** |
| 4 | `out_of_sample` | retention ≥ 0.5 | worst **0.00** (BTCUSD), 62 trades | failed |
| 5 | `monte_carlo` | P(profit) ≥ 0.5 | worst **0.369** (BTCUSD) | failed |
| 6 | `stress` | PF ≥ 1.2 | worst PF **0.81** | failed |
| 7 | `parameter_robustness` | dispersion ≤ 0.5 | worst **1.17** (XAUUSD) | failed |
| 8 | `paper` | — | — | not evaluable |
| 9 | `risk` | — | — | not evaluable |

**AFTER**, 11 999 bars, no ceiling, anchored split → **1 passed · 6 failed · 2 not evaluable**

| # | gate | threshold | figure | verdict |
|---|---|---|---|---|
| 1 | `backtest` | ≥ 30 trades | 208 / 185 | **passed** |
| 2 | `costs` | PF ≥ 1.2 | worst PF **0.81** (BTCUSD) | failed |
| 3 | `walk_forward` | ratio ≥ 0.5 | worst **0.47** — **BTCUSD 21/45**, XAUUSD 26/45 | **failed** |
| 4 | `out_of_sample` | retention ≥ 0.5 | worst **0.00** (BTCUSD), 62 trades | failed |
| 5 | `monte_carlo` | P(profit) ≥ 0.5 | worst **0.369** (BTCUSD) | failed |
| 6 | `stress` | PF ≥ 1.2 | worst PF **0.81** | failed |
| 7 | `parameter_robustness` | dispersion ≤ 0.5 | worst **1.17** (XAUUSD) | failed |
| 8 | `paper` | — | — | not evaluable |
| 9 | `risk` | — | — | not evaluable |

Every figure is unchanged except the walk-forward one — the split is anchored and the tape is the same, so the
anchored and unanchored cuts coincide on a full tape (the last 40 % of it, whichever end one counts from). The
only thing that moved is the gate the ceiling was hiding.

### 3.3 The comparison that is now controlled

Anchoring turns the before/after into a controlled experiment for the first time: on the 3 999-bar archive the
anchored windows are `train 08-06 → 09-11`, `validation 09-11 → 09-24`, `holdout 09-24 → 10-07`, and on the
11 999-bar tape they are `train 04-07 → 07-27`, `validation 07-27 → 09-01`, `holdout 09-01 → 10-08`. The long
run's tail **contains** the short run's windows; the difference between the two runs is now the extra history,
not a different market period.

| gate | 3 999 anchored (13 folds) | 11 999 anchored (45 folds) | move |
|---|---|---|---|
| `backtest` | passed — 60 / 38 trades | passed — 208 / 185 trades | same |
| `costs` / `stress` | failed — worst PF **1.18** (XAUUSD) | failed — worst PF **0.81** (BTCUSD) | worse |
| `walk_forward` | failed — worst **0.31** (BTCUSD 4/13) | failed — worst **0.47** (BTCUSD 21/45) | ▲ up, still below the bar |
| `out_of_sample` | failed — BTCUSD retention 1.00 / 18 trades; XAUUSD 0.43 / 14 trades | failed — BTCUSD **0.00** / 62 trades; XAUUSD 1.00 / 68 trades | sharper, not kinder |
| `monte_carlo` | passed — worst 0.616 (XAUUSD) | failed — worst **0.369** (BTCUSD) | ▼ down |
| `parameter_robustness` | failed — worst 0.620 (XAUUSD) | failed — worst **1.173** (XAUUSD) | worse |
| **totals** | **2 · 5 · 2** | **1 · 6 · 2** | worse |

## 4. Verdict

**`walk_forward` does not hold, and it never did — the pass was an artefact of the ceiling.**

- With the six oldest folds, BTCUSD scored **3/6 = 0.50**, exactly the threshold: a pass by the letter of the
  rule on a coin-flip of six disjoint fortnight-long windows taken from the start of the tape.
- With the folds that actually cover the rolling window, BTCUSD scores **21/45 = 0.467** and the gate
  **fails**. XAUUSD, which the ceiling had also scored at 0.50, scores **26/45 = 0.578** and would pass on its
  own — the campaign aggregates per market and BTCUSD decides the verdict.
- The other eight gates, all six p-values, the selection and the multiple-testing outcome are **unchanged**
  (0 of 6 survivors, minimum p 0.3497, ×21 above Bonferroni): nothing about the statistical evidence moved, and
  nothing needed to.

**The result is stable now.** It is stable in the sense that matters: the fold set is no longer a fixed slice of
the oldest history, so re-running with more history cannot silently replace the windows under test. It is also
stable in the ordinary sense — the gate fails on the complete tape (0.467) and on the short tape (0.31) alike,
and the ceiling's version of the same tape is the only run in which it passed.

**The bottom line for a promotion is unchanged and, if anything, firmer:** `1 passed · 6 failed · 2 not
evaluable`, no candidate surviving the false-discovery correction, and a promotion refused for the gold winner
(`balanced-2R` at 3 999 bars, `fast-1.5R` at 11 999 — note that the **selected candidate changed** between the
two tapes, which is itself a reason not to promote either). Every declared cap and every anchored window is now
written in the report, so the next comparison can be held to them.

## 5. Left aside, and why

1. **`research/discovery.py` keeps `max_folds=6` and the unanchored cut.** It is a neighbouring defect already
   recorded in `README.md` (the laboratory's 100-bar validation blocks trade nothing), and it is a different
   module on a different subject. Leaving it alone is what keeps the discovery reports comparable with the ones
   already published.
2. **The anchor fixes the tail by fraction, not by an absolute bar count.** The tail keeps the fractions it
   always had, so the *proportion* of a tape reserved out of sample is invariant; what moves on a longer tape is
   where that tail starts. Fixing the tail to a constant number of bars instead would need a new parameter and
   would change every published window, which is a decision for the Lead, not a silent default.
3. **`docs/research/README.md` is now staler than it was** — it documents the 3 999-bar campaign and was already
   stale before this change; it is hand-written, no script generates it, and it was not in this task's remit.
4. **`docs/research/decisions/witness@1.1.0.json` and `2026-10-08-campaign.json` were rewritten** by the
   mandated campaign run: they are generated artifacts, always overwritten, and the pre-fix versions are
   preserved under `docs/research/archive/`.
5. **The 3 999-bar "after" run was written to `docs/research/archive/after-3999bars/`** rather than to
   `docs/research/`, so that it could not overwrite the 11 999-bar report while proving the controlled
   comparison.
6. **No threshold was lowered, no code outside `protocol.py` / `campaign.py` / `run_campaign.py` and their tests
   was touched, and nothing was committed.**
