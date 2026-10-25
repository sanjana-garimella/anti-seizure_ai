# Roadmap: hybrid RAG for a growing body of clinical context (proposed, not built)

**The problem.** The clinical context keeps growing: four decision tables, the dosing
sheet (now v9, with ten starting-schedule tabs), tablet rules, message templates, PI
emails and notes, 70+ sign-off items, FDA label references and trade-off docs. Today
people transcribe all of it by hand into JSON rules and Markdown. That gets harder to
keep consistent (for example, the template F taper table vs the 2026-10-23 email),
harder for a new collaborator to navigate, and too large to paste into an LLM prompt.

**The idea.** Keep the rule engine as the only thing that decides doses and
categories, and add **retrieval** over the source material for everything around the
decision: explaining, finding, checking and wording. "Hybrid" here means two things:

1. **Hybrid retrieval.** Combine keyword search (BM25, good for drug names, rule IDs and
   numbers), embedding search (good for "how do we handle a rash on lamotrigine?"
   phrasing) and exact structured lookup (drug + weight band + rule ID straight from
   the JSON), then re-rank.
2. **Hybrid reasoning.** Rules decide; retrieval supplies the cited source; an LLM (if
   used at all) only phrases what the rules and sources already say.

```
question / decision ──► rule engine (decides) ──► rationale: rule IDs + inputs
                                │
                                ▼
                  hybrid retriever (BM25 + embeddings + exact JSON lookup)
                  over a provenance-tagged corpus: kristen/ sheets & notes,
                  NEEDS_PI_SIGNOFF, FDA reference, data/*.json rows, templates
                                │
                                ▼
          cited passages ──► (optional) LLM phrasing ──► guardrails ──► physician
```

**Where it would help, from lowest to highest risk**

| Use | Who | Notes |
|---|---|---|
| "Why?" panel: next to each recommendation, the PI source passage behind the rule that fired (sheet, tab, row, version) | clinicians | Retrieval only, no generation |
| Search and Q&A over the project with citations ("where does the 30/70 split come from?") | engineers, clinicians | Answers must cite; abstain when no source is found |
| Conflict finder: flag where sources disagree (v6 vs v9, template vs email, sheet vs JSON) | engineers, PI | Turns surprises into sign-off items |
| Grounded entity normalization: match free-text mentions to a reference list of drugs and side effects | ML | Already on the to-do list in `docs/ai-engineering-applicability.md` |
| Grounded wording drafts for family messages, with each sentence tied to a template or fact | PI review | Highest risk; last, and only behind the existing guardrail |

**To do**

- [ ] Build the corpus: chunk each source with provenance (file, sheet/tab, row, version,
      date, `verified` flag). Synthetic and source documents only; nothing private.
- [ ] Local hybrid index: a stdlib BM25 baseline first, then optional
      `sentence-transformers` embeddings (already an optional dependency), plus exact
      lookup keyed on the rule and ladder IDs the engine already emits.
- [ ] Evaluation set: 30–50 real questions taken from the sign-off items and review
      meetings, with the correct source passages. Measure retrieval recall@k and
      citation accuracy before adding any generation.
- [ ] Ship the "Why?" panel and the conflict finder first (no LLM needed).
- [ ] Only then try LLM phrasing, on an approved enterprise model, with
      `guardrails.py` and physician review unchanged.

**Guardrails that stay fixed**

- Retrieval and LLMs never choose a dose or category, and never write into engine inputs.
- Every generated sentence cites a source; no source means no answer.
- Any number in generated text must pass `family_message_allowed_doses()`.
- No PHI in the index. Any LLM, embedding service or connector must be on the approved
  list or get the PI's approval first (`NON_NEGOTIABLE_RULES.md` §3–4).
