"""
nlp/negation.py
----------------
A coarse, dependency-free negation heuristic shared by the point-A
structurers (LexiconStructurer, TransformerStructurer). Catches the common
"denies any X, Y, and Z" / "no rash or dizziness" pattern that the synthetic
eval (validation/nlp_synthetic_eval.py) surfaced as a real false-positive
source in all three structurers.

THIS IS NOT a full negation-scope parser (no NegEx/medspaCy dependency is
available under the project's stdlib-only constraint). It only checks whether
a negation cue word appears earlier in the SAME sentence/clause as the
candidate finding, and the scope resets at sentence punctuation AND at
contrastive conjunctions ("but"/"however"/"though"/"except") so "denies any
nausea, but has been hyper" does not also suppress "hyper" — a 10,000-case
templated stress run (validation/nlp_synthetic_eval_large.py, the "mixed
negation" case type) caught exactly this failure before the conjunction
boundary was added. Remaining known gap: a same-clause construction like "no
rash, but definitely dizzy, and also dizzy again" within one un-punctuated
clause could still misfire in unusual orderings - this heuristic trades a few
false negatives for fewer false positives, which is the right tradeoff for an
ADVISORY suggestion layer (point A). It is intentionally NOT used by the
red-flag safety net (point B, nlp/redflags.py), which is deliberately
high-recall / escalate-only — suppressing on negation there would risk
missing a true urgent signal, which is the one place we'd rather over-flag.

DELIBERATELY EXCLUDED: bare contractions like "n't" (couldn't / won't /
wouldn't). Those look like negation cues but in this app's vocabulary they
are usually the SIGNAL itself, not a negation of one — "we couldn't afford
it" (missed_dose_reason: cost_or_insurance) and "he won't take it"
(missed_dose_reason: child_refuses) both use "n't" as part of the meaningful
phrase. A first version of this heuristic caught "n't" generically and
broke both of those cases (see tests/test_nlp.py).
"""

from __future__ import annotations

import re

# HARD scope-reset boundaries: sentence punctuation OR a contrastive
# conjunction. Either one ends how far back a negation cue can reach.
_HARD_RESET_RE = re.compile(r"[.!?]+|\b(?:but|however|though|except)\b", re.I)

# AFFIRMING-verb reset (finding D4): "denies fever and has a rash" used to mark
# "rash" negated, because a plain "and" does not reset scope. A caregiver
# routinely lists a denied symptom and an affirmed one joined only by "and"
# ("denies fever and developed a rash"). When an affirming verb (has/have/had/
# developed/got/is/was/started/showed) appears AFTER the negation cue AND that
# verb is not itself negated ("has not had"), the clause has switched from
# denial to affirmation, so the negation scope ends there. This is deliberately
# narrow: "denies fever and chills" (no affirming verb between them) keeps BOTH
# negated, as before, and "she has not had any rash" keeps "rash" negated
# because the affirming verb "had" is itself under the "not".
_AFFIRM_VERB_RE = re.compile(
    r"\b(?:has|have|had|developed|got|is|was|started|showed)\b", re.I)
_NEGATION_RE = re.compile(
    r"\b(den(?:y|ies|ied)|no|not|never|without|absent|ruled\s*out|free\s*of)\b",
    re.I,
)


def is_negated(text: str, start: int) -> bool:
    """True if a negation cue precedes position `start` within the same
    sentence/clause of `text`. Scope resets at (a) sentence punctuation, (b)
    contrastive conjunctions like "but", and (c) an affirming verb (has/had/
    developed/...) that is not itself negated — see finding D4. `start` is a
    character offset into `text`."""
    if not text or start <= 0:
        return False

    # 1) Nearest HARD reset (punctuation / contrastive conjunction).
    scope_start = 0
    for m in _HARD_RESET_RE.finditer(text, 0, start):
        scope_start = m.end()

    # 2) An affirming verb after the nearest negation cue flips the clause to
    #    affirmation — UNLESS that verb is itself negated ("has not had"). Find
    #    the last negation cue in scope, then look for a non-negated affirming
    #    verb after it and move scope_start past that verb.
    neg_matches = list(_NEGATION_RE.finditer(text, scope_start, start))
    if neg_matches:
        last_neg_end = neg_matches[-1].end()
        for vm in _AFFIRM_VERB_RE.finditer(text, last_neg_end, start):
            # Is this affirming verb itself under a negation cue immediately
            # before it (e.g. "not had")? If so it is NOT an affirmation reset.
            window = text[scope_start:vm.start()]
            trailing_neg = list(_NEGATION_RE.finditer(window))
            if trailing_neg and not text[trailing_neg[-1].end():vm.start()].strip():
                continue  # "(no|not|never ...) <verb>" — still a denial
            scope_start = vm.end()
            break

    preceding = text[scope_start:start]
    return bool(_NEGATION_RE.search(preceding))
