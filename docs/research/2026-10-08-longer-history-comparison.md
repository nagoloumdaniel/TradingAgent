# Longer history — did 12 000 M15 bars change the campaign verdict?

**Run date:** 2026-10-08 · **Command:** `uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets`
**Baseline preserved at:** `docs/research/archive/2026-10-08-campaign-3999bars.json`
**Old datasets preserved at:** `docs/research/datasets/archive-3999bars/`

No project code, threshold or test was touched (`git diff -- src tests scripts` is empty).
Both old 3 999-bar datasets were **archived, not deleted** (the store refuses two datasets for
one symbol), so the "before" is reproducible byte-for-byte from its frozen `.jsonl`.

---

## 1. What was actually fetched

| | symbol | bars | period covered | days | fingerprint | gaps |
|---|---|---|---|---|---|---|
| **BEFORE** | XAUUSD | 3 999 | 2026-08-06T08:15Z → 2026-10-07T02:45Z | 62 | `f0d6c90ff7671a79…` | 18 |
| **BEFORE** | BTCUSD | 3 999 | 2026-08-27T04:15Z → 2026-10-07T22:00Z | 41 | `d330537e34f96f24…` | 8 |
| **AFTER** | XAUUSD | **11 999** | 2026-04-07T17:15Z → 2026-10-08T02:00Z | **184** | `3906afa2c63dab53…` | 58 |
| **AFTER** | BTCUSD | **11 999** | 2026-06-05T00:00Z → 2026-10-08T02:00Z | **125** | `96f80664d572e28c…` | 9 |

Full fingerprints:

- XAUUSD `mt5-XAUUSD-2026-10-08` → `3906afa2c63dab5317e63d32b15bbfc7199c3cf218bf45a7cf642e5bd79961f0`
- BTCUSD `mt5-BTCUSD-2026-10-08` → `96f80664d572e28c8e5e560b70f1fdbea98a8512d8bec26ecac8ae4b861920c2`

**12 000 asked, 11 999 returned for each market.** The shortfall is 1 bar (0.008%) — the terminal
serves closed candles only, so the forming candle is excluded. Report as 11 999, not 12 000.

Both new series are **wall-clock supersets** of the old ones (the old XAUUSD window 08-06→10-07 is
inside 04-07→10-08; same for BTCUSD). No history was lost.

Tool note: the fetch tool now derives `dataset_id = mt5-<SYMBOL>-<date>`, so the new files
(`XAUUSD-M15-mt5-XAUUSD-2026-10-08.jsonl`) **cannot** overwrite the old
(`XAUUSD-M15-mt5-2026-10-07.jsonl`). The archive move was still required because
`DatasetStore.load_all` raises `ValueError` on two datasets for one symbol.

---

## 2. The nine gates, before and after

| # | gate | threshold | BEFORE (3 999 bars) | AFTER (11 999 bars) | verdict move |
|---|---|---|---|---|---|
| 1 | `backtest` | ≥ 30 in-sample trades | **passed** — 38 (XAUUSD) / 60 (BTCUSD) | **passed** — 185 / 208 | same |
| 2 | `costs` | PF ≥ 1.2 | **failed** — worst PF **1.18** (XAUUSD) | **failed** — worst PF **0.81** (BTCUSD) | same, worse |
| 3 | `walk_forward` | ratio ≥ 0.5 | **failed** — worst **0.17** (1/6 folds) | **passed** — worst **0.50** (3/6 folds) | ▲ up, exactly on the line |
| 4 | `out_of_sample` | retention ≥ 0.5 | **failed** — worst **0.43** (XAUUSD); 14 / 18 trades | **failed** — worst **0.00** (BTCUSD); **68 / 62 trades** | same |
| 5 | `monte_carlo` | P(profit) ≥ 0.5 | **passed** — worst **0.616** (XAUUSD) | **failed** — worst **0.369** (BTCUSD) | ▼ down |
| 6 | `stress` | PF ≥ 1.2 | **failed** — worst PF **1.18** | **failed** — worst PF **0.81** | same, worse |
| 7 | `parameter_robustness` | dispersion ≤ 0.5 | **failed** — worst **0.62** (XAUUSD) | **failed** — worst **1.17** (XAUUSD) | same, worse |
| 8 | `paper` | — | **not evaluable** | **not evaluable** | same |
| 9 | `risk` | — | **not evaluable** | **not evaluable** | same |
| | **totals** | | **passed 2 · failed 5 · not evaluable 2** | **passed 2 · failed 5 · not evaluable 2** | **unchanged** |

