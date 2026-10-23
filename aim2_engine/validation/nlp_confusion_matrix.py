"""
validation/nlp_confusion_matrix.py
------------------------------------
Full label x label confusion matrix for the point-A structurers, built on
top of the templated gold-labeled dataset from nlp_synthetic_eval_large.py
(reused rather than re-authored - same phrase banks / case generator).

WHY A SEPARATE SCRIPT FROM THE EXISTING EVALS: nlp_synthetic_eval.py and
nlp_synthetic_eval_large.py already report micro precision/recall/F1 and a
per-label (tp/fp/fn) breakdown - useful summaries, but they collapse each
label's errors into a single number. A confusion matrix answers a different,
more diagnostic question: when a structurer gets something wrong, WHAT does
it say instead? e.g. is "dizziness" specifically getting mistaken for
"sleepiness"? Is "behavior" swallowing what should be "irritability"? That
pattern is invisible in a plain fp/fn count but obvious in a confusion matrix.

WHY THIS CAN ONLY RUN ON THE SYNTHETIC/TEMPLATED DATA, NOT THE REAL PUBMED OR
ADE-CORPUS-V2 DATA: a confusion matrix needs true labels for both axes. The
real-text evals (nlp_external_eval.py, nlp_pubmed_eval.py) were deliberately
scoped to recall-style proxies precisely because they have NO per-case gold
label set - there's nothing to build a real matrix from there. This is the
one gold-labeled slice of this whole investigation, so it's the one place a
genuine confusion matrix is possible.

HOW THE MATRIX IS BUILT (multi-label, no gold SPANS, only gold LABEL SETS per
example - see nlp_synthetic_eval.py's docstring on this project's scoring
convention): for each example, exact (gold, pred) label matches go on the
diagonal. Leftover missed gold labels and leftover spurious predicted labels
are then RECONCILED into off-diagonal (gold -> predicted-instead) pairs:
  1. First pass pairs leftovers that share the same coarse field (e.g. two
     side_effect subtypes) - this is the trustworthy signal: a same-field
     substitution genuinely suggests the model confused two similar concepts.
  2. Any leftovers still unpaired after that get paired across fields in
     arbitrary (sorted) order - this is a WEAKER, order-dependent artifact of
     the greedy reconciliation, not a real substitution claim. Printed
     separately and flagged as such.
  3. Anything left with no partner at all becomes a genuine miss (gold -> "O")
     or false alarm ("O" -> predicted), same meaning as fn/fp elsewhere.

DECISION-PATH NOTE: advisory/diagnostic only. Does not touch engine_core.

Run:
  cd aim2_engine
  python3 validation/nlp_confusion_matrix.py
  python3 validation/nlp_confusion_matrix.py --n 5000 --transformer-sample 800
  python3 validation/nlp_confusion_matrix.py --no-transformers   # lexicon only, fast
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> aim2_engine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))                   # -> validation/

from nlp.structurer import LexiconStructurer                     # noqa: E402
from nlp_synthetic_eval_large import gen_dataset                 # noqa: E402

NONE_LABEL = "O"   # "nothing" - matches BIO convention, used on either axis

# Canonical label order + compact display names for the grid.
_LABEL_ORDER = [
    "side_effect:sleepiness", "side_effect:rash", "side_effect:dizziness",
    "side_effect:irritability", "side_effect:nausea", "side_effect:behavior",
    "seizure_mention", "drug_mention", "test_mention", NONE_LABEL,
]
_SHORT = {
    "side_effect:sleepiness": "sleepy", "side_effect:rash": "rash",
    "side_effect:dizziness": "dizzy", "side_effect:irritability": "irrit",
    "side_effect:nausea": "nausea", "side_effect:behavior": "behav",
    "seizure_mention": "seiz", "drug_mention": "drug",
    "test_mention": "test", NONE_LABEL: "O",
}


def label_of(finding) -> str:
    if finding.field == "side_effect":
        return f"side_effect:{finding.suggested_value}"
    return finding.field


# missed_dose_reason / dose_change are lexicon-only, out-of-scope fields (see
# nlp_synthetic_eval.py's docstring) - filtered out of predictions so a
# sentence that incidentally triggers one (e.g. "was switched to" firing
# dose_change alongside drug_mention) can't masquerade as a real confusion.
_OUT_OF_SCOPE_FIELDS = {"missed_dose_reason", "dose_change"}


def predict_labels(structurer, text) -> set:
    try:
        findings = structurer.structure(text, source_question="Q13")
        return {label_of(f) for f in findings if f.field not in _OUT_OF_SCOPE_FIELDS}
    except Exception as exc:                       # never let the eval crash
        print(f"    ! {structurer.__class__.__name__} raised on {text!r}: {exc}")
        return set()


def _field_of(label: str) -> str:
    return label.split(":")[0]


def _reconcile(missed_gold: list, spurious_pred: list):
    """Pair up leftover (unmatched) gold/pred labels for one example.
    Returns a list of (gold_or_NONE, pred_or_NONE) tuples. See module
    docstring for the two-pass same-field-first strategy."""
    used_g = [False] * len(missed_gold)
    used_p = [False] * len(spurious_pred)
    pairs = []          # (gold, pred, same_field: bool)

    # Pass 1: same coarse field only (trustworthy substitution signal).
    for gi, g in enumerate(missed_gold):
        if used_g[gi]:
            continue
        for pi, p in enumerate(spurious_pred):
            if not used_p[pi] and _field_of(p) == _field_of(g):
                pairs.append((g, p, True))
                used_g[gi] = used_p[pi] = True
                break

    # Pass 2: cross-field leftovers, arbitrary order (weak signal).
    free_p = [pi for pi, u in enumerate(used_p) if not u]
    for gi, g in enumerate(missed_gold):
        if used_g[gi]:
            continue
        if free_p:
            pi = free_p.pop(0)
            pairs.append((g, spurious_pred[pi], False))
            used_p[pi] = True
        else:
            pairs.append((g, NONE_LABEL, False))   # genuine miss
        used_g[gi] = True

    for pi, u in enumerate(used_p):
        if not u:
            pairs.append((NONE_LABEL, spurious_pred[pi], False))  # genuine false alarm

    return pairs


def build_confusion(structurer, dataset):
    """Returns (matrix[gold][pred] -> count, same_field_flags[(gold,pred)] -> bool)."""
    matrix = defaultdict(Counter)
    same_field_pairs = set()
    for text, gold in dataset:
        pred = predict_labels(structurer, text)
        for l in gold & pred:
            matrix[l][l] += 1
        missed = sorted(gold - pred)
        spurious = sorted(pred - gold)
        for g, p, same_field in _reconcile(missed, spurious):
            matrix[g][p] += 1
            if same_field:
                same_field_pairs.add((g, p))
    return matrix, same_field_pairs


def print_matrix(name, matrix, same_field_pairs, dataset_n):
    present = {l for l in matrix} | {p for row in matrix.values() for p in row}
    rows = [l for l in _LABEL_ORDER if l in present]
    cols = rows  # same label set on both axes

    print(f"\n  === {name}  confusion matrix (n={dataset_n}) ===")
    header = "  gold\\pred".ljust(14) + "".join(f"{_SHORT[c]:>7}" for c in cols)
    print(header)
    for r in rows:
        line = f"  {_SHORT[r]:<12}" + "".join(f"{matrix[r][c]:>7}" if matrix[r][c] else f"{'.':>7}"
                                                for c in cols)
        print(line)

    off_diag = [(g, p, c) for g, row in matrix.items() for p, c in row.items() if g != p and c > 0]
    off_diag.sort(key=lambda x: -x[2])
    if off_diag:
        print("    notable confusions (gold -> predicted-instead), sorted by count:")
        for g, p, c in off_diag[:15]:
            g_disp = "(nothing)" if g == NONE_LABEL else g
            p_disp = "(nothing / missed)" if p == NONE_LABEL else p
            tag = "" if (g, p) in same_field_pairs else "  [cross-field pairing - weaker signal]"
            print(f"      {g_disp:<28} -> {p_disp:<28} x{c}{tag}")
    else:
        print("    no confusions - every gold/pred label matched exactly.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=5000, help="cases for the lexicon baseline")
    ap.add_argument("--transformer-sample", type=int, default=800,
                     help="subsample size for transformer models (cost control)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-transformers", action="store_true")
    args = ap.parse_args()

    print("\n  NLP confusion matrix - Aim 2 point-A structurers (advisory layer only)")
    print("  " + "=" * 78)
    print("  Built on the templated gold-labeled set (nlp_synthetic_eval_large.py's")
    print("  generator) - the only slice of this investigation with real per-case gold")
    print("  labels. See module docstring for the multi-label reconciliation method.\n")

    full = gen_dataset(args.n, seed=args.seed)
    m, sf = build_confusion(LexiconStructurer(), full)
    print_matrix("LexiconStructurer (deterministic baseline)", m, sf, len(full))

    if not args.no_transformers:
        try:
            from nlp.transformer_structurer import (
                BioBERTStructurer, ClinicalBERTStructurer, GenericNERAdapter)
        except ImportError:
            print("\n  transformers/torch not installed - skipping transformer models.")
            BioBERTStructurer = None

        if BioBERTStructurer is not None:
            sample = gen_dataset(args.transformer_sample, seed=args.seed + 1)
            for key, candidate in [
                ("BioBERTStructurer", BioBERTStructurer(lazy=False)),
                ("ClinicalBERTStructurer", ClinicalBERTStructurer(lazy=False)),
                ("d4data/biomedical-ner-all", GenericNERAdapter("d4data/biomedical-ner-all", lazy=False)),
                ("HUMADEX/english_medical_ner", GenericNERAdapter("HUMADEX/english_medical_ner", lazy=False)),
            ]:
                if candidate.is_available():
                    m, sf = build_confusion(candidate, sample)
                    print_matrix(key, m, sf, len(sample))
                else:
                    print(f"\n  {key} unavailable - skipping.")
    print()


if __name__ == "__main__":
    main()
