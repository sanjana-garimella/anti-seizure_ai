"""
validation/nlp_synthetic_eval.py
---------------------------------
Synthetic precision/recall/accuracy check for the optional NLP structurers
(point A): LexiconStructurer (deterministic baseline) vs BioBERTStructurer vs
ClinicalBERTStructurer.

WHY SYNTHETIC: there is no labeled gold-standard NER dataset for the free-text
fields in this project (gold_cases.json labels the STRUCTURED engine inputs,
not free text), and the original BioBERT/ClinicalBERT checkpoints used here
(alvaroalon2/biobert_diseases_ner; samrawal/bert-base-uncased_clinical-ner)
do not publish eval metrics on their model cards for these specific
fine-tunes. This is a small, hand-labeled, MADE-UP (non-PHI, no patient data)
sentence set built to get a real, reproducible number for THIS app's actual
use case. It is NOT a clinician-reviewed gold standard and NOT the same as
the published BC5CDR / i2b2 benchmark numbers for these architectures in
general — treat it as a diagnostic smoke test, not a validation study.

SCOPE: only entity types BioBERT/ClinicalBERT can structurally produce
(side_effect + its keyword refinements, seizure_mention, drug_mention,
test_mention). missed_dose_reason / dose_change are lexicon-only fields and
are deliberately excluded — including them would unfairly penalize the
transformer models for a category they were never wired to detect.

DECISION-PATH NOTE: this script and everything it prints is advisory /
diagnostic only. It does not touch engine_core, and never will.

Run:  cd aim2_engine && python3 validation/nlp_synthetic_eval.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.structurer import LexiconStructurer

# ---------------------------------------------------------------------------
# Synthetic gold set (made up, non-PHI, no patient data).
# Label convention: "side_effect:<value>" for side-effect findings (the field
# name alone is too coarse — both structurers keep field="side_effect" and put
# the specific symptom in suggested_value); bare field name otherwise
# ("seizure_mention" | "drug_mention" | "test_mention").
# ---------------------------------------------------------------------------
GOLD = [
    ("She has been really sleepy and tired since the last dose increase.",
     {"side_effect:sleepiness"}),
    ("He broke out in a rash on his arms after starting the new medicine.",
     {"side_effect:rash"}),
    ("She seems dizzy and unsteady when she stands up.",
     {"side_effect:dizziness"}),
    ("He has been irritable and cranky every afternoon.",
     {"side_effect:irritability"}),
    ("She has been nauseous and vomiting after taking her dose.",
     {"side_effect:nausea"}),
    ("He had a seizure with jerking and staring for about a minute.",
     {"seizure_mention"}),
    ("She had another seizure last night, whole body shaking for two minutes.",
     {"seizure_mention"}),
    ("We switched her to lamotrigine last week per the doctor.",
     {"drug_mention"}),
    ("He started taking levetiracetam twice a day.",
     {"drug_mention"}),
    ("The doctor ordered an EEG to check for ongoing seizure activity.",
     {"test_mention"}),
    ("She had blood work done to check her liver levels.",
     {"test_mention"}),
    ("Everything has been going well, no concerns at all.",
     set()),
    ("She's doing great this month, nothing new to report.",
     set()),
    ("He's been happy and active, eating and sleeping normally.",
     set()),
    ("She denies any rash, dizziness, or nausea this week.",
     set()),
    ("He has had a rash and seems really drowsy since starting the higher dose.",
     {"side_effect:rash", "side_effect:sleepiness"}),
    ("She's been nauseous, irritable, and a bit unsteady on her feet today.",
     {"side_effect:nausea", "side_effect:irritability", "side_effect:dizziness"}),
    ("His teacher said he seems more aggressive and hyper at school lately.",
     {"side_effect:behavior"}),
    ("She had a seizure and afterward seemed very sleepy and out of it.",
     {"seizure_mention", "side_effect:sleepiness"}),
    ("He's tolerating the medicine fine, just slightly more tired in the "
     "evenings, otherwise no other issues.",
     {"side_effect:sleepiness"}),
]


def label_of(finding) -> str:
    if finding.field == "side_effect":
        return f"side_effect:{finding.suggested_value}"
    return finding.field


# missed_dose_reason / dose_change are lexicon-only, out-of-scope fields (see
# module docstring) - filtered out of predictions so a sentence that
# incidentally triggers one of them (e.g. "switched to" firing dose_change
# alongside drug_mention) can't count as a false positive here. See the
# matching comment in nlp_synthetic_eval_large.py for the concrete case that
# surfaced this via validation/nlp_confusion_matrix.py.
_OUT_OF_SCOPE_FIELDS = {"missed_dose_reason", "dose_change"}

# Safety-relevant classes whose RECALL is the honest headline (finding D5): a
# missed rash / seizure mention is far costlier than a missed benign symptom.
_SAFETY_LABELS = ["side_effect:rash", "seizure_mention"]


def predict_labels(structurer, text) -> set:
    try:
        findings = structurer.structure(text, source_question="Q13")
        return {label_of(f) for f in findings if f.field not in _OUT_OF_SCOPE_FIELDS}
    except Exception as exc:                       # never let the eval crash
        print(f"    ! {structurer.__class__.__name__} raised on {text!r}: {exc}")
        return set()


def evaluate(name, structurer):
    tp = fp = fn = 0
    exact = 0
    per_label = {}   # label -> [tp, fp, fn]
    rows = []
    for text, gold in GOLD:
        pred = predict_labels(structurer, text)
        ex_tp, ex_fp, ex_fn = gold & pred, pred - gold, gold - pred
        tp += len(ex_tp); fp += len(ex_fp); fn += len(ex_fn)
        exact += int(pred == gold)
        rows.append((text, gold, pred))
        for l in ex_tp:
            per_label.setdefault(l, [0, 0, 0])[0] += 1
        for l in ex_fp:
            per_label.setdefault(l, [0, 0, 0])[1] += 1
        for l in ex_fn:
            per_label.setdefault(l, [0, 0, 0])[2] += 1

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision == precision and recall == recall and (precision + recall)
          else float("nan"))
    exact_match_acc = exact / len(GOLD)

    # Per-label P/R/F1, plus macro-F1 (finding D5). For imbalanced safety
    # labels, macro-F1 and the per-label recall of rare high-stakes classes
    # (rash, seizure) are the honest headline; micro-F1 and exact-match let the
    # frequent classes mask poor recall on the rare ones.
    per_label_stats = {}
    for label, (ltp, lfp, lfn) in per_label.items():
        lp = ltp / (ltp + lfp) if (ltp + lfp) else float("nan")
        lr = ltp / (ltp + lfn) if (ltp + lfn) else float("nan")
        lf1 = (2 * lp * lr / (lp + lr)
               if lp == lp and lr == lr and (lp + lr) else float("nan"))
        per_label_stats[label] = {"precision": lp, "recall": lr, "f1": lf1,
                                  "tp": ltp, "fp": lfp, "fn": lfn}
    label_f1s = [s["f1"] for s in per_label_stats.values() if s["f1"] == s["f1"]]
    macro_f1 = sum(label_f1s) / len(label_f1s) if label_f1s else float("nan")

    print(f"\n  === {name} ===")
    print("    HEADLINE (safety-relevant recall — the metric that matters most):")
    for label in _SAFETY_LABELS:
        s = per_label_stats.get(label)
        if s:
            print(f"      recall[{label:<20}] {s['recall']:.3f}  "
                  f"(tp={s['tp']} fn={s['fn']}, precision {s['precision']:.3f})")
        else:
            print(f"      recall[{label:<20}] (no gold instances in this set)")
    print(f"    macro F1 (unweighted over labels): {macro_f1:.3f}")
    print(f"    micro precision      : {precision:.3f}  ({tp} tp / {tp + fp} predicted)")
    print(f"    micro recall         : {recall:.3f}  ({tp} tp / {tp + fn} gold)")
    print(f"    micro F1             : {f1:.3f}")
    print(f"    [diagnostic only] exact-match accuracy : {exact_match_acc:.3f}  "
          f"({exact}/{len(GOLD)} sentences got the EXACT right label set - "
          f"strict, all-or-nothing; NOT a headline metric for imbalanced safety labels)")
    print("    per-label breakdown  :")
    for label in sorted(per_label_stats):
        s = per_label_stats[label]
        print(f"      {label:<24} precision {s['precision']:.2f}  recall {s['recall']:.2f}  "
              f"f1 {s['f1']:.2f}  (tp={s['tp']} fp={s['fp']} fn={s['fn']})")
    mismatches = [(t, g, p) for t, g, p in rows if g != p]
    if mismatches:
        print("    misses / false positives:")
        for text, gold, pred in mismatches:
            print(f"      text: {text!r}")
            print(f"        gold: {sorted(gold) or '(none)'}   pred: {sorted(pred) or '(none)'}")
    return {"precision": precision, "recall": recall, "f1": f1,
            "macro_f1": macro_f1, "exact_match_accuracy": exact_match_acc,
            "per_label": per_label_stats}


def main():
    print("\n  NLP synthetic eval - Aim 2 point-A structurers (advisory layer only)")
    print("  " + "=" * 70)
    print(f"  {len(GOLD)} hand-labeled synthetic sentences, scoped to entity types")
    print("  BioBERT/ClinicalBERT can structurally produce. Not a clinical gold")
    print("  standard - see module docstring.\n")

    results = {}
    results["lexicon"] = evaluate("LexiconStructurer (deterministic baseline)", LexiconStructurer())

    try:
        from nlp.transformer_structurer import BioBERTStructurer, ClinicalBERTStructurer
    except ImportError:
        BioBERTStructurer = ClinicalBERTStructurer = None
        print("\n  transformers/torch not installed - skipping BioBERT/ClinicalBERT.")
        print("  Run: pip3 install --user transformers torch")

    if BioBERTStructurer is not None:
        b = BioBERTStructurer(lazy=False)
        if b.is_available():
            results["biobert"] = evaluate("BioBERTStructurer (alvaroalon2/biobert_diseases_ner)", b)
        else:
            print("\n  BioBERT unavailable (see warning above) - skipping.")

        c = ClinicalBERTStructurer(lazy=False)
        if c.is_available():
            results["clinical_bert"] = evaluate(
                "ClinicalBERTStructurer (samrawal/bert-base-uncased_clinical-ner)", c)
        else:
            print("\n  ClinicalBERT unavailable (see warning above) - skipping.")

        # Two more well-established pretrained candidates, tried via the generic
        # fuzzy-label adapter (GenericNERAdapter) rather than a hand-written
        # ModelProfile - see nlp/transformer_structurer.py docstring on why.
        from nlp.transformer_structurer import GenericNERAdapter
        for key, model_name in [
            ("d4data_biomedical_ner_all", "d4data/biomedical-ner-all"),
            ("humadex_english_medical_ner", "HUMADEX/english_medical_ner"),
        ]:
            g = GenericNERAdapter(model_name, lazy=False)
            if g.is_available():
                results[key] = evaluate(f"GenericNERAdapter ({model_name})", g)
            else:
                print(f"\n  {model_name} unavailable (see warning above) - skipping.")

    print("\n  " + "=" * 70)
    print("  SUMMARY (led by safety recall + macro-F1; exact-match is diagnostic only)")
    for name, r in results.items():
        pl = r.get("per_label", {})
        safety = "  ".join(
            f"{lbl.split(':')[-1]}-recall "
            + (f"{pl[lbl]['recall']:.3f}" if lbl in pl else "n/a")
            for lbl in _SAFETY_LABELS)
        print(f"    {name:<14} {safety}  macro-F1 {r.get('macro_f1', float('nan')):.3f}  "
              f"micro-F1 {r['f1']:.3f}  [diag] exact-match {r['exact_match_accuracy']:.3f}")
    print()


if __name__ == "__main__":
    main()
