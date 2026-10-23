"""
weak_supervision.py
-------------------
Snorkel-style weak supervision (pure stdlib). Your existing lexicons ARE
labeling functions; this formalizes them so you can programmatically label lots
of synthetic / unlabeled caregiver text and train the interpretable baseline
(baseline_nb) without hand-labeling everything.

  * LabelingFunction   - name + fn(text) -> label str OR None (ABSTAIN).
  * LabelModel         - combines LF votes. Majority vote by default; if a small
                         labeled dev set is given it estimates each LF's accuracy
                         and does an accuracy-WEIGHTED vote (a light generative
                         label model). Reports coverage / conflict / accuracy.
  * make_training_set  - apply the model to raw texts -> (text, label) pairs for
                         the baseline, keeping only confident (non-abstain) rows.

Deterministic, no ML libs, no network. Weak labels are for TRAINING the advisory
NLP only; nothing here touches the decision engine.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ABSTAIN = None


@dataclass
class LabelingFunction:
    name: str
    fn: Callable[[str], Optional[str]]

    def __call__(self, text: str) -> Optional[str]:
        try:
            return self.fn(text)
        except Exception:
            return ABSTAIN


def keyword_lf(name: str, label: str, keywords: Sequence[str]) -> LabelingFunction:
    kws = [k.lower() for k in keywords]
    def _fn(text: str) -> Optional[str]:
        t = (text or "").lower()
        return label if any(k in t for k in kws) else ABSTAIN
    return LabelingFunction(name, _fn)


@dataclass
class LabelModel:
    lfs: List[LabelingFunction]
    weights: Dict[str, float] = field(default_factory=dict)   # per-LF accuracy weight

    # --- analysis ---------------------------------------------------------
    def votes(self, text: str) -> Dict[str, Optional[str]]:
        return {lf.name: lf(text) for lf in self.lfs}

    def coverage(self, texts: Sequence[str]) -> Dict[str, float]:
        n = len(texts) or 1
        fired = Counter()
        for t in texts:
            for lf in self.lfs:
                if lf(t) is not ABSTAIN:
                    fired[lf.name] += 1
        return {lf.name: round(fired[lf.name] / n, 3) for lf in self.lfs}

    def estimate_accuracies(self, texts: Sequence[str], gold: Sequence[str]) -> Dict[str, float]:
        """Empirical accuracy of each LF where it fires (on a labeled dev set).
        Sets weights used by the weighted vote."""
        correct = Counter(); total = Counter()
        for t, g in zip(texts, gold):
            for lf in self.lfs:
                v = lf(t)
                if v is not ABSTAIN:
                    total[lf.name] += 1
                    if v == g:
                        correct[lf.name] += 1
        self.weights = {lf.name: (correct[lf.name] / total[lf.name]) if total[lf.name] else 0.5
                        for lf in self.lfs}
        return self.weights

    # --- prediction -------------------------------------------------------
    def predict(self, text: str) -> Tuple[Optional[str], float]:
        """(label, confidence). Accuracy-weighted vote when weights are set,
        else majority vote. confidence = winning mass / total mass (0 if no LF
        fired -> ABSTAIN)."""
        tally: Dict[str, float] = defaultdict(float)
        for lf in self.lfs:
            v = lf(text)
            if v is not ABSTAIN:
                tally[v] += self.weights.get(lf.name, 1.0)
        if not tally:
            return ABSTAIN, 0.0
        total = sum(tally.values())
        label, mass = max(tally.items(), key=lambda kv: kv[1])
        return label, round(mass / total, 3)

    def score(self, texts: Sequence[str], gold: Sequence[str]) -> Dict[str, float]:
        pred = [self.predict(t)[0] for t in texts]
        covered = [(p, g) for p, g in zip(pred, gold) if p is not ABSTAIN]
        acc = sum(1 for p, g in covered if p == g) / (len(covered) or 1)
        return {"coverage": round(len(covered) / (len(texts) or 1), 3),
                "accuracy_where_covered": round(acc, 3), "n": len(texts)}


def make_training_set(model: LabelModel, texts: Sequence[str],
                      min_confidence: float = 0.5) -> List[Tuple[str, str]]:
    """Weakly-label raw texts; keep confident, non-abstain rows for training."""
    rows = []
    for t in texts:
        label, conf = model.predict(t)
        if label is not ABSTAIN and conf >= min_confidence:
            rows.append((t, label))
    return rows
