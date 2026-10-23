"""
validation/leakage_safe_cv.py
-----------------------------
Leakage-safe cross-validation utilities for the advisory NLP model comparison
(e.g. HUMADEX vs ClinicalBERT vs fine-tuned candidates).

WHY THIS EXISTS
  The synthetic/templated datasets used by nlp_synthetic_eval*.py and
  nlp_finetune_compare.py are generated from a small set of phrase-bank
  templates. Many items are near-duplicates of one another. If you split such
  a set at RANDOM (plain k-fold), near-identical twins land in both train and
  test, so a model can score highly by MEMORIZING templates without learning
  anything that generalizes to how real caregivers write. nlp_finetune_compare.py
  says exactly this in its own docstring. This is the classic ML leakage failure
  described in Lopez de Prado, "Advances in Financial Machine Learning" (ch. 7):
  standard cross-validation is optimistically biased when samples are not
  independent. The fix there (purged / grouped CV) transfers directly here.

WHAT THIS PROVIDES  (all stdlib-only; no numpy/sklearn/torch)
  * naive_kfold(n, ...)          - plain random k-fold split (the leaky baseline).
  * group_kfold(groups, ...)     - keeps every item sharing a group id in the
                                   SAME fold, so template families never straddle
                                   train/test. Groups are balanced across folds.
  * purge_train(...)             - removes train items too textually similar to
                                   any test item (token-Jaccard >= threshold),
                                   i.e. Lopez de Prado "purging" of leaked overlap.
  * template_group_key(text)     - derive a group id from a text's normalized
                                   skeleton (numbers/spacing/punct stripped) so
                                   paraphrase-variants of one template cluster.
  * cross_val(splits, eval_fold) - run any per-fold eval fn over a set of splits
                                   and aggregate F1 with a mean +/- spread.
  * optimism_gap(...)            - headline number: F1 under naive k-fold minus
                                   F1 under grouped+purged k-fold. A large gap ==
                                   that much of your reported score was leakage.

DECISION-PATH NOTE
  Advisory / evaluation only. Nothing here is imported by engine_core, and no
  output of this module ever becomes an engine input. It measures the NLP layer;
  it does not participate in any clinical decision. Simulated data only.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from random import Random
from typing import Callable, Dict, List, Sequence, Set, Tuple

Split = Tuple[List[int], List[int]]  # (train_idx, test_idx)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_TOK = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> Set[str]:
    return set(_TOK.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity of two strings (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0


def template_group_key(text: str) -> str:
    """Collapse a sentence to a coarse 'template skeleton' so paraphrase/number
    variants of the same underlying template share a group id.

    Digits -> #, runs of whitespace -> single space, punctuation dropped, lower.
    e.g. 'lasts about 2 to 3 minutes, 3 times a day' and
         'lasts about 4 to 5 minutes, 2 times a day'  ->  same key.
    """
    t = text.lower()
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[^a-z#\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Splitters
# ---------------------------------------------------------------------------
def naive_kfold(n: int, n_splits: int = 5, seed: int = 0) -> List[Split]:
    """Plain random k-fold over n items. This is the LEAKY baseline used only
    to measure how optimistic random splitting is versus group_kfold."""
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    idx = list(range(n))
    Random(seed).shuffle(idx)
    folds = [idx[k::n_splits] for k in range(n_splits)]
    return _folds_to_splits(folds, n_splits)


def group_kfold(groups: Sequence, n_splits: int = 5, seed: int = 0) -> List[Split]:
    """K-fold that never splits a group across train/test. Groups are assigned
    to folds greedily (largest first) to keep fold sizes balanced; ties broken
    by a seeded shuffle so different seeds give different (still valid) folds."""
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    freq = Counter(groups)
    uniq = list(freq)
    Random(seed).shuffle(uniq)
    uniq.sort(key=lambda g: -freq[g])          # largest groups placed first
    fold_load = [0] * n_splits
    fold_of: Dict = {}
    for g in uniq:
        f = min(range(n_splits), key=lambda k: fold_load[k])
        fold_of[g] = f
        fold_load[f] += freq[g]
    folds: List[List[int]] = [[] for _ in range(n_splits)]
    for i, g in enumerate(groups):
        folds[fold_of[g]].append(i)
    return _folds_to_splits(folds, n_splits)


def _folds_to_splits(folds: List[List[int]], n_splits: int) -> List[Split]:
    splits: List[Split] = []
    for k in range(n_splits):
        test = sorted(folds[k])
        train = sorted(i for j in range(n_splits) if j != k for i in folds[j])
        splits.append((train, test))
    return splits


def purge_train(train_idx: Sequence[int], test_idx: Sequence[int],
                texts: Sequence[str], threshold: float = 0.8) -> List[int]:
    """Drop train items whose token-Jaccard to ANY test item is >= threshold.
    This is Lopez de Prado 'purging': remove train samples that overlap the
    test set so the score can't be inflated by near-duplicate leakage."""
    test_tok = [_tokens(texts[i]) for i in test_idx]
    kept: List[int] = []
    for i in train_idx:
        ti = _tokens(texts[i])
        leaked = False
        for tt in test_tok:
            union = ti | tt
            if union and len(ti & tt) / len(union) >= threshold:
                leaked = True
                break
        if not leaked:
            kept.append(i)
    return kept


