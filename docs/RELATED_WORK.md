# Related work and the inspectable LLM

Dr. Barbour's sheets are the source of every dose. This note is why the
decision path is rules, and the one language-model use I would add later.

## Why the rules decide

- Mount Sinai, *Communications Medicine* 2025. Six models repeated a planted
  false clinical detail 65.9% of the time under the default prompt, and 44.2%
  after a mitigation prompt. That is a planted error, not a spontaneous error
  rate. https://www.nature.com/articles/s43856-025-01021-3
- Gorenshtein et al., neurosymbolic-LLM review, still a preprint. Designs
  where the symbolic layer can veto had the largest drop in hallucinations,
  about 40%.
- Russell and Norvig, *Artificial Intelligence: A Modern Approach*. The engine
  is a knowledge-based agent: explicit rules, percepts to actions. Fuller
  mapping: `NEUROSYMBOLIC_RATIONALE.md`.

## What the models are for

The clinic default for free text is HUMADEX. ClinicalBERT (Alsentzer),
BioBERT, and the lexicon are the comparisons. They read the family's note and
can only raise a flag. ADE-Corpus-V2 (Gurulingappa et al., 2012) is the
outside benchmark: MEDLINE case reports, not caregiver speech.

Chen 2024, `drug_safety_and_recommendation`, is the pattern for the openFDA
FAERS lookup and the audit log. It does not choose a dose.

FDA labels are a cross-check. Where a label and her workbook disagree, the
workbook wins.

## Inspectable LLM (proposed, not built)

The model would only restate a decision `engine_core/` already made. The
slot is `GuardedGenerator` in `aim2_engine/nlp/generate.py`: a fact-only
prompt, `guardrails.py` on the output, and the template if the model is
missing or fails a check.

Inspectable means the review screen shows four things:

1. **Inputs.** The rule that fired, the structured facts, and the allowed
   milligram numbers. Nothing else enters the prompt.
2. **Output.** Each sentence is tied to one of those facts, or to a cited
   passage from her sheet. A sentence with no source is dropped.
3. **Check.** A number outside `family_message_allowed_doses()` fails. A
   safety flag skips the model and leaves no family message.
4. **Diff.** The template and what the model changed. Approving the message
   approves that diff.

I would ship the trace on the template first, with no model. Generation
comes after that view is in the review queue, on an approved local model,
behind the same guardrail. Retrieval over her files, if I build it, stays
the step in `ROADMAP.md`: the "Why?" panel before any phrasing.
