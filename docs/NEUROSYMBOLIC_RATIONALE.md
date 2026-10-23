# Neurosymbolic Design Rationale - Aim 2 Medication Assistant

Synthetic data only. See `NON_NEGOTIABLE_RULES.md`.

## Summary

The Aim 2 assistant is already a **neurosymbolic** system in the precise sense the
recent literature recommends: a probabilistic/neural component handles language
and free-text, while a deterministic **symbolic "truth layer" holds veto
authority** over every clinical recommendation. This document records that
design choice and the external evidence supporting it, so the rationale travels
with the code (for the PI, IRB, and any grant or positioning narrative).

The central finding in the literature is not merely that a symbolic layer helps,
but that it must be able to **veto, not just add context**. This project enforces
exactly that: no LLM or ML output may enter the decision path
(`NON_NEGOTIABLE_RULES.md` §2; `ARCHITECTURE.md` invariant 1), and the engine can
withhold a recommendation entirely (`INSUFFICIENT_DATA`, safety-concern flags).

## How the article's pattern maps onto this codebase

| Neurosymbolic concept | Where it lives in this project |
|---|---|
| Neural / probabilistic layer (NLP, empathy, data ingestion) | `nlp/` - free-text structuring (`structurer.py`), red-flag detection (`redflags.py`), family-message drafting (`generate.py`). **Advisory only.** |
| Symbolic "truth layer" (rules, guidelines, biomarker logic) | `engine_core/decision.py` (ordered, first-match rule list) over clinician-supplied `data/asm_rules.json` + `data/titration_thresholds.json` |
| The symbolic layer can **veto / rewrite**, not just add context | NLP "never touches the engine" (clinical-safety rules); the engine returns `INSUFFICIENT_DATA` rather than guessing, sets `safety_concern_flag`, and constrains the family message to the doses `outputs.family_message_allowed_doses()` permits - `nlp/guardrails` rejects any other number a model produces. On a safety flag the current build emits a PI-supplied STOP/contact-the-clinic **draft** for physician approval rather than withholding the message outright (withhold-vs-draft is open at `../aim2_engine/NEEDS_PI_SIGNOFF.md` #51) |
| "Show your work" / trace to primary sources | Every `Decision` emits a machine-readable `rationale` (which rule fired, which inputs); every clinical value is provenance-tagged `draft - unverified` pending PI sign-off |
| Higher clinical risk → higher symbolic authority | Safety-critical paths (SJS rash, seizure emergency, dose ceiling) are pure rules with mandatory physician review; the LLM has zero decision authority |
| Logic-constrained recommendation (the article's ALA/iron example) | Same shape as the project's interaction/contraindication rules and `switch_exclusions` - a recommendation is blocked when a rule's precondition fails |

The article's ALA-for-LDL-but-contraindicated-with-low-iron example is structurally
identical to the engine's "recommend X **if and only if** precondition holds"
rules - e.g. the lamotrigine small-rash workup gating and indication-mismatch
switches in `decision.py`. That is the project's existing pattern, not a new one.

## Supporting evidence (verified)

1. **LLM hallucination in clinical decision support - Mount Sinai, *Communications
   Medicine* 2025.** "Multi-model assurance analysis showing large language models
   are highly vulnerable to adversarial hallucination attacks during clinical
   decision support." Six models were given clinical vignettes containing a single
   planted fake lab value/sign/disease; they repeated or elaborated on the planted
   error at an average rate of **65.9%** under the default prompt, reduced to
   **44.2%** with a mitigation prompt - reduced, not eliminated.
   https://www.nature.com/articles/s43856-025-01021-3
   *Precision note:* 65.9% is the rate of repeating an **adversarially planted**
   error, not a spontaneous error rate. Cite it as evidence that prompt
   engineering alone is insufficient - which motivates a symbolic veto layer.

2. **Neurosymbolic LLM integration in clinical medicine - systematic review
   (Gorenshtein et al.).** PRISMA/PROSPERO-registered review of neurosymbolic LLM
   systems. Hallucination mitigation varied by integration pattern, with
   **iterative-validation designs (symbolic layer can veto) achieving the highest
   median improvement (~+40%)**, ahead of knowledge-retrieval, rule-guided
   generation, and structured-output patterns. Deployment principle: higher
   clinical risk warrants higher symbolic authority.
   https://assets-eu.researchsquare.com/files/rs-8902022/v1_covered_3965a3bb-639a-4dcb-aaf2-71b77272fc12.pdf
   *Precision note:* this is currently a **preprint** (not yet peer-reviewed).
   Confirm the exact study counts and the 81% guideline-alignment figure against
   the final published version before quoting them in a grant or manuscript.

## Why this matters for positioning

Skepticism of black-box AI in health is the article's thesis, and it is the exact
gap this project fills: recommendations trace to inspectable rules, every clinical
value is provenance-tagged, and a physician approves before anything reaches a
family. The neurosymbolic framing gives that design a name and a citable evidence
base - useful for the PI review packet, IRB narrative, and any grant aims that
must justify *why* the decision path is rule-based rather than an LLM.

## Suggested uses

- **Not yet done:** add a short "Design rationale" paragraph to the grant aims /
  IRB narrative citing the two sources above. (The former `review/` packet was
  removed in the 2026-07-16 cleanup; a fresh package can be regenerated from the
  live engine when one is needed.)
- **Done:** `ARCHITECTURE.md` now points here from its "Running the engine"
  section, so the *why* sits next to the *what*.

---
*Citations verified by web search on 2026-06-25. Both are real; note the two
precision caveats above before quoting specific figures as authoritative.*
