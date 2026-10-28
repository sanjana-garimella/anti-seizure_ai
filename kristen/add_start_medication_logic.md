# Adding or switching a medicine — what the tool does and where I need your input

DRAFT, 2026-10-28. Synthetic cases only. I treat every output as a draft until you sign off.  
Open items are in `aim2_engine/NEEDS_PI_SIGNOFF.md` (#41, #52, #69, #70). Dose-formulation trade-offs are in `logic_tradeoffs_for_discussion.md`.

---

## 1. Flow

**What I built.** After page 1, the engine assigns a category (add, switch, titrate, hold, and so on). If the category is add or switch and there is no safety flag, the family can continue to page 2: preference questions, then my top three options and their ranking (they may skip). You see the note, the recommendation, and a **Medication options (DRAFT)** card with starting schedules when page 2 ran. I never choose the final drug or dose.

**Trade-off.** Page 2 runs only after the real decision, so add/switch families take an extra step, but titrate/hold families are not asked medicine-option questions. Ranking does not change the category.

**I'd like your call on.** Is the two-step flow OK? Should page 2 also appear for R12g (increase vs add in the draft)?

---

## 2. When I recommend “add a second medicine”

| Path | When |
|---|---|
| R5b | The current medicine is helping, seizures continue, side effects block going higher |
| R9-add | Frequent or long breakthrough, at maximum dose, Q11 = **better** |
| Breakthrough grid, level 4 | Six-level side effects on the grid route → lower dose and add |
| Breakthrough grid, at max | Would escalate but already at max → add |
| Level 3, family chooses add | Breakthrough with dosing choices → lower and add second med |
| R12g | At goal, medium side effects, still seizing → draft **asks** increase vs add (category not add yet) |

**Not add:** R9-switch (at max, Q11 not “better”), R6 (intolerable side effects → switch). The grid needs the six-level side-effect scale and/or the tier route; otherwise I still use R5b–R13.

**Trade-offs.** At max, “helping” is Q11 alone. Level 4 on the grid goes to lower-and-add even when breakthrough is non-urgent. Where your sheet allows hold or add at max, I pick add. R12g does not open page 2.

**I'd like your call on.** (1) Is Q11 enough at max? (2) Level 4: lower-and-add or lower first? (3) Offer hold or add at max? (4) Page 2 for R12g?

---

## 3. Lower the dose and add

**What I built.** The family draft says to lower to **[dose for you to confirm]** and add a second medicine. I do not compute the lower dose (#50).

**Trade-off.** You retain control of the lower dose; the family message stays non-specific. I do not state when the new medicine should start relative to the lowering.

**I'd like your call on.** (5) Name one ladder step down in the draft? (6) Start the new medicine with the lowering or after it settles?

---

## 4. Page 2 (medicine-option questions)

**What I built.** If switch or add and no safety flag: medicines tried (current med hidden but counted), avoid lab-heavy options, mood/behavior/sleep/migraine, Southeast Asian or Han ancestry, health conditions, then top three and family rank. Skip is allowed.

**Trade-off.** The same questions for add and switch. If they skip, you get the category without ranked options or schedules on the review card.

**I'd like your call on.** (7) Different questions for add? (8) Page 2 for R12g?

---

## 5. Which medicine (ranking)

**What I built.** Your switching workbook → drop avoids, rank by favors then base order, show top three. Same logic for add and switch. Nine medicines in your rank order; I never offer carbamazepine as a new med; ethosuximide is not on the list (initiation tab exists).

**Trade-off.** I do not apply drug–drug pairing rules. I reuse the switch workbook for every add.

**I'd like your call on.** (9) Pairing rules for add? (10) Ethosuximide for absence-only? (11) OK to keep switch ranking for add until pairing rules exist?

---

## 6. Starting schedules and switch taper

**What I built.**

- I show your v9 initiation tabs verbatim when a single band applies; otherwise I defer (unclear band, no tab, or tab blocked — e.g. lamotrigine with valproate or an enzyme inducer; **the current medicine counts** as a co-med).
- Switch taper (DRAFT, #69): old medicine full dose week 1, then ×0.75, ×0.5, ×0.25, off after week 4; new medicine starts week 1.
- **Review card:** schedule per option after page 2.
- **Clinical summary:** schedule and taper only when I already named a **switch target** (e.g. R6, R9-switch). Many page-2 visits: schedules on the card only. **Add:** no starting schedule in the summary; family text keeps the current med and does not name the new drug or doses.

**Trade-offs.** Lamotrigine with valproate on switch: start timing is unsettled. Taper ends at week 4 while many tabs run longer. Summary vs card can differ when there is no switch target. Monotherapy tabs may not fit add-on starts.

**I'd like your call on.** (12) Lamotrigine week 1 vs after valproate off? (13) Old med off week 4 vs low dose until the new med reaches goal? (14) Second family message with start doses after you choose? (15) Same tabs for add-on? (16) Hold the current dose during add-on? (17) Four-week follow-up from start or from target?

---

## 7. First seizure medicine

**What I built.** Check-ins assume the child is already on an ASM. I have no first-medicine path.

**I'd like your call on.** (18) Should I build one? (19) Early check-ins (e.g. lamotrigine rash at week 2)?

---

## What I'd prioritize in our meeting

Lamotrigine/valproate timing · pairing rules for add · lower-and-add dose and timing · family message with starting doses · first-medicine scope.
