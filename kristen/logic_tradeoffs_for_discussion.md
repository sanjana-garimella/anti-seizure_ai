# How the tool decides: key areas and trade-offs (for discussion with Dr. Barbour)

DRAFT, 2026-10-23. Synthetic data only. Nothing here changes a dose decision until you sign off;
every output stays a draft for physician review.

Each section says **what the tool does today**, **why** (your earlier instruction where there is
one), the **trade-off**, and the **question for you**. Sign-off item numbers refer to
`aim2_engine/NEEDS_PI_SIGNOFF.md`.

---

## 1. Turning a ladder dose into tablets or liquid

**Today**
- The ladder gives a daily dose (mg/day). The tool looks for a tablet plan whose **total daily
  dose is within 10% below to 30% above** that ladder dose, and never above the absolute maximum.
- If several plans fit, it picks the **closest total**, ties go **up**, then whole tablets over
  halves, even over uneven, and fewest tablets.
- At most **4 tablets per dose** are considered.

**Why:** your flags #1, #2 and #4 ("try with total dose", "bias up", "+25% is acceptable").

**Trade-off**
- A wide window (+30%) means a plan almost always exists, but the child may get noticeably more
  than the ladder step. Example: 30 kg, 250 mg tablets, ladder 1200 → **1250 mg/day** (+4%), but
  45 kg on 500 mg tablets, ladder 1800 → **2000 mg/day** (+11%).
- A narrow window keeps doses closer to the ladder but makes "no tablet fits" and liquid
  fallbacks more common.
- The 4-tablet cap is a usability choice. Small children on small tablets may need more.

**Questions:** Is −10%/+30% still right, or should the final (goal) step be tighter than the
climb steps? Is 4 tablets per dose right for every drug and age?

---

## 2. One tablet strength per medicine (new, #66)

**Today:** levetiracetam keeps the family's **current tablet** if it fits every step as well as
500 mg does. Otherwise it switches to **500 mg**, never to another strength. Other medicines still
pick the best-fitting single strength.

**Why:** the study team saw the tool suggest 250, 500 or 750 mg depending on the child, and
asked for one tablet everywhere.

**Trade-off**
- One standard tablet is simpler for families and pharmacies, and the plan is predictable.
- But some steps that only a 250 mg tablet could hit now fall back to **liquid**. Example: a
  12-year-old on 500 mg tablets going from 1000 to 1050 mg/day gets a liquid option instead of
  250 mg tablets.
- Switching a family to a new strength means a new prescription and a chance of mix-ups at home.
  The family draft says so ("a different tablet from the 750 mg your child takes now").

**Questions:** Is 500 mg the right standard for levetiracetam? Should other medicines get a
standard too (e.g. lamotrigine 100 mg, oxcarbazepine 300 mg)? When the standard doesn't fit, which
is better: liquid, skipping to the next step, or allowing a second strength just for that step?

---

## 3. Tablets vs liquid by age (#61)

**Today:** over 10 years → tablets; 10 and under → liquid. **Age wins** over what the family
currently uses, with a note on the summary. Exactly 10 → liquid ("for now").

**Trade-off**
- A clear age rule is consistent and easy to audit.
- But it can switch a family's form with no clinical reason. A 12-year-old who does well on
  liquid would be shown tablets, and an 8-year-old who swallows tablets would be shown liquid.
- The alternative, "keep the form they're on", avoids needless switches but makes the age rule
  apply only to new starts.

**Questions:** Age or current form, which wins? Should a child switch form at their 11th
birthday? What should we show when the preferred form has no product (no liquid listed, or no
tablet small enough)?

---

## 4. Half tablets (#60)

**Today:** halves only on a **climb step**, never on the final (goal) dose, and never for
extended- or delayed-release products. Exception you asked for: valproic acid 500 mg and
zonisamide 50 mg may be halved on a climb step.

**Trade-off**
- Halves give finer steps and fewer liquid fallbacks.
- But our product list has valproate 500 mg only as a **delayed-release capsule** and zonisamide
  50 mg only as a **capsule**, which are normally swallowed whole. Your earlier rule says ER/DR is
  never split.

**Questions:** Which exact products did you mean? Should we remove the override, or add a 500 mg
valproate tablet? Can halves ever be used on the goal dose when nothing else fits?

---

## 5. What the family message says about the dose (new, #67)

**Today:** when every step converts to tablets, the family draft names the tablets directly:
"give 2 tablets in the morning and 3 tablets at night (250 mg each, 1250 mg/day)". "Please don't go
higher than…" uses the **tablet total**. Liquid cases still give the ladder mg/day plus a "How much
to give" line in mL.

**Trade-off**
- Tablet counts are what the family actually gives, and the message now matches the doctor's
  summary.
- But the ceiling the family sees can be **up to 30% above the ladder dose**, and sometimes above
  the usual effective maximum (never above the absolute maximum). The summary flags that case for
  you; the family message does not.
