"""
validation/nlp_naive_bayes_eval.py
-----------------------------------
Evaluates the TF-IDF + Multinomial Naive Bayes baseline (nlp/baseline_nb.py)
on the SAME synthetic extraction task as nlp_synthetic_full_validation.py
(LexiconStructurer, BioBERT, ClinicalBERT, HUMADEX, d4data), so the three
approaches (lexicon / naive bayes / medical transformer) can be compared
on equal footing.

Unlike the other structurers (rule-based or pretrained), Naive Bayes needs
labeled TRAINING data. To keep this a fair, leakage-free comparison, the 1200
generated notes are split 70/30 train/test (different seed partition than the
main eval) - NB trains on the 70% and is evaluated on the held-out 30% only.
The other structurers need no training, so their headline numbers are on the
full 1200; this script also reports the OTHER structurers on the SAME 30%
test split for an apples-to-apples secondary comparison.

Non-PHI, synthetic only. Advisory layer only - never touches engine_core.
Run:  cd aim2_engine && python3 validation/nlp_naive_bayes_eval.py
Writes: validation/nlp_naive_bayes_report.json
"""
from __future__ import annotations

import json
import os
import random
import sys
from typing import Dict, List, Set

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nlp.baseline_nb import TextClassifier                          # noqa: E402
from nlp.structurer import LexiconStructurer                        # noqa: E402
from nlp.interfaces import FreeTextFinding                          # noqa: E402
from validation.nlp_synthetic_full_validation import (              # noqa: E402
    generate_notes, eval_extraction, SEED, N_NOTES, _OUT_OF_SCOPE_FIELDS,
)


class NaiveBayesStructurer:
    """One binary TF-IDF+NB classifier per candidate label. Trained on a
    held-out TRAIN split of the synthetic notes; implements the same
    FreeTextStructurer protocol as LexiconStructurer / TransformerStructurer."""
    model_name = "naive_bayes_tfidf"

    def __init__(self):
        self._clfs: Dict[str, TextClassifier] = {}

    def fit(self, notes: List[Dict]) -> "NaiveBayesStructurer":
        labels: Set[str] = set()
        for n in notes:
            labels |= {g for g in n["gold"] if g.split(":")[0] not in _OUT_OF_SCOPE_FIELDS}
        texts = [n["text"] for n in notes]
        for lab in sorted(labels):
            y = ["pos" if lab in n["gold"] else "neg" for n in notes]
            clf = TextClassifier(min_df=1, alpha=1.0)
            clf.fit(texts, y)
            self._clfs[lab] = clf
        return self

    def is_available(self) -> bool:
        return bool(self._clfs)

    def structure(self, text: str, source_question=None) -> List[FreeTextFinding]:
        out = []
        for lab, clf in self._clfs.items():
            if clf.predict(text) == "pos":
                if lab.startswith("side_effect:"):
                    field, val = "side_effect", lab.split(":", 1)[1]
                else:
                    field, val = lab, lab
                out.append(FreeTextFinding(field=field, suggested_value=val,
                                            confidence=0.6, evidence="[nb]",
                                            source_question=source_question))
        return out


def main() -> int:
    print("NAIVE BAYES (TF-IDF) BASELINE EVAL")
    print("=" * 60)
    all_notes = generate_notes(N_NOTES, SEED)
    rng = random.Random(SEED + 1)               # different seed stream than the main eval
    idx = list(range(len(all_notes)))
    rng.shuffle(idx)
    split = int(0.7 * len(idx))
    train = [all_notes[i] for i in idx[:split]]
    test = [all_notes[i] for i in idx[split:]]
    print(f"train n={len(train)}  test n={len(test)}")

    nb = NaiveBayesStructurer().fit(train)
    nb_result = eval_extraction(nb, test)

    # Same held-out test split, zero-shot (no training needed) - fair secondary
    # comparison against lexicon on the identical 30% slice.
    lex_result = eval_extraction(LexiconStructurer(), test)

    print(f"\n{'model':<20} {'micro-F1':>9} {'macro-F1':>9} {'rash-rec':>9} {'n_test':>7}")
    for name, r in (("naive_bayes", nb_result), ("lexicon (same test split)", lex_result)):
        print(f"{name:<20} {r['micro_f1']:>9} {r['macro_f1']:>9} "
              f"{str(r['safety_recall']['side_effect:rash']):>9} {r['n_notes']:>7}")

    report = {
        "meta": {"seed": SEED, "n_train": len(train), "n_test": len(test),
                 "note": "NB trained on 70% split, held out 30% test. Lexicon "
                         "re-evaluated on the SAME test split for a fair "
                         "apples-to-apples comparison (its headline number "
                         "elsewhere uses all 1200 since it needs no training)."},
        "naive_bayes": nb_result,
        "lexicon_same_split": lex_result,
    }
    out = os.path.join(_HERE, "nlp_naive_bayes_report.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written: {os.path.relpath(out, _ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
