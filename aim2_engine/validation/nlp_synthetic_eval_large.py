"""
validation/nlp_synthetic_eval_large.py
----------------------------------------
Large-N templated synthetic stress test for the point-A structurers
(LexiconStructurer, BioBERTStructurer, ClinicalBERTStructurer). Complements
the small hand-written set in nlp_synthetic_eval.py.

WHY TEMPLATED, NOT HAND-LABELED: nobody hand-labels 10,000 sentences. Every
case here is built from a small bank of symptom/seizure/drug/test phrases and
sentence templates, so the gold label is known BY CONSTRUCTION - there is no
manual labeling step and therefore no manual labeling error. The tradeoff:
this measures performance ON THE TEMPLATE DISTRIBUTION, which is narrower and
more regular than real caregiver free text. Treat results as "does this
structurer get the easy, unambiguous cases right, at scale" and "what
fraction of a large synthetic batch trips it up" - not as a substitute for a
clinician-reviewed gold set (none exists for this layer yet - see
nlp_synthetic_eval.py's docstring for why).

PERFORMANCE NOTE: the lexicon baseline is regex - 10,000 cases run in well
under a second. BioBERT/ClinicalBERT run a real forward pass per clause per
case, so 10,000 cases can take many minutes on a laptop CPU. Default sample
sizes below reflect that; override with --n / --transformer-sample.

DECISION-PATH NOTE: advisory/diagnostic only. Does not touch engine_core.

Run:
  cd aim2_engine
  python3 validation/nlp_synthetic_eval_large.py                      # n=10000 lexicon,
                                                                        #   500-case sample for transformers
  python3 validation/nlp_synthetic_eval_large.py --n 2000 --transformer-sample 2000
  python3 validation/nlp_synthetic_eval_large.py --no-transformers     # lexicon only, fast
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.structurer import LexiconStructurer

# ---------------------------------------------------------------------------
# Phrase banks (made up, non-PHI, no patient data)
# ---------------------------------------------------------------------------
SUBJECTS = ["She", "He"]
POSSESSIVE = {"She": "her", "He": "his"}
BODY_PARTS = ["her arms", "his arms", "her legs", "his chest", "her back", "his face"]

SYMPTOM_PHRASES = {
    "sleepiness":    ["really sleepy", "very tired", "drowsy", "fatigued all day", "lethargic lately"],
    "rash":          ["a rash on {part}", "hives on {part}", "a skin rash"],
    "dizziness":     ["dizzy", "unsteady on {pos} feet", "having balance problems"],
    "irritability":  ["irritable", "cranky", "moody", "easily angered"],
    "nausea":        ["nauseous", "vomiting", "throwing up", "off {pos} food"],
    "behavior":      ["aggressive", "hyper", "acting out at school"],
}
SYMPTOM_SHORT = {
    "sleepiness": ["sleepy", "tired", "drowsiness"],
    "rash": ["rash", "hives"],
    "dizziness": ["dizziness", "dizzy spells"],
    "irritability": ["irritability", "crankiness"],
    "nausea": ["nausea", "vomiting"],
    "behavior": ["aggression", "hyperactivity"],
}
SEIZURE_PHRASES = ["a seizure", "a staring spell", "jerking and twitching",
                    "a convulsion", "whole-body shaking"]
DRUGS = ["levetiracetam", "lamotrigine", "oxcarbazepine", "valproate", "clobazam",
         "topiramate", "zonisamide", "brivaracetam", "ethosuximide", "lacosamide",
         "carbamazepine"]
TEST_PHRASES = ["an EEG", "blood work", "an MRI", "liver function tests", "a blood draw"]
NEGATIVE_PLAIN = [
    "Everything has been going well, no concerns at all.",
    "She's doing great this month, nothing new to report.",
    "He's been happy and active, eating and sleeping normally.",
    "No new issues since the last visit.",
    "Things are stable, nothing to flag.",
    "We had a quiet week, nothing to report.",
    # NOTE: "We ran out of the medicine and need a refill." used to be here,
    # labeled gold=set() (no findings). That was a labeling gap, not a
    # lexicon bug - the lexicon's missed_dose_reason regex ("ran out|run
    # out|refill") correctly fires on it, but missed_dose_reason/dose_change
    # are lexicon-only fields deliberately excluded from this eval's SCOPE
    # (see module docstring + nlp_synthetic_eval.py's), so this phrase had no
    # way to score as a true positive here - it could only ever look like a
    # false positive. Found via validation/nlp_confusion_matrix.py, which
    # showed a stable ~1.6% "(nothing) -> missed_dose_reason/dose_change"
    # confusion at both n=3000 and n=5000. Swapped for an unambiguous
    # negative phrase instead of adding an out-of-scope gold label.
]


def _fill(phrase: str, subj: str) -> str:
    return phrase.format(part=random.choice(BODY_PARTS), pos=POSSESSIVE[subj])


def _label_for(field: str) -> str:
    return f"side_effect:{field}" if field in SYMPTOM_PHRASES else field


# ---------------------------------------------------------------------------
# Case generator - returns (text, gold_label_set)
# ---------------------------------------------------------------------------
def gen_case(rng: random.Random):
    subj = rng.choice(SUBJECTS)
    r = rng.random()

    if r < 0.30:                                   # single side effect
        field = rng.choice(list(SYMPTOM_PHRASES))
        phrase = _fill(rng.choice(SYMPTOM_PHRASES[field]), subj)
        return f"{subj} has been {phrase}.", {_label_for(field)}

    if r < 0.45:                                   # multi side effect (2-3)
        fields = rng.sample(list(SYMPTOM_PHRASES), k=rng.choice([2, 3]))
        phrases = [_fill(rng.choice(SYMPTOM_PHRASES[f]), subj) for f in fields]
        text = f"{subj} has been " + ", ".join(phrases[:-1]) + f", and {phrases[-1]}."
        return text, {_label_for(f) for f in fields}

    if r < 0.55:                                   # seizure
        phrase = rng.choice(SEIZURE_PHRASES)
        return f"{subj} had {phrase} last night.", {"seizure_mention"}

    if r < 0.65:                                   # drug mention
        drug = rng.choice(DRUGS)
        verb = rng.choice(["started taking", "was switched to", "is now on"])
        return f"{subj} {verb} {drug}.", {"drug_mention"}

    if r < 0.73:                                   # test mention
        test = rng.choice(TEST_PHRASES)
        return f"The doctor ordered {test} to check on things.", {"test_mention"}

    if r < 0.83:                                   # plain negative
        return rng.choice(NEGATIVE_PLAIN), set()

    if r < 0.93:                                   # fully negated
        fields = rng.sample(list(SYMPTOM_SHORT), k=rng.choice([2, 3]))
        terms = [rng.choice(SYMPTOM_SHORT[f]) for f in fields]
        tmpl = rng.choice([
            "{s} denies any {a} or {b}.",
            "{s} has not had any {a}, {b}, or {c}.",
            "No {a} or {b} reported.",
        ])
        terms_padded = terms + terms[:1] * 3
        text = tmpl.format(s=subj, a=terms_padded[0], b=terms_padded[1], c=terms_padded[2])
        return text, set()

    # mixed: one negated symptom + one affirmed symptom (stresses the
    # sentence-scope limitation called out in nlp/negation.py's docstring)
    fields = rng.sample(list(SYMPTOM_PHRASES), k=2)
    neg_field, pos_field = fields[0], fields[1]
    neg_term = rng.choice(SYMPTOM_SHORT[neg_field])
    pos_phrase = _fill(rng.choice(SYMPTOM_PHRASES[pos_field]), subj)
    text = f"{subj} denies any {neg_term}, but has been {pos_phrase}."
    return text, {_label_for(pos_field)}


def gen_dataset(n: int, seed: int = 0):
    rng = random.Random(seed)
    return [gen_case(rng) for _ in range(n)]


# ---------------------------------------------------------------------------
# Scoring (same convention as nlp_synthetic_eval.py)
# ---------------------------------------------------------------------------
def label_of(finding) -> str:
    if finding.field == "side_effect":
        return f"side_effect:{finding.suggested_value}"
    return finding.field


## missed_dose_reason / dose_change are lexicon-only fields, deliberately out
# of scope for this eval (see module docstring - BioBERT/ClinicalBERT can't
# structurally produce them, so scoring them would unfairly penalize those
# models). LexiconStructurer scans every field independently, so a sentence
# like "She was switched to levetiracetam." legitimately fires BOTH
# drug_mention AND dose_change:changed (from "switch") - but gold here only
# ever labels the drug_mention half. Filtering these two fields out of
# PREDICTIONS (not just leaving them out of gold) keeps that from silently
# counting as a lexicon false positive. Found via
# validation/nlp_confusion_matrix.py - this was deflating LexiconStructurer's
# reported precision here too, not just in the confusion matrix.
_OUT_OF_SCOPE_FIELDS = {"missed_dose_reason", "dose_change"}

# Safety-relevant classes whose RECALL is the honest headline (finding D5).
_SAFETY_LABELS = ["side_effect:rash", "seizure_mention"]


def predict_labels(structurer, text) -> set:
    try:
        findings = structurer.structure(text, source_question="Q13")
        return {label_of(f) for f in findings if f.field not in _OUT_OF_SCOPE_FIELDS}
    except Exception as exc:
        print(f"    ! {structurer.__class__.__name__} raised on {text!r}: {exc}")
        return set()


def evaluate(name, structurer, dataset, max_examples_shown=15):
    tp = fp = fn = 0
    exact = 0
    per_label = {}
    fp_examples, fn_examples = [], []
    fp_label_counts, fn_label_counts = Counter(), Counter()

    t0 = time.perf_counter()
    for text, gold in dataset:
        pred = predict_labels(structurer, text)
        ex_tp, ex_fp, ex_fn = gold & pred, pred - gold, gold - pred
        tp += len(ex_tp); fp += len(ex_fp); fn += len(ex_fn)
        exact += int(pred == gold)
        for l in ex_tp:
            per_label.setdefault(l, [0, 0, 0])[0] += 1
        for l in ex_fp:
            per_label.setdefault(l, [0, 0, 0])[1] += 1
            fp_label_counts[l] += 1
            if len(fp_examples) < max_examples_shown:
                fp_examples.append((text, sorted(gold), sorted(pred)))
        for l in ex_fn:
            per_label.setdefault(l, [0, 0, 0])[2] += 1
            fn_label_counts[l] += 1
            if len(fn_examples) < max_examples_shown:
                fn_examples.append((text, sorted(gold), sorted(pred)))
    elapsed = time.perf_counter() - t0

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision == precision and recall == recall and (precision + recall)
          else float("nan"))
    exact_match_acc = exact / len(dataset)

    # Per-label P/R/F1 + macro-F1 (finding D5).
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

    print(f"\n  === {name}  (n={len(dataset)}, {elapsed:.1f}s, "
          f"{elapsed / len(dataset) * 1000:.1f} ms/case) ===")
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
          f"({exact}/{len(dataset)}; strict all-or-nothing, NOT a headline for "
          f"imbalanced safety labels)")
    print("    per-label breakdown  :")
    for label in sorted(per_label_stats):
        s = per_label_stats[label]
        print(f"      {label:<24} precision {s['precision']:.3f}  recall {s['recall']:.3f}  "
              f"f1 {s['f1']:.3f}  (tp={s['tp']} fp={s['fp']} fn={s['fn']})")

    if fp_label_counts:
        print(f"    top false-positive labels: {fp_label_counts.most_common(5)}")
    if fn_label_counts:
        print(f"    top false-negative labels: {fn_label_counts.most_common(5)}")
    if fp_examples:
        print(f"    sample false positives (up to {max_examples_shown}):")
        for text, gold, pred in fp_examples:
            print(f"      {text!r}\n        gold={gold} pred={pred}")
    if fn_examples:
        print(f"    sample false negatives (up to {max_examples_shown}):")
        for text, gold, pred in fn_examples:
            print(f"      {text!r}\n        gold={gold} pred={pred}")

    return {"precision": precision, "recall": recall, "f1": f1,
            "macro_f1": macro_f1, "exact_match_accuracy": exact_match_acc,
            "per_label": per_label_stats, "n": len(dataset), "seconds": elapsed}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=10000, help="cases for the lexicon baseline")
    ap.add_argument("--transformer-sample", type=int, default=500,
                     help="subsample size for BioBERT/ClinicalBERT (cost control)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-transformers", action="store_true")
    args = ap.parse_args()

    print("\n  NLP large-N synthetic eval - Aim 2 point-A structurers (advisory layer only)")
    print("  " + "=" * 78)

    full = gen_dataset(args.n, seed=args.seed)
    print(f"  generated {len(full)} templated cases (seed={args.seed})")
    gold_label_counts = Counter(l for _, g in full for l in g)
    none_count = sum(1 for _, g in full if not g)
    print(f"  gold label distribution: {dict(gold_label_counts)}  (+ {none_count} no-finding cases)\n")

    results = {}
    results["lexicon"] = evaluate("LexiconStructurer (deterministic baseline)",
                                   LexiconStructurer(), full)

    if not args.no_transformers:
        try:
            from nlp.transformer_structurer import BioBERTStructurer, ClinicalBERTStructurer
        except ImportError:
            BioBERTStructurer = ClinicalBERTStructurer = None
            print("\n  transformers/torch not installed - skipping BioBERT/ClinicalBERT.")
            print("  Run: pip3 install --user transformers torch")

        if BioBERTStructurer is not None:
            sample = gen_dataset(args.transformer_sample, seed=args.seed + 1)
            b = BioBERTStructurer(lazy=False)
            if b.is_available():
                results["biobert"] = evaluate(
                    "BioBERTStructurer (alvaroalon2/biobert_diseases_ner)", b, sample)
            else:
                print("\n  BioBERT unavailable - skipping.")

            c = ClinicalBERTStructurer(lazy=False)
            if c.is_available():
                results["clinical_bert"] = evaluate(
                    "ClinicalBERTStructurer (samrawal/bert-base-uncased_clinical-ner)", c, sample)
            else:
                print("\n  ClinicalBERT unavailable - skipping.")

            # Two more well-established candidates via the generic fuzzy-label
            # adapter (see nlp/transformer_structurer.py for why no hand-written
            # ModelProfile).
            from nlp.transformer_structurer import GenericNERAdapter
            for key, model_name in [
                ("d4data_biomedical_ner_all", "d4data/biomedical-ner-all"),
                ("humadex_english_medical_ner", "HUMADEX/english_medical_ner"),
            ]:
                g = GenericNERAdapter(model_name, lazy=False)
                if g.is_available():
                    results[key] = evaluate(f"GenericNERAdapter ({model_name})", g, sample)
                else:
                    print(f"\n  {model_name} unavailable - skipping.")

    print("\n  " + "=" * 78)
    print("  SUMMARY (led by safety recall + macro-F1; exact-match is diagnostic only)")
    for name, r in results.items():
        pl = r.get("per_label", {})
        safety = "  ".join(
            f"{lbl.split(':')[-1]}-recall "
            + (f"{pl[lbl]['recall']:.3f}" if lbl in pl else "n/a")
            for lbl in _SAFETY_LABELS)
        print(f"    {name:<14} n={r['n']:<6} {safety}  macro-F1 {r.get('macro_f1', float('nan')):.3f}  "
              f"micro-F1 {r['f1']:.3f}  [diag] exact-match {r['exact_match_accuracy']:.3f}  "
              f"({r['seconds']:.1f}s)")
    print()


if __name__ == "__main__":
    main()