# ---------------------------------------------------------------------------
# Cross-validation driver
# ---------------------------------------------------------------------------
def cross_val(splits: Sequence[Split],
              eval_fold: Callable[[List[int], List[int]], float]) -> Dict[str, float]:
    """Run eval_fold(train_idx, test_idx) -> F1 (or any score) over every split
    and aggregate. eval_fold owns fitting/predicting/scoring for its fold."""
    scores = [eval_fold(list(tr), list(te)) for tr, te in splits]
    return {
        "mean": round(statistics.mean(scores), 4),
        "stdev": round(statistics.stdev(scores), 4) if len(scores) > 1 else 0.0,
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "folds": [round(s, 4) for s in scores],
    }


def optimism_gap(n: int, groups: Sequence, texts: Sequence[str],
                 eval_fold: Callable[[List[int], List[int]], float],
                 n_splits: int = 5, seed: int = 0,
                 purge_threshold: float = 0.8) -> Dict[str, object]:
    """Headline diagnostic. Runs the SAME eval_fold under:
        (a) naive random k-fold          -> leaky, optimistic
        (b) grouped k-fold               -> template families held out
        (c) grouped k-fold + purge       -> plus near-duplicate purging
    and reports the gap. A large 'gap_naive_minus_grouped_purged' means that
    much of a random-split score is memorization, not generalization."""
    naive = cross_val(naive_kfold(n, n_splits, seed), eval_fold)
    grouped_splits = group_kfold(groups, n_splits, seed)
    grouped = cross_val(grouped_splits, eval_fold)

    def _purged(tr: List[int], te: List[int]) -> float:
        return eval_fold(purge_train(tr, te, texts, purge_threshold), te)

    grouped_purged = cross_val(grouped_splits, _purged)
    gap = round(naive["mean"] - grouped_purged["mean"], 4)
    return {
        "naive_kfold": naive,
        "group_kfold": grouped,
        "group_kfold_purged": grouped_purged,
        "gap_naive_minus_grouped_purged": gap,
        "interpretation": (
            f"~{round(gap * 100, 1)} F1 points of the naive-split score come from "
            f"leakage (template/near-duplicate overlap), not generalization."
        ),
        "n_items": n,
        "n_groups": len(set(groups)),
        "n_splits": n_splits,
    }


# ---------------------------------------------------------------------------
# Self-contained demonstration (no transformers/torch, simulated data only)
# ---------------------------------------------------------------------------
def _demo() -> Dict[str, object]:
    """Build a deliberately LEAKY templated dataset and show, with a trivial
    exact-match 'memorizer' model, that naive k-fold massively overstates
    performance while grouped+purged k-fold reveals the true (poor) score."""
    rng = Random(7)
    templates = [
        ("lasts about {a} to {b} minutes and happens {c} times a day", "over_3min"),
        ("only {a} seconds, maybe once a week", "under_1min"),
        ("really drowsy and tired since we upped the dose to {a} mg", "somnolence"),
        ("a red rash spreading on {a} arm", "spreading_rash"),
        ("stared blankly for {a} seconds, {c} episodes today", "under_1min"),
        ("stopped breathing for {a} seconds during the seizure", "breathing"),
    ]
    texts: List[str] = []
    labels: List[str] = []
    groups: List[str] = []
    for _ in range(240):                       # many near-duplicate variants
        tpl, lab = rng.choice(templates)
        txt = tpl.format(a=rng.randint(1, 5), b=rng.randint(2, 6), c=rng.randint(1, 6))
        texts.append(txt)
        labels.append(lab)
        groups.append(template_group_key(txt))

    def eval_fold(train_idx: List[int], test_idx: List[int]) -> float:
        # "Memorizer": predict the label of the exact-matching train text if seen,
        # else fall back to the single most common train label. On leaky splits it
        # sees test twins in train and looks great; held out, it cannot generalize.
        seen = {texts[i]: labels[i] for i in train_idx}
        fallback = Counter(labels[i] for i in train_idx).most_common(1)[0][0]
        gold = [{(labels[i],)} for i in test_idx]
        pred = [{(seen.get(texts[i], fallback),)} for i in test_idx]
        return _micro_f1(gold, pred)

    return optimism_gap(len(texts), groups, texts, eval_fold, n_splits=5, seed=1)


def _micro_f1(gold_docs: Sequence[Set], pred_docs: Sequence[Set]) -> float:
    tp = fp = fn = 0
    for g, p in zip(gold_docs, pred_docs):
        tp += len(g & p)
        fp += len(p - g)
        fn += len(g - p)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def write_report(result: Dict[str, object], path: str) -> str:
    """Persist a gap result as an ENGINEERING validation artifact (JSON).
    This is QA on the NLP eval, not a clinical artifact: it must not be placed
    in KRISTEN_CLINICAL_REVIEW.md, NEEDS_PI_SIGNOFF.md, or any clinician-facing
    output. Home is validation/."""
    import json
    payload = dict(result)
    payload["_note"] = ("ENGINEERING / eval-QA artifact. Advisory NLP layer only; "
                        "never touches engine_core. Not for clinical review or the "
                        "clinician UI. Simulated data only.")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


if __name__ == "__main__":
    import argparse
    import json
    import os

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None,
                    help="write the gap report JSON to this path "
                         "(default: validation/nlp_leakage_report.json)")
    args = ap.parse_args()

    result = _demo()
    print(json.dumps(result, indent=2))
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "nlp_leakage_report.json")
    write_report(result, out)
    print(f"\n[engineering artifact written] {out}")