- Each step now gives both the tablet count and the mg/day total, so the message is longer.

**Questions:** Should the family see the mg/day total at all, or only tablet counts? Is the tablet
total the right "don't go higher than" number? Please review the wording.

---

## 6. Side effects at Level 3: wait vs uneven dosing vs three times a day (new, #68 / #52)

**Today:** at Level 3 the family can pick one option, but only from what the tool would suggest:
- **Uneven AM/PM** (within 30/70 to 40/60) is offered only for clobazam, valproic acid,
  carbamazepine and oxcarbazepine.
- **Three times a day** is offered for other medicines, only at steady state or with breakthrough
  seizures (your 2026-07-16 confirmation).
- **Wait** is always offered; follow-up is in 4 weeks.

The answer goes to your summary only. It never changes the decision or the total daily dose.

**Trade-off**
- Your tablet notes say "allow uneven dosing for all medications", but the decision table limits
  it to 4 drugs. Allowing it everywhere gives families more options, but some drugs may not
  benefit from an uneven split.
- Letting the family choose increases buy-in, but they may pick an option you wouldn't. Showing
  you the choice and letting you decide keeps control with you, at the cost of an extra
  back-and-forth.

**Questions:** Does the family choose, or do you choose and they only see the result? Is uneven
dosing for all medicines or only the 4? Should TID be offered while titrating or holding? Is 4
weeks the right follow-up for "wait"? Please edit the draft wording.

---

## 7. Removing the preference questions (new, #68)

**Today:** the survey no longer asks "Do you have a preference about the medicine?", the
increase / add / switch ranking, how strongly they feel, or the at-goal "continue increasing vs add
another medication" question. Families can still write a preference in the free-text box. That
text reaches you but never affects the decision.

**Trade-off**
- Fewer questions, and no implied promise that the family's choice drives treatment.
- But three rules no longer fire from the survey: **R7** (breakthrough seizures, family wants a
  slower increase → slow down), **R16** (seizure-free and still titrating, family wants slower →
  slow down) and **R15** (family wants a different medicine → refer to you with switch options).
  A family who would have ticked "try a different medicine" now gets the standard recommendation
  unless you read their free text.
- When the at-goal case needs it (rule R12g), the tool still asks the family "keep increasing or
  add a second medicine?" in its draft message.

**Questions:** Is it OK that family preference no longer changes the recommendation? Or should
R7/R15/R16 come back in some form, e.g. a single yes/no "Would you like to talk about other options?"

---

## 8. When to show the "medicine options" questions

**Today:** the questions in the survey (other medicines tried, ancestry, health conditions) show
only when a switch or add is **likely**: at the goal dose with ongoing seizures, or side effects at
Level 3 or higher. The actual medicine suggestions appear **after** the survey only when the tool's
decision is a non-urgent switch or add.

**Trade-off**
- The study team asked to show these questions "only if in the decision table". To do that
  exactly, the survey would have to work out the decision before the family finishes. That means
  copying the decision tables into the survey page (two copies that can drift apart), or asking
  these questions on a second page after submission (one more step for the family).
- Today's rule of thumb sometimes shows the questions when the decision won't be switch/add, and
  vice versa.

**Questions:** Is the rule of thumb acceptable, or should these questions move to a second page
that appears only when the decision is switch/add?

---

## 9. Breakthrough seizures with missing detail

**Today:** if the family gives how **often** seizures happen but not how **long**, the tool
**assumes urgent** (the middle tier) and follows the urgent plan for that phase (your F1 decision,
2026-07-16). With neither frequency nor duration, it defers to you.

**Trade-off**
- Assuming urgent avoids under-treating a possibly long seizure.
- But it may raise the dose for short seizures that would have been non-urgent. Asking the family
  again delays care.

**Question:** Still comfortable with "assume urgent"? Should the family draft ask them to report
seizure length next time?

---

## 10. Reaching the maximum dose

**Today:** the **absolute maximum** is never exceeded; ladder steps above it are dropped (only
the steps that fit are shown). Going above the **usual effective maximum** is allowed, with a flag
for you. Once the child is **at** the maximum with frequent or long breakthrough seizures, the tool
recommends **adding a second medicine** if the current one has been helping (R9-add), and
**switching** if it hasn't (R9-switch).

**Trade-off**
- This is strict on safety. But a child just below the absolute max may get one small step, and
  only reaches the add/switch decision at the next check-in, about 4 weeks later.
- "Helping" comes from the family's answer to "Overall, how have seizures changed?" (better / about
  the same / worse). "About the same" counts as not helping, which leads to a switch.

**Questions:** When only one small step remains, should the tool take it, or go straight to adding a
second medicine? Should "about the same" lead to add rather than switch?

---

## 11. Side-effect level mapping

