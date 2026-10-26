# Validation Plan - Aim 2 ASM Titration Engine

Simulated data only until IRB + HIPAA/CITI complete. This plan
pre-registers *how* the engine is evaluated so the metrics are reproducible. The
toolkit is `aim2_engine/validation/metrics.py`; the runner is `harness.py`.

## 1. Frame

This is an **agreement / classification** validation (engine output vs. a clinician
reference standard), not a treatment comparison. Significance tests (t-test,
chi-square of independence) do not apply to the core question. The headline is
**agreement coefficients with confidence intervals**, not p-values.

## 2. Reference standard (the part that matters most)

- **Two pediatric epileptologists** independently code each case, **blinded to the
  engine** and to each other.
- Compute **human-vs-human weighted kappa** - this is the **ceiling**. The engine is
  judged *relative to it*: matching two experts who agree at κ = 0.65 with κ ≈ 0.6
  is a success, not a failure. Always report engine-vs-human next to human-vs-human.
- Resolve the two raters' disagreements by adjudication into a **consensus
  reference**; report engine-vs-consensus as the primary, and engine-vs-each-rater
  as sensitivity analyses.

## 3. Pre-registered conventions (fix before scoring)

- **Deferral handling:** a deferral (`insufficient_data`) is **excluded from the
  categorical accuracy/kappa denominator** and analyzed separately (§5). (The
  alternative - deferral as a third class - is available via `deferral_mode="class"`
  but is *not* the primary analysis.)
- **Weighted-kappa ordering:** ordinal severity of disagreement is encoded with
  linear weights over a clinician-confirmed order (default in `metrics.py`:
  lower_dose < no_change < hold < slower_uptitration < escalate < add < switch).
  **Dr. Barbour to confirm this ordering** (it is a methods choice, not yet verified).

## 4. Metrics to report (always)

- **Categorical recommendation:** confusion matrix; observed agreement + class
  prevalence; **weighted** Cohen's kappa (+ plain kappa and **PABAK** to expose the
  kappa paradox under class imbalance); **macro-averaged** precision/recall/F1 (so
  the rare, high-stakes classes like *switch* count). Do **not** lead with overall
  accuracy.
- **Binary safety flag:** **sensitivity and specificity separately**; the
  **false-negative rate as its own headline** (it is the actual harm); plus NPV.
- **Continuous dose (if/when the engine emits a number):** **Bland-Altman** (bias +
  95% limits of agreement) and **ICC(2,1)**. Never Pearson r as an agreement metric.
- **Confidence intervals on everything:** bootstrap for kappa/F1; **Wilson** for all
  proportions (sensitivity/specificity/FNR). A point estimate without a CI at this N
  is not interpretable.
- **Deferral quality:** cross-tab *engine deferred* × *humans disagreed*; **Fisher's
  exact** (not chi-square - expected cells < 5). A good engine defers precisely where
  the experts split.

## 5. Sample-size targets (why the current run is underpowered)

Safety is the binding constraint. With **0 observed misses in n positive safety
cases**, the 95% upper bound on the miss rate is ≈ **3/n** (rule of three):

| True-positive safety cases | 95% upper bound on missed-safety rate |
|---|---|
| 5 (current) | ~0.45 - uninformative |
| 30 | ~0.10 |
| 60 | ~0.05 |
| 100 | ~0.03 |

So to *claim* a ≤5% missed-safety-event rate, we need ≈**60 true safety-positive
cases**, not 5. For the categorical metrics, ensure the rare classes (especially
*switch* / *add*) have enough support for stable macro-F1 (target ≳10–15 each), and
expect wide kappa CIs until total N is in the low hundreds.

## 6. Current status - 2026-10-26, engine 0.4.3

Synthetic gold set, 63/63:

- Cohen's κ **1.0** (target ≥ 0.8, MET) · concordance **1.0**
- weighted κ **1.0** with the 10 deferrals excluded · PABAK 1.0 · macro-F1 1.0
- **5** safety-concern outputs. §5 still needs ~60 true safety-positive cases
  before a ≤5% miss-rate claim

Zero disagreement at this N does not support a miss-rate claim.

Next steps: (a) two-rater chart-review coding for a real reference + ceiling,
(b) expand safety-positive cases toward the table above, (c) PI confirms the
weighted-kappa ordering.

**Tooling caveat:** `validation/confusion_analysis.py` - the script that produced
the safety sensitivity/specificity + FNR figures against the answer key - does not
currently run. The answer key is intact at
`aim2_engine/validation/private/INTERNAL_engine_answer_key_DO_NOT_SEND.csv`; the
script's line 32 just looks for it at the project root. A one-line path fix
restores those metrics - do that before quoting safety sens/spec/FNR again.
