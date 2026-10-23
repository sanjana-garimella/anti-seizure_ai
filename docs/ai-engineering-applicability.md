# AI Engineering (Chip Huyen) - Applicability to Aim 2

Backlog of concepts to potentially build. **Hard boundary:** nothing here touches the
rule-based decision path. `engine_core/` stays ML/LLM-free (NON_NEGOTIABLE rule #1).
Everything below lives in the **advisory NLP layer**, **eval infra**, or **engineering discipline**.

*Progress re-checked against the code 2026-08-04.*

## Build first - Evaluation (Ch. 3–4)
- [x] Formalize the HUMADEX vs. ClinicalBERT eval into a repeatable pipeline (fixed held-out set, versioned) - `validation/nlp_finetune_compare.py`, `nlp_external_eval.py`, `nlp_freetext_eval.py`, `nlp_pubmed_eval.py`, with `tests/test_nlp_freetext_eval.py` locking it in. HUMADEX currently outperforms ClinicalBERT on two real-text evals; ClinicalBERT is still the coded default (`nlp/transformer_structurer.py`).
- [x] Define and document per-model metrics (not just accuracy - precision/recall on clinical entity extraction) - `validation/nlp_eval_metrics.py`, `nlp_eval_stats.py`, `nlp_confusion_matrix.py`.
- [x] Guard against leakage inflating the comparison - `validation/leakage_safe_cv.py` (grouped + purged CV, `optimism_gap()`), 11 tests, report at `validation/nlp_leakage_report.json`. Engineering artifact only; never clinician-facing.
- [ ] Add data-contamination checks (ensure eval text isn't in any training/reference set) - leakage-safe CV covers near-duplicate inflation *within* our corpus, not overlap with a model's pretraining data.
- [ ] Log eval runs with provenance so model-selection decisions are auditable - reports are written as dated JSON (e.g. `nlp_pubmed_eval_result_20260701T222513Z.json`) but there is no single versioned run log.

## Dataset engineering (Ch. 8) - pre-IRB
- [x] Synthetic-data generation for the test corpus (simulated data only until IRB/HIPAA/CITI clear) - `nlp/generate.py`, `nlp/typo.py`, `nlp/weak_supervision.py`, `validation/nlp_synthetic_eval*.py`, `../synthetic_validation/`.
- [ ] Written annotation guidelines + inter-annotator quality checks - the two-rater protocol is pre-registered in `VALIDATION_PLAN.md` §2 but no guidelines document exists yet.
- [ ] Define what "data quality" means for clinician free-text inputs.

## Security hardening - advisory path only (Ch. 5)
- [ ] Prompt-injection / prompt-attack defenses if any LLM parses free-text notes.
- [ ] Treat free-text ingestion as an untrusted input surface.

## NLP layer improvements - advisory, never engine input (Ch. 6–7)
- [ ] RAG-grounded entity extraction (ground findings in a reference set) - output stays advisory.
- [ ] Finetune the extractor for pediatric epilepsy free-text - output stays advisory.
- [ ] Enforce: NLP findings never written into IntervalSurvey or any engine input.

## Conceptual backing (Ch. 2) - no build
- Sampling/hallucination material justifies why NLP output must stay "draft - unverified" + flagged.

## Explicitly OUT of scope
- Agentic / auto-acting patterns (Ch. 6 agents, Ch. 10 auto-feedback loops).
  Violates human-in-the-loop + rule-based-only. Reference as what *not* to do.

---

# Related Reading - ranked (TOCs verified)

Boundary holds: ML/LLM material informs the **advisory NLP layer** only, never `engine_core/`.
Russell & Norvig is the exception - it informs the **rule-based engine** design itself.

## For the advisory NLP layer (ClinicalBERT / HUMADEX extraction)

1. **Deep Learning for NLP - Surdeanu.** *Most directly applicable of all.* The exact
   toolkit for clinical entity extraction from free-text notes:
   - Ch 10 - RNN/LSTM + **Conditional Random Fields + Viterbi** (sequence labeling = clinical NER)
   - Ch 11 - POS/sequence-tagging implementation
   - Ch 12–13 - Transformers, fine-tuning, **using transformers with Hugging Face**
     (ClinicalBERT/HUMADEX are HF models; Ch 13 covers token classification)
   - Ch 2–3 - text-classification **evaluation measures** (precision/recall/F1) for your eval
2. **Hands-On Large Language Models - Alammar & Grootendorst.** Applied embeddings/transformers:
   - Ch 6 - embeddings for **semantic search**
   - Ch 10–11 - create/fine-tune an embedding model; **fine-tune BERT for classification**
   - Caveat: examples are Colab/GPU-oriented and use OpenAI/Cohere API keys.
3. **Hands-On ML - Géron.** Eval rigor for the HUMADEX vs. ClinicalBERT comparison:
   - Ch 1 - overfitting, poor/nonrepresentative data, testing & validating, model selection
   - Ch 2 - end-to-end project: pick a performance measure, cross-validation, fine-tuning
   - Ch 3 - **confusion matrix, precision/recall, ROC** (your core metrics)

## For the rule-based engine + decision map / knowledge graph

4. **Artificial Intelligence: A Modern Approach - Russell & Norvig.** The only book that
   speaks to `engine_core/`'s paradigm. Framing = "knowledge-based agent" mapping
   percepts→actions via **explicit knowledge representation and reasoning** - i.e. your
   rule engine + knowledge graph. Design reference (not code that enters the engine):
   - Knowledge representation & logical agents
   - Quantifying uncertainty / probabilistic reasoning (if you ever add confidence to flags)

## Situational (only for a specific need)
- **Advances in Financial ML - López de Prado** - purged/embargoed cross-validation &
  leakage detection, to guard against patient-level leakage inflating eval numbers.
- **Foundations of ML - Mohri et al.** - generalization bounds / model-selection theory,
  if you must formally defend that a model generalizes.
- **Deep Learning - Goodfellow et al.** - dense theory reference for architecture internals.

## Skip for this project
A Thousand Brains · Data Science from Scratch · Calculus (Strang) · Calculus Vol 1–3 ·
Linear Algebra for Everyone · Algorithmic Toolbox Notes · CLRS
(general math/algorithms - revisit CLRS only for a concrete decision-map graph problem).
