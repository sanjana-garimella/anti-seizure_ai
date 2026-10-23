"""
nlp/conformal.py
----------------
Distribution-free coverage + principled abstention for the advisory NLP
red-flag layer (Point B). This is the rigorous version of the project's
"defer on ambiguity" rule: instead of one hand-picked score cutoff, split
conformal prediction gives a FINITE-SAMPLE, distribution-free guarantee on how
often a truly urgent red flag is missed, and emits a "defer to human" band
whenever the evidence is ambiguous.

WHY THIS FITS THE ARCHITECTURE (clinical-safety.md, NON_NEGOTIABLE_RULES.md)
  * ADVISORY ONLY. Consumes scores from an existing detector (redflags.py or a
    classifier implementing the RedFlagDetector protocol) and returns one of
    {FLAG, CLEAR, DEFER}. It never edits a recommendation and never writes into
    engine_core/ inputs. FLAG / DEFER escalate to human review; CLEAR only
    means "the safety-net did not fire" (the structured engine path is
    unaffected).
  * DEFER = abstention. When both labels are plausible under the calibrated
    thresholds, the sample is routed to a human rather than guessed.
  * Pure stdlib, deterministic, no network, no ML libraries.

GUARANTEE (class-conditional / Mondrian split conformal)
  Calibrated at miss-rate level alpha on a held-out calibration set, then for a
  new sample drawn from the same distribution:

      P( decision != CLEAR | sample is truly urgent )  >=  1 - alpha

  i.e. the miss rate on true red flags is bounded by alpha (marginally), with
  the standard finite-sample (n+1) correction. Class-conditional calibration is
  used so the guarantee holds on the rare positive class regardless of
  prevalence -- exactly what a never-miss safety net needs.

  Reference (approach, not code): Angelopoulos & Bates, "Conformal Prediction:
  A Gentle Introduction" (2402.12683 is the TorchCP toolkit built on this);
  conformal risk control, Angelopoulos et al. 2022. This module is a stdlib
  re-implementation of split conformal so the no-dependency engine can run it;
  TorchCP/MAPIE can be swapped in behind the same ConformalRedFlag API.

  NB: the guarantee is only as good as the calibration set. Until IRB + real
  labels exist, calibrate on SIMULATED data only (clinical-safety.md) and treat
  the reported bound as DRAFT / unverified.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

FLAG = "FLAG"       # safety-net fired -> urgent human review
CLEAR = "CLEAR"     # safety-net did not fire (engine path unaffected)
DEFER = "DEFER"     # ambiguous -> route to human (abstention)


def _lower_quantile_idx(n: int, beta: float) -> int:
    """0-based index of the beta lower-quantile of n ascending values, with the
    conformal (n+1) finite-sample correction. E.g. beta=alpha -> the value below
    which at most a beta fraction of the population is expected to fall."""
    if n == 0:
        return 0
    k = math.ceil((n + 1) * beta) - 1
    return max(0, min(n - 1, k))


def _upper_quantile_idx(n: int, beta: float) -> int:
    """0-based index of the beta UPPER-quantile (only a beta fraction expected
    above it), conformal-corrected."""
    if n == 0:
        return 0
    k = math.ceil((n + 1) * (1.0 - beta)) - 1
    return max(0, min(n - 1, k))


@dataclass
class ConformalRedFlag:
    """Split-conformal decision layer over a red-flag score in [0, 1].

    alpha_miss : max tolerated miss rate on TRUE red flags (e.g. 0.05 -> >=95%
                 recall guarantee). This is the safety-critical knob.
    alpha_fp   : optional symmetric bound used only to define the CLEAR region;
                 controls how confidently-negative a score must be to be called
                 CLEAR (else DEFER). Larger alpha_fp -> more DEFER (more caution).
    """
    alpha_miss: float = 0.05
    alpha_fp: float = 0.05
    # fitted thresholds (None until calibrate()):
    tau_flag: Optional[float] = None    # score >= tau_flag  -> positive is plausible
    tau_clear: Optional[float] = None   # score <  tau_clear -> negative is plausible
    n_pos: int = 0
    n_neg: int = 0

    def calibrate(self, scores: Sequence[float], labels: Sequence[int]) -> "ConformalRedFlag":
        """Class-conditional calibration.

        tau_flag  = lower cutoff s.t. >= (1-alpha_miss) of TRUE positives score
                    at/above it  (guarantees recall >= 1-alpha_miss).
        tau_clear = upper cutoff s.t. >= (1-alpha_fp) of TRUE negatives score
                    below it.
        A score in [tau_flag, tau_clear) is plausibly EITHER class -> DEFER."""
        pos = sorted(float(s) for s, y in zip(scores, labels) if y == 1)
        neg = sorted(float(s) for s, y in zip(scores, labels) if y == 0)
        self.n_pos, self.n_neg = len(pos), len(neg)
        # tau_flag = alpha_miss lower-quantile of TRUE-positive scores: a true
        # positive lands at/above it with prob >= 1 - alpha_miss (recall guar.).
        self.tau_flag = pos[_lower_quantile_idx(len(pos), self.alpha_miss)] if pos else 0.0
        # tau_clear = alpha_fp upper-quantile of TRUE-negative scores: a true
        # negative lands BELOW it with prob >= 1 - alpha_fp.
        self.tau_clear = neg[_upper_quantile_idx(len(neg), self.alpha_fp)] if neg else 1.0
        # If the classes separate well, tau_flag may exceed tau_clear. We do NOT
        # swap: a score in the gap [tau_clear, tau_flag) is plausibly NEITHER,
        # which prediction_set() turns into an empty set -> escalate (FLAG),
        # never a silent CLEAR. Overlap (tau_flag < tau_clear) yields the DEFER
        # band, as intended.
        return self

    def prediction_set(self, score: float) -> List[str]:
        """Conformal prediction set: which labels are plausible for this score."""
        s = float(score)
        labels = []
        if self.tau_flag is not None and s >= self.tau_flag:
            labels.append("urgent")
        if self.tau_clear is not None and s < self.tau_clear:
            labels.append("not_urgent")
        return labels or ["urgent"]   # empty set -> escalate (never silently drop)

    def decide(self, score: float) -> dict:
        """Map the prediction set to an action. Two plausible labels -> DEFER."""
        pset = self.prediction_set(score)
        if pset == ["urgent"]:
            decision = FLAG
        elif pset == ["not_urgent"]:
            decision = CLEAR
        else:
            decision = DEFER
        return {
            "decision": decision,
            "score": round(float(score), 4),
            "prediction_set": pset,
            "tau_flag": self.tau_flag,
            "tau_clear": self.tau_clear,
            "provenance": "conformal red-flag (advisory) — draft, unverified",
        }

    # ---- empirical validation on a held-out set (reports the realized rates) --
    def coverage_report(self, scores: Sequence[float], labels: Sequence[int]) -> dict:
        miss = total_pos = fp = total_neg = defer = 0
        for s, y in zip(scores, labels):
            d = self.decide(s)["decision"]
            if y == 1:
                total_pos += 1
                if d == CLEAR:
                    miss += 1
            else:
                total_neg += 1
                if d == FLAG:
                    fp += 1
            if d == DEFER:
                defer += 1
        n = len(scores)
        return {
            "n": n,
            "target_miss_rate": self.alpha_miss,
            "empirical_miss_rate": round(miss / total_pos, 4) if total_pos else None,
            "empirical_flag_fp_rate": round(fp / total_neg, 4) if total_neg else None,
            "defer_rate": round(defer / n, 4) if n else None,
            "guarantee_met": (miss / total_pos <= self.alpha_miss) if total_pos else None,
        }


# ---------------------------------------------------------------------------
# Self-contained demo / smoke: synthetic two-Gaussian scores show the empirical
# miss rate on true red flags stays at/under the target alpha on held-out data.
# ---------------------------------------------------------------------------
def _demo() -> None:
    import random
    random.seed(7)

    def synth(n_pos, n_neg):
        scores, labels = [], []
        for _ in range(n_pos):     # urgent: high scores
            scores.append(min(1.0, max(0.0, random.gauss(0.75, 0.15)))); labels.append(1)
        for _ in range(n_neg):     # not urgent: low scores
            scores.append(min(1.0, max(0.0, random.gauss(0.30, 0.15)))); labels.append(0)
        return scores, labels

    cal_s, cal_y = synth(300, 1200)      # imbalanced, like real red flags
    te_s, te_y = synth(300, 1200)

    print("Conformal red-flag layer — synthetic demo (DRAFT/unverified)")
    for a in (0.10, 0.05, 0.01):
        crf = ConformalRedFlag(alpha_miss=a, alpha_fp=0.10).calibrate(cal_s, cal_y)
        rep = crf.coverage_report(te_s, te_y)
        print(f"  alpha_miss={a:<4}  tau_flag={crf.tau_flag:.3f} tau_clear={crf.tau_clear:.3f}"
              f"  ->  miss={rep['empirical_miss_rate']}  fp={rep['empirical_flag_fp_rate']}"
              f"  defer={rep['defer_rate']}  guarantee_met={rep['guarantee_met']}")
    print("  (miss rate should sit at/under alpha_miss; tighter alpha -> more DEFER)")


if __name__ == "__main__":
    _demo()
