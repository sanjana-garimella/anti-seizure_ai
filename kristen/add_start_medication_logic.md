# Adding or starting a medication: how the tool decides (for discussion with Dr. Barbour)

DRAFT, 2026-10-28. Synthetic data only. This describes what the tool does today when the plan is
to **add a second medicine** or **start a new one**, and where I need your call. Nothing here
changes a dose decision until you sign off, and every output stays a draft for physician review.

Each section says **what the tool does today**, **where it comes from**, and the **question for
you**. Sign-off numbers refer to `aim2_engine/NEEDS_PI_SIGNOFF.md`.

---

## 0. Fixed 2026-10-28: the current medicine now counts when a new one starts (safety)

**Before.** Your lamotrigine starting tab only applies to children *not* on valproate or an enzyme
inducer. The tool checked this against the child's *other* listed medicines, but not the
**current** one. So for a child on valproate, adding lamotrigine (or switching to it) showed the
standard lamotrigine schedule instead of "clinician to set the starting doses".

**Now.** The current medicine counts as a co-medication for the new medicine's starting schedule,
on both paths:
- **Add:** the current medicine stays on.
- **Switch:** the current medicine overlaps the new one through the 4-week taper (new medicine
  starts in week 1).

This applies in the doctor's summary and on the review page, and it matches brand names too
(e.g. "Depakote"). For lamotrigine on valproate or carbamazepine the tool now shows no schedule
and says the standard tab doesn't apply.

**Question for you.** On a switch, should lamotrigine wait until valproate is fully stopped
(after week 4) and then use the standard tab? Or should it start in week 1 on the slower
with-valproate regimen, as the tool now assumes?

---

## 1. The overall flow

```
check-in (page 1)
   │
   ▼
rule-based decision ──► category = add_medication?  (sections 2-3)
   │                         │
   │                         ▼
   │                  safety flag? ── yes ──► no family message, no options page;
   │                         │                you handle it per the urgent protocol
   │                         no
   │                         ▼
   │                  page 2: "medicine options" questions  (section 4)
   │                         ▼
   │                  rule-based ranking -> top 3 options    (section 5)
   │                         ▼
   │                  family marks 1st / 2nd / 3rd / avoid
   ▼
your review page: decision, options, family's ranking, and the starting
schedule for each option (section 6). You pick the medicine and the doses.
```

The tool never picks the medicine and never sets the starting dose on its own. It decides only
*that* a second medicine should be discussed, then narrows the list and shows your schedule.

---

## 2. When the tool says "add a second medicine"

