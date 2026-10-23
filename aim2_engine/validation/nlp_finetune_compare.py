"""
validation/nlp_finetune_compare.py
------------------------------------
Fine-tunes 2+ small local models on a span-labeled SYNTHETIC dataset for the
point-A entity types (side_effect / seizure_mention / drug_mention /
test_mention), then compares them against the zero-shot candidates already
wired up in nlp/transformer_structurer.py (BioBERT, ClinicalBERT, and the two
GenericNERAdapter models). This is the "let's try training instead of just
prompting a pretrained model" experiment requested after the literature
search showed fine-tuning beats zero-shot for this kind of task (see chat
history / NEEDS_PI_SIGNOFF.md-adjacent discussion).

HONEST FRAMING - read before trusting any number this prints:
  This is a PIPELINE PROOF OF CONCEPT, not a validation study. The training
  AND validation data both come from the same small templated phrase banks
  below. A model trained on this can learn to recognize THESE TEMPLATES
  extremely well without learning anything that generalizes to how real
  caregivers actually write. High accuracy here tells you the fine-tuning
  mechanics work end-to-end (data labeling, tokenization/label alignment,
  training loop, span decoding, evaluation) - it does NOT tell you the
  resulting model would work on real free text. The literature comparison
  upstream of this script used REAL annotated clinical text; this script
  uses none, because none exists for this project yet (see
  nlp_synthetic_eval.py's docstring for why).

  The right next step once this pipeline is proven out: replace
  generate_dataset() below with real labeled examples (ideally caregiver
  phrasing Dr. Barbour / clinic staff can supply or confirm - ties into the
  validation gap already flagged in NEEDS_PI_SIGNOFF.md) and re-run.

DECISION-PATH NOTE: advisory/diagnostic only, same as every other script in
this directory. Nothing here touches engine_core, and the resulting
fine-tuned model, if ever used in clinic.py, would plug into the SAME
FreeTextStructurer protocol boundary as BioBERT/ClinicalBERT today - point A,
never the decision path.

REQUIRES: transformers, torch, and (for training) a CPU/GPU with a few
minutes to spare. Will NOT run in an environment without transformers/torch
installed (see chat history - PyPI was blocked in the sandbox this was
authored in; this needs to run on a machine with both installed, same as
nlp_synthetic_eval*.py's transformer portions).

Run:
  cd aim2_engine
  python3 validation/nlp_finetune_compare.py                       # defaults: 1500 train / 300 val, 3 epochs, 2 models
  python3 validation/nlp_finetune_compare.py --models distilbert-base-uncased emilyalsentzer/Bio_ClinicalBERT
  python3 validation/nlp_finetune_compare.py --n-train 3000 --n-val 500 --epochs 4
  python3 validation/nlp_finetune_compare.py --skip-zero-shot        # just the fine-tuning part, faster
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Span-labeled synthetic generator (made up, non-PHI, no patient data)
# ---------------------------------------------------------------------------
PHRASE_BANKS = {
    "side_effect": [
        "really sleepy", "very tired", "drowsy", "fatigued", "lethargic",
        "a rash on her arm", "hives on his arm", "a skin rash",
        "dizzy", "unsteady on her feet", "having balance problems",
        "irritable", "cranky", "moody", "easily angered",
        "nauseous", "vomiting", "throwing up", "off her food",
        "aggressive", "hyper", "acting out at school",
    ],
    "seizure": [
        "a seizure", "a staring spell", "jerking and twitching",
        "a convulsion", "whole-body shaking",
    ],
    "drug": [
        "levetiracetam", "lamotrigine", "oxcarbazepine", "valproate",
        "clobazam", "topiramate", "zonisamide", "brivaracetam",
        "ethosuximide", "lacosamide", "carbamazepine",
    ],
    "test": [
        "an EEG", "blood work", "an MRI", "liver function tests", "a blood draw",
    ],
}
TYPE2TAG = {"side_effect": "SIDE_EFFECT", "seizure": "SEIZURE",
            "drug": "DRUG", "test": "TEST"}

TEMPLATES = [
    "{subj} has been {E:side_effect}.",
    "{subj} has been {E:side_effect} and {E:side_effect}.",
    "{subj} had {E:seizure} last night.",
    "{subj} started taking {E:drug}.",
    "The doctor ordered {E:test} to check on things.",
    "{subj} has been {E:side_effect}, and the doctor ordered {E:test}.",
    "{subj} was switched to {E:drug} after having {E:seizure}.",
    "{subj} had {E:seizure} and afterward seemed {E:side_effect}.",
    "{subj} is now on {E:drug} and has been {E:side_effect}.",
    "Everything has been going well, no concerns at all.",
    "She's doing great this month, nothing new to report.",
    "He's been happy and active, eating and sleeping normally.",
    "Things are stable, nothing to flag.",
]

_PLACEHOLDER_RE = re.compile(r"\{E:(\w+)\}")


def gen_labeled_case(rng: random.Random):
    """Returns (text, [(start, end, entity_type), ...])."""
    tmpl = rng.choice(TEMPLATES)
    subj = rng.choice(["She", "He"])
    result = ""
    spans = []
    idx = 0
    while True:
        m = _PLACEHOLDER_RE.search(tmpl, idx)
        if not m:
            result += tmpl[idx:].replace("{subj}", subj)
            break
        result += tmpl[idx:m.start()].replace("{subj}", subj)
        etype = m.group(1)
        phrase = rng.choice(PHRASE_BANKS[etype])
        start = len(result)
        result += phrase
        spans.append((start, len(result), etype))
        idx = m.end()
    return result, spans


def generate_dataset(n: int, seed: int):
    rng = random.Random(seed)
    return [gen_labeled_case(rng) for _ in range(n)]


# ---------------------------------------------------------------------------
# BIO label scheme
# ---------------------------------------------------------------------------
LABELS = ["O"]
for _t in ("SIDE_EFFECT", "SEIZURE", "DRUG", "TEST"):
    LABELS += [f"B-{_t}", f"I-{_t}"]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for l, i in LABEL2ID.items()}


def encode_example(tokenizer, text, spans, max_length=64):
    enc = tokenizer(text, truncation=True, max_length=max_length, return_offsets_mapping=True)
    offsets = enc["offset_mapping"]
    labels = ["O"] * len(offsets)
    for start, end, etype in spans:
        tag = TYPE2TAG[etype]
        first = True
        for i, (tok_start, tok_end) in enumerate(offsets):
            if tok_start == tok_end:           # special token ([CLS]/[SEP]/pad)
                continue
            if tok_start >= start and tok_end <= end:
                labels[i] = f"B-{tag}" if first else f"I-{tag}"
                first = False
    enc["labels"] = [LABEL2ID[l] for l in labels]
    return enc


def decode_spans(label_ids, offsets):
    """BIO label ids + token offsets -> [(start, end, type), ...]."""
    spans = []
    cur_type, cur_start, cur_end = None, None, None
    for lab_id, (tok_start, tok_end) in zip(label_ids, offsets):
        if tok_start == tok_end:
            continue
        lab = ID2LABEL.get(lab_id, "O")
        if lab.startswith("B-"):
            if cur_type:
                spans.append((cur_start, cur_end, cur_type))
            cur_type, cur_start, cur_end = lab[2:], tok_start, tok_end
        elif lab.startswith("I-") and cur_type == lab[2:]:
            cur_end = tok_end
        else:
            if cur_type:
                spans.append((cur_start, cur_end, cur_type))
            cur_type = None
    if cur_type:
        spans.append((cur_start, cur_end, cur_type))
    return spans


# ---------------------------------------------------------------------------
# Train + evaluate one model
# ---------------------------------------------------------------------------
def train_and_eval(model_name: str, train_data, val_data, epochs: int, batch_size: int = 16):
    import torch
    from transformers import (AutoTokenizer, AutoModelForTokenClassification,
                               TrainingArguments, Trainer, DataCollatorForTokenClassification)

    print(f"\n  --- fine-tuning {model_name} ---")
    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForTokenClassification.from_pretrained(
        model_name, num_labels=len(LABELS), id2label=ID2LABEL, label2id=LABEL2ID)

    def to_features(data):
        feats = [encode_example(tokenizer, text, spans) for text, spans in data]
        for f in feats:
            f.pop("offset_mapping")          # not needed for training itself
        return feats

    train_feats = to_features(train_data)
    val_feats = to_features(val_data)

    class _DS(torch.utils.data.Dataset):
        def __init__(self, feats):
            self.feats = feats

        def __len__(self):
            return len(self.feats)

        def __getitem__(self, i):
            return self.feats[i]

    collator = DataCollatorForTokenClassification(tokenizer)
    args = TrainingArguments(
        output_dir=f"/tmp/nlp_finetune_{model_name.replace('/', '_')}",
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        logging_steps=50,
        save_strategy="no",
        report_to=[],
        disable_tqdm=False,
    )
    trainer = Trainer(model=model, args=args, train_dataset=_DS(train_feats),
                       data_collator=collator)
    trainer.train()
    train_seconds = time.perf_counter() - t0

    # ---- evaluate: decode predicted spans, compare to gold spans (exact match) ----
    # Trainer moves the model onto the accelerator (MPS on Apple Silicon, CUDA,
    # etc). Move it back to CPU for this single-example eval loop so the model
    # and the tokenizer's CPU tensors are on the same device — mismatched
    # devices is exactly what "Placeholder storage has not been allocated on
    # MPS device" means. n_val examples one at a time is fast enough on CPU.
    model.to("cpu")
    model.eval()
    tp = fp = fn = 0
    for text, gold_spans in val_data:
        enc = tokenizer(text, truncation=True, max_length=64, return_offsets_mapping=True,
                         return_tensors="pt")
        offsets = enc.pop("offset_mapping")[0].tolist()
        with torch.no_grad():
            logits = model(**enc).logits
        pred_ids = logits.argmax(dim=-1)[0].tolist()
        pred_spans = {(s, e, t) for s, e, t in decode_spans(pred_ids, offsets)}
        # gold_spans carries lowercase template-key types ("side_effect"),
        # decode_spans returns uppercase BIO tag types ("SIDE_EFFECT") - map
        # gold through TYPE2TAG so the comparison isn't comparing two
        # different casings of the same label (was previously silently
        # forcing tp=0 always - the bug behind the all-zero first run).
        gold = {(s, e, TYPE2TAG[t]) for s, e, t in gold_spans}
        tp += len(pred_spans & gold)
        fp += len(pred_spans - gold)
        fn += len(gold - pred_spans)

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision == precision and recall == recall and (precision + recall)
          else float("nan"))
    print(f"    train time: {train_seconds:.0f}s   val: precision {precision:.3f}  "
          f"recall {recall:.3f}  F1 {f1:.3f}  (tp={tp} fp={fp} fn={fn})")
    return {"precision": precision, "recall": recall, "f1": f1, "train_seconds": train_seconds}


# ---------------------------------------------------------------------------
# Zero-shot comparison on the SAME validation set, coarsened to bare entity
# types (drop the refined side-effect value) so it's apples-to-apples with
# the fine-tuned models, which only predict the 4 coarse types.
# ---------------------------------------------------------------------------
def _coarse_labels(finding) -> str:
    if finding.field == "side_effect":
        return "SIDE_EFFECT"
    return {"seizure_mention": "SEIZURE", "drug_mention": "DRUG",
            "test_mention": "TEST"}.get(finding.field)


def zero_shot_eval(name, structurer, val_data):
    tp = fp = fn = 0
    for text, gold_spans in val_data:
        gold_types = {t for _, _, t in gold_spans}
        gold_types = {TYPE2TAG[t] if t in TYPE2TAG else t for t in gold_types}
        try:
            findings = structurer.structure(text, source_question="Q13")
        except Exception:
            findings = []
        pred_types = {c for c in (_coarse_labels(f) for f in findings) if c}
        tp += len(pred_types & gold_types)
        fp += len(pred_types - gold_types)
        fn += len(gold_types - pred_types)
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision == precision and recall == recall and (precision + recall)
          else float("nan"))
    print(f"    {name:<45} precision {precision:.3f}  recall {recall:.3f}  F1 {f1:.3f}")
    return {"precision": precision, "recall": recall, "f1": f1}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+",
                     default=["distilbert-base-uncased", "emilyalsentzer/Bio_ClinicalBERT"])
    ap.add_argument("--n-train", type=int, default=1500)
    ap.add_argument("--n-val", type=int, default=300)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--skip-zero-shot", action="store_true")
    args = ap.parse_args()

    print("\n  NLP fine-tuning comparison - SYNTHETIC pipeline proof-of-concept")
    print("  " + "=" * 78)
    print("  Read the module docstring before trusting these numbers.\n")

    train_data = generate_dataset(args.n_train, seed=args.seed)
    val_data = generate_dataset(args.n_val, seed=args.seed + 999)   # different seed than train
    print(f"  train n={len(train_data)}  val n={len(val_data)}  "
          f"(disjoint seeds, same template/phrase distribution)")

    results = {}
    for model_name in args.models:
        try:
            results[model_name] = train_and_eval(model_name, train_data, val_data, args.epochs)
        except ImportError:
            print("\n  transformers/torch not installed - cannot fine-tune here.")
            print("  Run: pip3 install --user transformers torch")
            break
        except Exception as exc:
            print(f"\n  {model_name} failed: {exc}")

    if not args.skip_zero_shot:
        print("\n  --- zero-shot candidates on the SAME validation set (coarse types only) ---")
        try:
            from nlp.structurer import LexiconStructurer
            from nlp.transformer_structurer import (
                BioBERTStructurer, ClinicalBERTStructurer, GenericNERAdapter)
            zero_shot_eval("LexiconStructurer", LexiconStructurer(), val_data)
            for label, candidate in [
                ("BioBERTStructurer", BioBERTStructurer(lazy=False)),
                ("ClinicalBERTStructurer", ClinicalBERTStructurer(lazy=False)),
                ("d4data/biomedical-ner-all", GenericNERAdapter("d4data/biomedical-ner-all", lazy=False)),
                ("HUMADEX/english_medical_ner", GenericNERAdapter("HUMADEX/english_medical_ner", lazy=False)),
            ]:
                if candidate.is_available():
                    zero_shot_eval(label, candidate, val_data)
                else:
                    print(f"    {label}: unavailable - skipping")
        except ImportError:
            print("  (transformers/torch not installed - skipping zero-shot candidates)")

    print("\n  " + "=" * 78)
    print("  SUMMARY (fine-tuned models)")
    for name, r in results.items():
        print(f"    {name:<35} F1 {r['f1']:.3f}  precision {r['precision']:.3f}  "
              f"recall {r['recall']:.3f}  ({r['train_seconds']:.0f}s train)")
    print()


if __name__ == "__main__":
    main()