Per-market detail:

| gate | market | BEFORE | AFTER |
|---|---|---|---|
| `costs` / `stress` | BTCUSD | PF 1.342, 26 trades, DD 44.91 | PF 0.812, 57 trades, DD 135.08 |
| `costs` / `stress` | XAUUSD | PF 1.176, 14 trades, DD 49.82 | PF 0.902, 63 trades, DD 118.11 |
| `out_of_sample` | BTCUSD | 18 trades, PF 0.501, −68.88, retention **1.00** | 62 trades, PF 0.951, −18.98, retention **0.00** |
| `out_of_sample` | XAUUSD | 14 trades, PF 0.495, −57.56, retention **0.43** | 68 trades, PF 1.146, **+56.78**, retention **1.00** |
| `monte_carlo` | BTCUSD | P=0.833, median +68.21, worst DD 156.2 | P=0.369, median −29.40, worst DD 311.8 |
| `monte_carlo` | XAUUSD | P=0.616, median +27.93 | P=0.538, median +14.55 |
| `parameter_robustness` | BTCUSD | dispersion 0.222 | dispersion 0.443 |
| `parameter_robustness` | XAUUSD | dispersion 0.620 | dispersion 1.173 |

Selected candidate changed on XAUUSD: `balanced-2R` → `fast-1.5R`. BTCUSD kept `fast-1.5R`.
Correlation BTCUSD/XAUUSD: **+0.333** over 2 637 bars → **+0.325** over 8 129 bars (both `ok`).

---

## 3. p-values and the multiple-testing correction

Correction actually applied: **Benjamini–Hochberg**, α = 0.10, 6 hypotheses (one per market×candidate).
Bonferroni threshold reported for comparison: **0.016667**.

| hypothesis | p BEFORE | p AFTER |
|---|---|---|
| BTCUSD:fast-1.5R | 0.1638 | 0.6344 |
| BTCUSD:balanced-2R | **0.9780** | 0.5814 |
| BTCUSD:slow-2.5R | 0.8032 | **0.8631** |
| XAUUSD:fast-1.5R | 0.8022 | 0.4565 |
| XAUUSD:balanced-2R | 0.2957 | 0.7502 |
| XAUUSD:slow-2.5R | 0.4865 | **0.3497** |
| **min p** | **0.1638** | **0.3497** |
| **survivors after correction** | **0 / 6** | **0 / 6** |

`discoveries_before = 6` means "six hypotheses carried a measured p-value", **not** "six discoveries".
Survivors after correction: **zero, before and after.** The smallest p-value actually moved *away*
from significance (0.164 → 0.350), i.e. ×21 above the Bonferroni line instead of ×10.

---

## 4. The confound nobody asked about: the windows are not the same

`split_dataset` cuts the series by **fraction from the start** (60 % train / 20 % validation /
20 % sealed holdout). Prepending older history therefore slides every window *backwards in time*
instead of extending the newest one:

| window | BEFORE XAUUSD | AFTER XAUUSD |
|---|---|---|
| train | 2026-08-06 → 09-11 (2 399) | 2026-04-07 → 07-27 (7 199) |
| validation (p-value + costs + MC) | 2026-09-11 → 09-24 (799) | 2026-07-27 → 09-01 (2 399) |
| sealed holdout (out_of_sample) | 2026-09-24 → 10-07 (801) | 2026-09-01 → 10-08 (2 401) |

The before and after measurements therefore read **different market periods**, not the same period
with more data. This is not a controlled experiment and the deltas above must not be read as
"more data ⇒ better".