| Path | When | Source |
|---|---|---|
| **R5b** | The medicine is helping, seizures continue, and side effects prevent going higher | your 2026-06 rule |
| **R9-add** | Frequent or long breakthrough seizures, **already at the maximum dose**, and the family says the medicine is helping | your 2026-06 rule |
| **Breakthrough table, Level 4** | Big-impact side effects plus breakthrough seizures (non-urgent, urgent or emergent; every phase): **lower the dose and add** | your 2026-06-25 tables |
| **Breakthrough table, at max** | Urgent or emergent seizures, holding or steady phase, Level 0-3, when the planned increases would go past the max: **add** | your 2026-06-25 tables |
| **Level 3, family option 4** | Urgent or emergent seizures with Level 3 side effects, and the family picks "lower the dose and add a second medication" | your template H (#52) |
| **R12g** (a question, not a decision) | At the goal dose, seizures continue, side effects noticeable but manageable: the draft asks the family "keep increasing, or add?" | your 2026-06 wording |

If it's *not* helping at the max (R9-switch), or side effects are intolerable (R6), the tool says
**switch**, not add.

**Questions for you**
1. R9 tells "helping" apart from "not helping" using the family's overall answer (Q11 "better").
   Is that enough, or should seizure frequency vs the last visit also count?
2. Non-urgent breakthrough + Level 4 currently says "lower the dose and add". Is that right for
   non-urgent seizures, or would you rather lower and wait first?
3. Your emergent steady-phase cells say "keep the dose the same **or** add". The tool picks *add*
   whenever the max is reached. Should it instead offer both to the family, as at Level 3?

---

## 3. "Lower the dose and add"

**Today.** The family draft says "let's lower the [medicine] dose to **[lower dose - physician to
confirm]** and add a second medicine alongside it". The tool does not compute the lower dose,
because the ladder only resolves upward (#50). For emergent Level 4 it follows your two-step text
("week 1 make 1 dose decrease; after week 1 a second decrease") in the doctor's summary, without
numbers.

**Questions for you**
4. Should the lower target be **one ladder step down** (two for emergent L4), so the draft can name
   it? Or do you always want to set it yourself?
5. Should the new medicine start **at the same time** as the lowering, or after the lower dose has
   settled (e.g. week 2)?

---

## 4. The "medicine options" questions (page 2)

**Today.** Page 2 appears only when the decision is switch or add **and** there is no safety flag.
It reuses age, sex, seizure types, adherence and other medicines from page 1, then asks:

- medicines already tried (the current medicine is hidden and always counted as tried)
- avoid medicines that need blood tests?
- mood difficulties, behavior difficulties, trouble sleeping, migraines
- Southeast Asian or Han Chinese ancestry
- health conditions: kidney disease, kidney stones, liver disease, diabetes, obesity, heart
  problems, bleeding disorder, sulfa allergy, prior chemotherapy

The family can skip page 2. Then you see the decision without options.

**Questions for you**
6. Are these the right questions for an **add**, or does adding need extra ones (e.g. "would a
   second medicine twice a day be hard to manage?")?
7. Should page 2 also show for R12g (the "keep increasing or add?" question), so the options are
   ready if the family picks add?

---

## 5. Which medicine: the ranking

**Today** (your workbook *Switching to a different medication.xlsx*, `verified: true`). Nine
medicines in your base order: lamotrigine, brivaracetam, lacosamide, oxcarbazepine, clobazam,
levetiracetam, zonisamide, valproic acid, topiramate.

1. **Avoid rules** remove a medicine (e.g. female over 10 → no valproate or topiramate; kidney
   disease → no topiramate, zonisamide, levetiracetam, brivaracetam; already tried → removed).
2. **Favor rules** move a medicine up (e.g. mood difficulties → lamotrigine, oxcarbazepine;
   migraines → topiramate, valproate, zonisamide).
3. The rest are sorted by number of favor rules, then your base order. The **top 3** are shown.
4. If fewer than 3 remain, the empty slots are left for you (no guessing).

The same ranking is used for a switch and for an add. Carbamazepine is never offered as a new
medicine (your 2026-10-25 answer). Ethosuximide has a starting tab but is not in the ranking list.

**Questions for you**
8. **Should an add use different rules from a switch?** Today nothing looks at how the new
   medicine pairs with the current one. Examples I'd want your call on:
   - levetiracetam + brivaracetam (same mechanism) - avoid as an add?
   - lamotrigine + valproate (interaction; lamotrigine needs the lower schedule and valproate can
     raise lamotrigine levels) - allow with the special schedule, or avoid?
   - oxcarbazepine + lacosamide (both sodium-channel; more dizziness) - avoid or warn?
   - clobazam as an add-on - favor it, since it's often used that way?
9. Should **ethosuximide** be in the list for children with absence (staring) seizures only?
10. Your rule "treatment-naive, no mood or behavior issues → avoid brivaracetam" never fires on an
    add (the child is on a medicine). Is that what you intended?

---

## 6. Starting the new medicine

**Today.** Each option on your review page has a "Starting schedule" with your v9 initiation tab
for the child's age/weight band, cells verbatim (liquid and tablet, the family's current form
marked "preferred"). The tool does not round or change your cells. It shows no schedule, with a
reason, when:

- the child's weight and age are both missing, or no single band fits;
- the medicine has no tab yet; or
- the tab doesn't apply (lamotrigine with valproate or an enzyme inducer, including the
  child's current medicine - section 0).

On a **switch**, the doctor's summary also shows the stop plan for the old medicine (week 1
current dose, then ×0.75, ×0.50, ×0.25, off after week 4; #69). On an **add**, the old medicine
stays where it is: the family draft says "please keep giving [medicine] as prescribed for now".

**Gaps**
- On an add, the doctor's **summary** (the EHR-ready note) shows no starting schedule. Only the
  review page does, per option, after the family completes page 2.
- The family draft for an add names no medicine and no doses ("your team will go over which
  medicine and the exact doses with you"), as you asked in the kb edits.

**Questions for you**
11. After you choose the medicine, should the tool draft a **second family message** with your
    starting schedule in tablets or mL (the same way as increase steps, #67)?
12. Are your initiation tabs right for an **add-on**, or should an add-on start lower or slower than
    a first medicine (beyond lamotrigine)?
13. While the new medicine is climbing, should the **current medicine be held** at its dose? Today
    the draft says "keep giving it as prescribed", which could include an increase plan already in
    progress.
14. Follow-up after an add is 4 weeks (your tables). Should it be timed from the start of the new
    medicine or from when it reaches its target?

---

## 7. Starting a first medicine (not in the tool yet)

**Today.** The tool handles between-visit check-ins for a child who is **already on** a medicine.
It has no path for a child who is starting their **first** seizure medicine. Your initiation tabs
would cover the dosing, and the ranking (with the treatment-naive brivaracetam rule) would cover
the choice.

**Questions for you**
15. Do you want a "first medicine" flow in this tool, or is that always decided in clinic?
16. If yes: should the family get a check-in during the first weeks of the starting schedule
    (e.g. week 2 for rash on lamotrigine), and should those check-ins use the same decision tables
    with phase = "titrating"?

---

## 8. What I need from you, in order

1. Switch timing for lamotrigine after valproate (section 0).
2. Add-specific pairing rules (Q8).
3. Lower-dose target for "lower and add" (Q4, Q5).
4. Whether to draft a starting-schedule message for the family (Q11) and hold the current dose
   (Q13).
5. Whether to build the first-medicine flow (Q15, Q16).
