# How the tool decides — trade-offs (Dr. Barbour)

DRAFT, 2026-10-28 · synthetic data only · drafts until you sign off.  
Sign-off: `aim2_engine/NEEDS_PI_SIGNOFF.md`. Add/switch/start: `add_start_medication_logic.md`.

---

## 1. Ladder to tablets or liquid (#1, #2, #4)

**Today:** Total daily dose within **-10% / +30%** of ladder; never above absolute max. Pick closest total (tie up), prefer whole/half/even tablets, max **4 tablets/dose**.

**Trade-off:** Wide band -> plan almost always exists but can sit well above ladder step; narrow band -> more liquid/defer. Four-tablet cap is usability, not clinical.

**Ask:** Keep -10%/+30% for goal vs climb? Four tablets always?

---

## 2. One levetiracetam strength (#66)

**Today:** Keep family's tablet if it fits all steps as well as **500 mg**; else lock to 500 mg. Other meds still pick best single strength.

**Trade-off:** Simpler Rx; some steps only 250 mg could hit -> liquid. New strength -> new Rx and mix-up risk (draft warns family).

**Ask:** 500 mg standard correct? Same rule for lamotrigine 100 mg, oxcarbazepine 300 mg? If standard fails: liquid, skip step, or one-off strength?

---

## 3. Tablets vs liquid by age (#61)

**Today:** Over 10 -> tablets; 10 and under -> liquid (age beats current form). Age 10 -> liquid.

**Trade-off:** Auditable rule vs forced form changes (12 yo on liquid, 8 yo on tablets).

**Ask:** Age or current form wins? Switch at 11th birthday? When preferred form has no product?

---

## 4. Half tablets (#60)

**Today:** Halves on **climb steps only**, not goal dose, not ER/DR. Exception: valproic acid 500 mg and zonisamide 50 mg on climb (your ask).

**Trade-off:** Our list has valproate 500 mg as DR capsule and zonisamide 50 mg capsule — normally not split.

**Ask:** Which products did you mean? Add 500 mg valproate tablet? Halves on goal if nothing else fits?

---

## 5. Family message wording (#67)

**Today:** If all steps are tablets, draft gives counts + mg/day; ceiling uses **tablet total**. Liquid: ladder mg/day + mL line.

**Trade-off:** Matches what they give; ceiling can be up to +30% above ladder (flagged for you, not in family text). Longer messages.

**Ask:** Tablet counts only, or mg/day too? Right "don't go higher than" number?

---

## 6. Level 3: wait / uneven / TID (#52, #68)

**Today:** Family picks one offered option (summary only, does not change decision). Uneven AM/PM only clobazam, valproic acid, carbamazepine, oxcarbazepine. TID other meds at steady or with breakthrough. Wait always; 4-week follow-up.

**Trade-off:** Your notes say uneven for all meds; table limits to four. Family choice vs you choose only.

**Ask:** Family or you choose? Uneven for all meds? TID while titrating/holding? 4 weeks for wait?

---

## 7. Removed preference questions (#68)

**Today:** No survey Q12 increase/add/switch rank or at-goal add question. Free text only (does not change engine). R12g still asks increase vs add in **draft** when rule fires.

**Trade-off:** Shorter survey; R7, R15, R16 no longer fire from ticks.

**Ask:** OK that preference doesn't change recommendation? One yes/no "talk about other options?"

---

## 8. Medicine options page 2

**Today:** After page 1, only if **switch/add** and no safety flag -> preference questions, top 3, family rank (skippable). Not on page 1.

**Trade-off:** Second step when add/switch; R12g does not open page 2 unless category is already add.

**Ask:** Page 2 for R12g? (Add/switch detail: `add_start_medication_logic.md`.)

---

## 9. Breakthrough, missing duration (F1)

**Today:** Frequency without duration -> **assume urgent** tier. Neither -> defer.

**Trade-off:** Avoid under-treating long seizures; may escalate short ones.

**Ask:** Keep assume urgent? Ask family to report length next time?

---

## 10. At maximum dose

**Today:** Never above absolute max. Above usual effective max allowed with flag. At max + frequent/long breakthrough: **add** if Q11 better, **switch** if not.

**Trade-off:** One step below max may delay add/switch ~4 weeks. "About the same" -> switch not add.

**Ask:** Take last small step or jump to add? "About the same" -> add?

---

## 11. Side-effect levels (no-seizures path)

**Today:** L4 big impact -> **lower** (manageable). L5 not a fit -> **switch** (intolerable).

**Trade-off:** L4 families may expect switch; you get lower-first.

**Ask:** L4 + ongoing seizures: lower or switch?

---

## 12. Recommend vs defer (e.g. J2)

**Today:** Recommend when rule clear; defer on missing/conflict. Example J2: behavioral intolerable at goal, seizure-free -> tool **switch** (R14); gold was defer.

**Trade-off:** Speed vs safety when context matters.

**Ask:** Behavioral intolerable -> defer instead of switch?

---

## 13. Safety flag (#51)

**Today:** Safety flag -> short stop/call clinic **draft**; you approve before send.

**Trade-off:** Draft speeds response; withhold = nothing prepared for family.

**Ask:** Draft or withhold?

---

## 14. Switch taper and starts (#69, #70)

**Today:** Switch taper DRAFT: old med full w1, x0.75, x0.5, x0.25, off w4; new med w1. Initiation tabs verbatim when band applies. **Current med counts** for tab checks (e.g. lamotrigine + valproate -> defer). Summary schedule+taper only if **switch target** named; else review card (see add/start doc).

**Trade-off:** Taper ends w4; tabs often longer. Lamotrigine/VPA start timing open.

**Ask:** Off w4 vs x0.25 until you stop? Lamotrigine w1 vs after valproate off? Old med low until new at goal?

---

## 15. v9 sheet checks (#70)

Confirm when you can:

- Carbamazepine: no initiation tab in v9.
- Levetiracetam: starting tab uses 750 mg from 40 kg; titration uses 500 mg (#66).
- Product list gaps: valproate plain tablets, lamotrigine chewables, topiramate 15 mg.
- Lamotrigine: weight vs age band overlap; age exactly 16.
- Topiramate: gap 11-12 kg; levetiracetam 17.5-20 kg blank row.
- Zonisamide child step 3.5->5 mg/kg vs max increase 0.7; brivaracetam >=50 kg start above min maintenance.
- v6->v9: weekly steps (LTG, TPM, ZNS), lower usual maxes, clobazam by weight, oxcarbazepine bands merged.

---

## 16. Still need (#63 and add/switch)

- Below therapeutic range on current med: use v9 start tabs or separate schedule? (#63)
- Add pairing / page-2 scope: `add_start_medication_logic.md`

**Meeting priority:** formulation window · LEV 500 mg · age vs form · half tablets · v9 band/product gaps · taper timing · safety draft vs withhold
