"""
validation/nlp_pubmed_eval.py
--------------------------------
Runs the point-A structurers against a local PubMed cache built by
fetch_asm_pubmed_cases.py. That cache is gitignored. Read that script's
docstring first - in
short: this is real, externally-authored, on-topic (your 11 ASMs) text, but
it has NO gold annotation, so this script can only report RECALL-style
proxies, not precision/F1:

  - drug-mention recall proxy: for each case we KNOW at least one of the 11
    ASM names appears literally in the text (that's how the case was
    selected). Did the structurer's drug_mention findings include it?
  - side-effect/seizure finding RATE (not recall): the search terms biased
    toward adverse-effect/seizure-control language, so most cases probably
    describe one, but we have no per-case label confirming that, so this is
    "how often did the structurer find something," not a true recall number.

DECISION-PATH NOTE: advisory/diagnostic only. Does not touch engine_core.

Run:
  cd aim2_engine
  python3 validation/fetch_asm_pubmed_cases.py --n 2000      # do this first
  python3 validation/nlp_pubmed_eval.py
  python3 validation/nlp_pubmed_eval.py --n 500 --no-transformers
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation.nlp_eval_stats import (
    bootstrap_ci, mcnemars_test, fmt_ci, write_artifact)

_DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "asm_pubmed_cases.json")


def _coarse_labels(finding) -> str:
    if finding.field == "side_effect":
        return "SIDE_EFFECT"
    return {"seizure_mention": "SEIZURE", "drug_mention": "DRUG",
            "test_mention": "TEST"}.get(finding.field)


def load_cases(path, n, seed):
    if not os.path.isfile(path):
        print(f"  No cache at {path}.")
        print("  Run: python3 validation/fetch_asm_pubmed_cases.py --n 2000   first.")
        sys.exit(1)
    data = json.load(open(path))
    cases = [c for c in data["cases"] if c.get("matched_asms")]
    random.Random(seed).shuffle(cases)
    print(f"  Loaded {len(data['cases'])} cached cases ({len(cases)} with a confirmed "
          f"ASM mention); using {min(n, len(cases))}.")
    return cases[:n]


def evaluate(name, structurer, cases, max_examples_shown=8):
    drug_tp = drug_total = 0
    side_effect_hits = seizure_hits = 0
    drug_misses = []
    per_case_drug_hit = []   # 1.0 if a known ASM was recovered on that case
    print(f"\n  --- {name}  (n={len(cases)}) running... ---", flush=True)
    for i, c in enumerate(cases):
        if i and i % 100 == 0:
            print(f"      ... {i}/{len(cases)} cases done", flush=True)
        text = c["text"]
        try:
            findings = structurer.structure(text, source_question="Q13")
        except Exception as exc:
            print(f"    ! {name} raised on pmid {c.get('pmid')}: {exc}")
            findings = []
        pred_types = {_coarse_labels(f) for f in findings}
        drug_values = {f.suggested_value.lower() for f in findings if f.field == "drug_mention"}

        drug_total += 1
        # Was a drug_mention finding's value a match (either direction, since
        # NER spans sometimes include/exclude surrounding words) for one of
        # the ASM names we already know appear literally in this text?
        found_known_drug = bool(drug_values) and any(
            any(asm.lower() in dv or dv in asm.lower() for dv in drug_values)
            for asm in c["matched_asms"]
        )
        per_case_drug_hit.append(1.0 if found_known_drug else 0.0)
        if found_known_drug:
            drug_tp += 1
        elif len(drug_misses) < max_examples_shown:
            drug_misses.append((c["pmid"], c["matched_asms"], sorted(drug_values)))

        if "SIDE_EFFECT" in pred_types:
            side_effect_hits += 1
        if "SEIZURE" in pred_types:
            seizure_hits += 1

    n = len(cases)
    # Bootstrap 95% CI on the drug-mention recall proxy (finding D2) so a
    # model-vs-model gap is read against sampling noise, not as a bare point.
    rate, lo, hi = bootstrap_ci(per_case_drug_hit)
    print(f"\n  === {name}  (n={n}) ===")
    print(f"    NOTE: no gold spans here — these are RECALL PROXIES / finding RATES, "
          f"NOT precision or F1.")
    print(f"    drug-mention recall proxy : {drug_tp}/{drug_total} = {fmt_ci(rate, lo, hi)}"
          if drug_total else "    (no cases)")
    print(f"    side_effect finding rate  : {side_effect_hits}/{n} = {side_effect_hits / n:.3f}")
    print(f"    seizure finding rate      : {seizure_hits}/{n} = {seizure_hits / n:.3f}")
    if drug_misses:
        print(f"    sample drug-mention misses (up to {max_examples_shown}):")
        for pmid, known, found in drug_misses:
            print(f"      pmid {pmid}: known ASM(s) {known}  structurer drug_mention values {found}")
    return {"drug_recall_proxy": drug_tp / drug_total if drug_total else float("nan"),
            "drug_recall_proxy_ci": [lo, hi],
            "side_effect_rate": side_effect_hits / n, "seizure_rate": seizure_hits / n,
            "n": n, "per_case_drug_hit": per_case_drug_hit}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default=_DEFAULT_CACHE)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-transformers", action="store_true")
    args = ap.parse_args()

    print("\n  NLP eval - REAL ASM-specific PubMed corpus (no gold spans - recall proxies only)")
    print("  " + "=" * 78)

    cases = load_cases(args.cache, args.n, args.seed)

    from nlp.structurer import LexiconStructurer
    results = {"lexicon": evaluate("LexiconStructurer", LexiconStructurer(), cases)}

    if not args.no_transformers:
        try:
            from nlp.transformer_structurer import (
                BioBERTStructurer, ClinicalBERTStructurer, GenericNERAdapter)
        except ImportError:
            print("\n  transformers/torch not installed - skipping zero-shot models.")
            BioBERTStructurer = None

        if BioBERTStructurer is not None:
            for key, candidate in [
                ("biobert", BioBERTStructurer(lazy=False)),
                ("clinical_bert", ClinicalBERTStructurer(lazy=False)),
                ("d4data", GenericNERAdapter("d4data/biomedical-ner-all", lazy=False)),
                ("humadex", GenericNERAdapter("HUMADEX/english_medical_ner", lazy=False)),
            ]:
                if candidate.is_available():
                    results[key] = evaluate(getattr(candidate, "model_name", key), candidate, cases)
                else:
                    print(f"\n  {key} unavailable - skipping.")

    # Pairwise McNemar on paired per-case drug-hit correctness (finding D2).
    keyed = {k: v for k, v in results.items() if v.get("per_case_drug_hit")}
    sig = []
    keys = list(keyed)
    if len(keys) >= 2:
        print("\n  pairwise significance (McNemar on paired per-case drug-hit correctness):")
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                ca, cb = keyed[a]["per_case_drug_hit"], keyed[b]["per_case_drug_hit"]
                if len(ca) != len(cb):
                    continue
                mt = mcnemars_test([bool(x) for x in ca], [bool(x) for x in cb])
                verdict = ("SIGNIFICANT" if mt["p_value"] == mt["p_value"]
                           and mt["p_value"] < 0.05 else "not significant (p>=0.05)")
                print(f"    {a} vs {b}: n={len(ca)}  b={mt['b']} c={mt['c']}  "
                      f"p={mt['p_value']:.4f} ({mt['method']}) -> {verdict}")
                sig.append({"a": a, "b": b, "n": len(ca), **mt, "verdict": verdict})

    print("\n  " + "=" * 78)
    print("  SUMMARY (recall proxies only — no precision/F1 obtainable without gold spans)")
    for name, r in results.items():
        ci = r.get("drug_recall_proxy_ci", [float('nan'), float('nan')])
        print(f"    {name:<14} drug-recall-proxy {r['drug_recall_proxy']:.3f} "
              f"(95% CI {ci[0]:.3f}-{ci[1]:.3f})  "
              f"side_effect-rate {r['side_effect_rate']:.3f}  seizure-rate {r['seizure_rate']:.3f}")
    print("\n  HONEST FRAMING (finding D2): this corpus has NO gold annotation. A higher")
    print("  finding RATE can mean better recall OR more over-firing — it cannot tell them")
    print("  apart. A model-vs-model gap that is not McNemar-significant (or whose CIs")
    print("  overlap) is NOT grounds to change the production default. The deterministic")
    print("  LexiconStructurer stays the default until a labeled, significance-tested eval exists.")

    payload = {
        "eval": "nlp_pubmed_eval (real ASM PubMed corpus, no gold spans)",
        "framing": "recall proxies / finding rates only; gaps significance-tested; no precision",
        "args": {"n": args.n, "seed": args.seed, "no_transformers": args.no_transformers},
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "per_case_drug_hit"}
                    for k, v in results.items()},
        "significance": sig,
    }
    path = write_artifact("nlp_pubmed_eval_result", payload)
    print(f"\n  Wrote reproducible result artifact: {path}")
    print()


if __name__ == "__main__":
    main()