### The walk-forward "pass" is not a sample-size effect

`WalkForwardPlan(train_bars=350, validation_bars=250, step_bars=200, max_folds=6)` and
`protocol.walk_forward` starts at `start = 0` and **breaks at `max_folds`**. Consequence:

- The fold count is **6 before and 6 after**, never ~18. `max_folds=6` is a hard cap; the
  11 999-bar series cannot produce more folds than the 3 999-bar one. The hypothesis
  "6 folds → ~18 folds" is **false for this implementation**.
- The six folds still cover only bars 0–1 600 — the *oldest* slice of each series. In the AFTER
  XAUUSD set that is **2026-04-13 → 05-01**; in the BEFORE set it was **2026-08-12 → 08-31**.
  The two fold sets **do not overlap at all**.

So `walk_forward` went 0.17 → 0.50 on a *different, disjoint 19-day window* of the same size, and
0.50 is exactly the threshold (`ratio >= 0.5`). It is a pass by the letter of the rule, on one
coin-flip of six folds; it carries no evidence that a longer history helped.

---

## 5. Verdict

**No — a longer history did not rescue the campaign.**

- Gate count is **identical**: 2 passed, 5 failed, 2 not evaluable, before and after.
- The two swaps cancel: `walk_forward` failed → passed (exactly on the threshold, on a disjoint
  window, still 6 folds), `monte_carlo` passed → failed (BTCUSD P(profit) 0.833 → 0.369).
- The one part of the hypothesis that **is** confirmed: the out-of-sample sample really did
  roughly triple — **18 → 62** trades (BTCUSD, ×3.4) and **14 → 68** (XAUUSD, ×4.9), and in-sample
  60 → 208 / 38 → 185. The "too few operations" complaint is genuinely gone.
- But `out_of_sample` **still fails**, now on BTCUSD: retention 0.00 (PF 0.951, −18.98) while
  XAUUSD clears it (retention 1.00, PF 1.146, +56.78). More trades produced a sharper, not a
  kinder, answer on BTCUSD.
- The statistical evidence got **weaker**, not stronger: minimum p-value 0.164 → 0.350,
  **0 of 6 hypotheses survive** the correction in both runs, ×10 above the Bonferroni line before
  and ×21 after.
- The cost-sensitive gates (`costs`, `stress`) and `parameter_robustness` moved in the **wrong**
  direction on both markets (PF 1.34 → 0.81 on BTCUSD; dispersion 0.62 → 1.17 on XAUUSD).
- The two gates a backtest cannot decide (`paper`, `risk`) stay not evaluable — unchanged, as they
  must.

---

## 6. Left aside, and why

1. **The 3 999-bar baseline was archived, not deleted** — `docs/research/datasets/archive-3999bars/`
   and `docs/research/archive/2026-10-08-campaign-3999bars.json`. The store forbids two datasets for
   one symbol, and destroying the "before" would have made this comparison unverifiable.
2. **`docs/research/decisions/witness@1.1.0.json` was rewritten** by the campaign run (promotion still
   refused, reasons recomputed). `docs/research/thresholds.json` was rewritten with identical bytes
   (same digest `a2de0101d321…`). Both are campaign outputs, not hand edits.
3. **`docs/research/README.md` is now stale** — it documents the 3 999-bar campaign. It is hand-written
   (no script generates it), so nothing rewrote it; updating it is a documentation decision for the Lead.
4. **The 11 999-vs-12 000 shortfall** and the calendar holes (XAUUSD 58, BTCUSD 9) are recorded, not
   repaired: project policy is to count holes against the market calendar, never to fill them.
   They are 0.48 % / 0.08 % of the bars, so they do not move any figure above.
5. **No threshold was lowered, no code was changed, nothing was committed.**
6. Making this a clean experiment would need a code change outside my remit — either a
   `max_folds=None` walk-forward plan, or an anchored split that keeps the newest window fixed while
   prepending history. Both are proposals for the Lead, not actions taken.