**Today:** Level 4 ("big impact") is treated as **manageable**, which lowers the dose. Only Level
5 ("not a fit") is treated as **intolerable**, which switches the medicine (your no-seizures table).

**Trade-off:** families who choose Level 4 may expect a change of medicine and instead get a lower
dose. Lowering first keeps a medicine that is working for seizures.

**Question:** Is lower-then-reassess right for Level 4, or should Level 4 with ongoing seizures
lead to a switch?

---

## 12. Defer to you vs recommend

**Today:** the tool recommends whenever a rule clearly applies and defers only when inputs are
missing or conflicting. Example where we disagree with your gold label (case **J2**): a child on
levetiracetam at goal, seizure-free, with intolerable behavioral side effects. The tool says
**switch** (rule R14). Your label was **needs more information**.

**Trade-off:** recommending more often saves your time on clear cases, while deferring more often
is safer when context matters (e.g. behavioral side effects may have other causes).

**Question:** For behavioral side effects specifically, should the tool defer to you instead of
recommending a switch?

---

## 13. Safety flag: draft vs withhold the family message (#51)

**Today:** on a safety flag (e.g. rash with blisters), the tool writes a short **"stop and contact
the clinic"** draft. You approve it before anything is sent.

**Trade-off:** a ready draft lets you respond faster. Withholding the message entirely makes it
impossible for anything automatic to reach the family. Either way nothing is sent without you.

**Question:** Draft or withhold? (Our fuzz test still counts the draft as a "leak" until you
decide.)

---

## 14. Switching: stopping the old medicine (#69)

**Today:** when the decision is a switch, the doctor's view shows your week-by-week starting
schedule for the new medicine. It does **not** yet show how to stop the old one, because your two
documents differ:

| | Template F table (kb edits) | Your email, 2026-10-23 |
|---|---|---|
| Old medicine | week 1 current dose, then ×0.75, ×0.50, ×0.25 "**and continue**" | decrease "until **week 4 off**" |
| New medicine | starting → increasing → **goal dose by week 4** | follow its own starting tab (3 to 14 weeks) |

**Questions:**
1. Is the old medicine off at week 4, or does it stay at ×0.25 until you say stop?
2. Are the weekly steps ×0.75 / ×0.50 / ×0.25 of the current dose?
3. Does the new medicine start in week 1 at the same time (cross-taper)? Most starting tabs take
   longer than 4 weeks to reach the goal dose (lamotrigine 8 to 14 weeks). Should the old medicine
   stay at a low dose until the new one reaches its goal?

---

## 15. Questions from final_doses_titration_v9 (#70)

v9 is now loaded: the dose ladder for every medicine and your 10 starting-schedule tabs. Things to
confirm:

- **Carbamazepine has no starting-schedule tab.** The tool says "no starting schedule yet".
- **Levetiracetam tablets:** your starting tabs use **750 mg** tablets from 40 kg. We follow your tab
  for starting a new medicine, but dose increases use the **500 mg** standard (#66). Should both use
  the same tablet?
- **Product lists differ from ours.** Valproate **125 / 250 / 500 mg plain tablets** (our list has
  capsules and delayed-release capsules only; this matters for the half-tablet question in #60).
  Lamotrigine **50 / 100 mg chewables** (our list has 2 / 5 / 25 mg). Topiramate **15 mg** tablets.
- **Lamotrigine: weight band or age band?** A 12–16-year-old under 45 kg matches both a weight band
  and the "12–16 y" row. A child who is exactly 16 fits neither "12–16 y" nor "> 16 y". The tool
  hands these to the doctor.
- **Topiramate:** no band between 11 and 12 kg ("≤ 11 kg", then "12–22 kg").
- **Levetiracetam tablets 17.5–20 kg** have no schedule ("—"), while 15–17.5 and 20–22.5 kg do.
  Intended?
- **Zonisamide (children):** the step from 3.5 to 5 mg/kg/day is 1.5, larger than the sheet's
  maximum increase of 0.7 mg/kg/day (new in v9).
- **Brivaracetam ≥ 50 kg:** starting dose 100 mg/day is above the minimum maintenance dose of
  50 mg/day (also in v6).
- **Bigger changes from v6:** lamotrigine, topiramate and zonisamide now increase **weekly**
  (were every 2 weeks); most "usual effective max" values went down; clobazam now uses weight
  groups (< 20, 20–30, 30–50, ≥ 50 kg) instead of age; oxcarbazepine was revised (this answers
  #64) and its 60–70 and ≥ 70 kg bands merged into ≥ 60 kg. Please confirm these were intended.

---

## 16. Data we still need from you

- How to stop the old medicine when switching (section 14).
- Titration schedules from the **starting dose to the minimum therapeutic dose** for children
  already on a medicine but below its therapeutic range (#63). Your v9 starting tabs cover new
  medicines; tell us if they should also be used for this.
