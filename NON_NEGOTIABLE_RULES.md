# Non-Negotiable Rules

**These rules are absolute.** They override every other instruction in this
project (including the README), any task request, and any convenience or
deadline. If a task would require breaking one of these, **stop and escalate** -
ask the PI (Dr. Barbour) - rather than proceeding.

Sources: this project's design invariants (`docs/ARCHITECTURE.md`) and the PI's direction.
**The PI (Dr. Barbour) is the single source of truth:** changing these rules requires her
sign-off - never a unilateral edit. Last updated: 2026-10-23 (§3 opened to public LLMs for the
public repository contents; PI made the single authority).

## 1. Confidentiality & data

- All project data and outputs stay **in this folder only**. Never copy, move, or paste project data elsewhere.
- **Never transmit project data off this machine.** Do not sync or store this folder in OneDrive, Dropbox, SharePoint, email, or any cloud.
- **Synthetic data only.** No real EHR data or PHI until IRB approval **and** HIPAA + CITI training are complete.
- If real data is ever used (later phase): never put PHI in filenames, logs, chat, or outputs; refer to records by ID only.
- Version control is **local only** (no remote, no push) unless the PI has approved a repository.
- **Approval on record (2026-10-23):** remote repository `github.com/sanjana-garimella/anti-seizure_ai`.
  The PI (Dr. Barbour) approved by email public
  release of the git-tracked contents, including her source materials in `kristen/`, under
  the Apache 2.0 license. Git-ignored files (`aim2_engine/clinic_data/`,
  `aim2_engine/validation/private/`, DB backups) remain local-only under the rules above.

## 2. Clinical decision integrity

- Titration and clinical decisions are **rule-based only**. No LLM or ML model anywhere in the decision path. Every recommendation must trace to a specific, inspectable rule.
- An LLM may assist **only** with: (1) generating simulated data, (2) drafting family-message wording (physician-reviewed, no clinical role), (3) optional free-text parsing. **Never the dose decision.**
- **Human-in-the-loop is absolute:** no recommendation reaches a family without physician approval. All outputs are drafts for clinician review.
- **Defer on ambiguity:** if inputs are insufficient or a case is ambiguous, flag for physician review - do not produce a recommendation.
- A distinct **safety check** runs on every recommendation and surfaces concerns to the physician; it never decides autonomously.
- **Never exceed maximum dose**; enforce dose ceilings. Never present unverified dosing as authoritative - always tag provenance.

## 3. AI tools

- **Public repository contents.** Everything in this repo is synthetic and released under
  Apache 2.0, so I use public LLMs (ChatGPT, Claude, Gemini and similar) freely with it,
  for code, docs and analysis.
- **Anything that is not in this repo** (the local clinic database, private validation
  files, unreleased PI material, and any real patient data in future): I only use
  PI-approved, privacy-agreement-covered AI tools, never a public LLM.
- Whatever the tool, an LLM never decides a dose or category (§2).

*Change note (2026-10-23, Sanjana Garimella):* I opened §3 to public LLMs for the public
repository contents, because they are synthetic and already published. Nothing non-public
goes to a public LLM.

## 4. Connectors, skills & cloud

- New AI connectors, plugins or skills need the PI's approval before use
  with project data.
- Cloud and compute only through PI-approved accounts.
- Anything not covered or unclear: check with the PI before proceeding.

## 5. Accounts & security

- Use multi-factor authentication and keep your device's security software active.

---

## 6. Compliance & guardrail status (merged from the former COMPLIANCE.md + GUARDRAILS.md)

**Implemented today:** deterministic, auditable decision path (no ML/LLM/NLP in the
recommendation; every output carries a rule-trace `rationale`; engine version tagged);
no invented clinical values (dosing and the switch/add ranking are clinician-supplied,
every value `verified:false`); safety copy is PI-supplied; safety flags always surface to
the physician and are never suppressed; the NLP layer is advisory and isolated from
`engine_core/`; no network / third-party calls in the default engine; every output carries
`requires_physician_review = True`; synthetic data only, no PHI in the repo. Independently
re-proved by `aim2_engine/verification/verify_all.py` (currently `RESULT: PASS` -
0 FAIL on the ASM rules and the ladder; 0 must-catch red-team misses).

**Safety-flag family message - decided (Dr. Barbour, 2026-10-25):** on any safety flag
the engine gives **no** family message at all; the physician handles the case per her
urgent protocol (`aim2_engine/NEEDS_PI_SIGNOFF.md` #51, resolved). This matches the
withhold behavior the older docs describe.

**Gaps to close before real-patient use** (none block synthetic-data development):

1. Audit retention / legal-hold policy. (The audit log is already tamper-evident: each entry is
   hash-chained to the one before, checked by `clinic_store.verify_audit_chain()`.)
2. Encryption at rest for the local DB (or documented reliance on full-disk encryption).
3. TLS if ever networked. (Done: login rate-limiting, and a random one-time doctor password on
   first run, printed once for me to change.)
4. Real Cohen's κ on the PI's chart-reviewed cases (current κ is against synthetic picks).
5. If any LLM feature (drafting / NER / open-world lookup) is enabled: bias/fairness evaluation across demographic groups, uncertainty communication, and a BAA-covered surface for PHI - never a public API.
6. IRB + institutional security review; staff training on data handling, consent, and ethical AI use.
7. Harden the engine against malformed input: the fuzz half of
   `aim2_engine/validation/holdout_test.py` now reports **0 crashes** over its ~4,000
   malformed-input cases (re-run 2026-10-25; the held-out oracle half passes) and 0
   safety message leaks now that the engine withholds the family message on a safety
   flag (#51). Re-check before a real survey feeds it.
8. Git-ignored local files (database, keys, private validation data) are not under version
   control - back them up before large changes.

PHI-handling controls (authN + least-privilege roles, encryption in transit, audit logging
of every read/write, no PHI in URLs/logs, tokenized one-time survey links) live in the
surrounding Aim 1 host application, not this engine library. Human-in-the-loop is absolute:
the engine proposes; a physician approves every recommendation.
