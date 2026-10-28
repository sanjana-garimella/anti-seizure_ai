# Walkthrough for Dr. Barbour - new code, 2026-10-23 (DRAFT)

What to show, in what order, and what to ask. About 40 minutes.
All cases are synthetic demo patients. Every dose shown is a DRAFT
(`verified:false`) for physician review; nothing is sent to a family without approval.

## Before the meeting

- Start the app: `cd aim2_engine && python3 clinic.py` (or the `clinic-test` preview on
  port 8011). Log in as `doctor`.
- The review queue has 30 cases. All saved notes and family drafts were rebuilt today
  with the current engine (ladder v9).
- Not committed yet: the v9 ladder load and the starting-schedule tabs (sign-off #70)
  are still uncommitted work. Commit or confirm they are final before showing sections 5-6.
- Have open: `kristen/questions_round_2.md`, `kristen/logic_tradeoffs_for_discussion.md`
  (sections 14-15), `aim2_engine/NEEDS_PI_SIGNOFF.md` (#60-70).

## Order at a glance

| # | Topic | Cases to open | Sign-off items |
|---|---|---|---|
| 1 | Family message names the tablets | Iris, Jonah, Lena | #66, #67 |
| 2 | Age rule: 10 and under gets liquid | Maya, Owen | #61, round-2 Q6 |
| 3 | Plans that mix liquid and tablets | Hazel | new (below) |
| 4 | Non-round ladder doses | Felix, Milo, June, Ada, Cole | new (below) |
| 5 | v9 dosing sheet loaded | any escalate case | #70 |
| 6 | Starting schedule when switching | Sam, Ben, Mia, Leo | #69, #70 |
| 7 | Survey changes (live family survey) | family check-in link | #68, round-2 Q1, Q2, Q5, Q9, Q10 |

---

## 1. Family message names the tablets (#66, #67)

**Show:** Iris (12 y, 30 kg, levetiracetam 2 × 250 mg tablets).
Family draft: "First, give 2 tablets in the morning and 2½ tablets at night (250 mg each,
1125 mg/day) for 1 week. Then, give 2 tablets in the morning and 3 tablets at night
(250 mg each, 1250 mg/day)… don't go higher than 1250 mg/day."

- The tablet counts come from the same plan as the clinician summary, so the two never disagree.
- The "don't go higher than" limit is the tablet total, which stays inside the -10%/+30% window
  and never goes above the absolute maximum.

**Then Jonah** (13 y, 60 kg, 750 mg tablets): the 750 mg tablet fits both steps, so it is kept.

**Then Lena** (13 y, 40 kg, 750 mg tablets): 750 mg does not fit, so the plan uses the standard
500 mg tablet and the draft says "This uses 500 mg tablets, a different tablet from the 750 mg
tablets your child takes now."

**Ask:**
- Is the tablet wording right for families? (#67)
- Is 500 mg the right standard levetiracetam tablet? Should other medicines get a standard strength? (#66)

## 2. Age rule: 10 and under gets liquid (#61)

**Show:** Maya (10 y, 30 kg, family reports 2 × 250 mg levetiracetam tablets). This was your
worked example (uneven AM/PM, half tablet). Under the current age rule (age 10 = liquid
"for now") she now gets liquid: 5.25 mL then 6 mL twice a day. The summary says
"Age 10 y: showing liquid per the age rule (family reports tablets)."

**Then Owen** (3 y, 12 kg): liquid, as expected for a young child.

**Ask:**
- Exactly 10 years old: tablets or liquid? (round-2 Q6)
- Should age override the form the family is already using, or keep their current form? (#61)

## 3. Plans that mix liquid and tablets (new)

**Show:** Hazel (12 y, 52.2 kg, ethosuximide 500 mg twice a day). Step 1 is given as liquid,
step 2 as tablets:

> "…increase to 1044 mg/day … for 1 week. Then, increase to 1217.83 mg/day (about 608.91 mg
> twice a day) and stay at this dose. Please don't go higher than 1250 mg/day…
> How much to give: first step, 10.44 mL twice a day; second step, 2 tablets in the morning and
> 3 tablets at night (250 mg each) = 1250 mg a day in total."

- **Fixed today:** the limit used to say 1217.83 mg/day while the tablets added up to 1250, so the
  family was told a limit below the dose they were told to give. The limit now uses the tablet
  total, the same rule as the all-tablet plans.
- **Still uneven:** the step sentence says 1217.83 mg/day, while the "How much to give" line says
  1250.

**Ask:**
- When only some steps convert to tablets, should the step sentence name the tablets
  (like the all-tablet plans) instead of the ladder mg/day?
- Ethosuximide 250 mg is described as "tablets". If the product is a capsule, should the wording say capsules?

## 4. Non-round ladder doses (new)

**Show:** Felix (8 y, 41.7 kg, ethosuximide, faster pace): "increase to 1112.14 mg/day (about
556.07 mg twice a day) … 11.12 mL twice a day."
Then Milo (4 y, 18.3 kg, oxcarbazepine): "233.325 mg/day … 1.94 mL twice a day."
June, Ada and Cole show the same pattern for ethosuximide, levetiracetam and lamotrigine.

- The ladder is mg/kg × weight, so a fractional weight gives doses like 1112.14 mg/day.
- **Fixed today:** the safety check on family messages wrongly flagged some of these as
  "possibly made-up numbers", because the message prints 6 digits (1217.83) and the check
  compared against the unrounded value (1217.832…). The check now accepts exactly the printed
  ladder value and still rejects any other number.

**Ask:**
- Should the family message round doses to something measurable? For example, the nearest
  5 or 10 mg/day, and the nearest 0.1 mL (or what the syringe shows). No one can measure
  1.94 mL or 116.66 mg.
- If yes, what rounding for each form (liquid mL, mg/day), and should the clinician summary
  keep the exact value?
- Rounding would change the clinical numbers, so it is not built until you decide.

## 5. v9 dosing sheet loaded (#70)

- `final_doses_titration_v9.xlsx` now drives the dose ladder. It reproduces v6 exactly, apart
  from the two documented oxcarbazepine ≥70 kg hand corrections.
- All six v6 data-quality flags are resolved in v9. Two new ones:
  - zonisamide child step 1.5 is above the max of 0.7
  - brivaracetam ≥50 kg start is above the minimum maintenance dose
- Some demo doses changed because of v9. For example, the 70 kg ethosuximide case went from
  1166.43 to 1050 → 1400 mg/day.

**Ask:** the two new data-quality flags, and section 15 of
`logic_tradeoffs_for_discussion.md`.

## 6. Starting schedule when switching (#69, #70)

**Show:** Sam (13 y, 50 kg, carbamazepine → oxcarbazepine). In the clinician summary, under
"Starting schedule for oxcarbazepine (Dr. Barbour's initiation tab, DRAFT)", the week-by-week
plan is shown for tablets (preferred by age) and for oral suspension.
Then Ben (oxcarbazepine, 45-50 kg band, 5 weeks) and Mia / Leo (levetiracetam: oral solution
preferred by age, plus tablets).

- The schedule is copied from your initiation tabs. It defers when there is no weight band,
  two bands match, there is no tab (carbamazepine), or lamotrigine is taken with valproate or
  an enzyme inducer — **including the current medicine** on add or switch (2026-10-28).
- **Built (DRAFT, #69):** on a switch, the doctor's summary also shows stopping the old medicine
  (week 1 current dose, then ×0.75 / ×0.50 / ×0.25, off after week 4; new medicine starts week 1).

**Ask:** confirm taper timing vs long starting tabs (section 14 of `logic_tradeoffs_for_discussion.md`;
lamotrigine after valproate: `add_start_medication_logic.md` section 0).

## 7. Survey changes - live (#68)

Open the family check-in survey from the login page and click through:

1. The increase / add / switch preference questions (Q12, 12a, 12b, and the at-goal
   "continue increasing vs add" question) are gone.
2. Medicine-option questions moved to **page 2**, only after submit, when the decision is
   switch or add (no safety flag) — not a page-1 "likely" rule anymore.
3. Answer side effects at **Level 3**: the new question offers wait / uneven AM-PM /
   three-times-a-day, showing only the options the engine suggests. The answer goes to the
   clinician summary only and never changes the decision.

**Ask (from `questions_round_2.md`):**
- Q1: at Level 3, does the family choose, or do you choose? For seizure-free children,
  breakthrough, or both?
- Q2: which medicines can be given three times a day?
- Q5: may we draft the family wording for the three choices? (#52)
- Q9: limit suggestions to tablet products for children over 10?
- Q10: confirm removing the preference questions. This is a rule change: R7, R15 and R16 no
  longer fire from the survey.

---

## Decisions to leave with

- [ ] Tablet wording in the family message (#67) and the 500 mg standard levetiracetam tablet (#66)
- [ ] Age 10: tablets or liquid; does age override the family's current form (#61)
- [ ] Mixed liquid/tablet plans: name the tablets in the step sentence?
- [ ] Rounding family doses to measurable amounts (mg and mL), or keep exact values
- [ ] Ethosuximide: tablets or capsules wording
- [ ] v9 new data-quality flags: zonisamide, brivaracetam (#70)
- [ ] Stopping the old medicine during a switch (#69)
- [ ] Survey: Level-3 choice, three-times-a-day medicines, removing preference questions (#68)
- [ ] Still open from before: withhold vs STOP-draft on safety flags (#51)

## Verification status (for reference)

- Unit tests: 478 pass
- Validation: Cohen's κ 0.976 (target ≥ 0.8, MET), against synthetic picks, not your chart-reviewed cases
- Independent proof suite: RESULT: PASS
- Synthetic sweep of 2,496 family drafts: none fail the message safety check, and none tell
  the family a limit below the tablet total
