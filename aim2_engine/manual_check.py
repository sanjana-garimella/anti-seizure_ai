"""
manual_check.py
---------------
One-command confirmation of the 2026-06-25/26 changes. Runs each scenario
through the real engine and prints PASS/FAIL against the expected outcome.

Run:  cd aim2_engine && python3 manual_check.py

This complements the click-through steps in docs/MANUAL_TEST_PLAN.md. It is a
convenience check, not part of the test suite. All cases use SIMULATED inputs.
"""
import clinic
from engine_core import (run_engine, snapshot_from_interval, PatientContext,
                         MedicationContext, PhysicianContingency)
from engine_core.asm_rules import load_asm_rules

REG = load_asm_rules("data/asm_rules.json")
PASS = "PASS"; FAIL = "FAIL"
results = []


def engine(impact=None, goal="still_increasing", on_dose=None, med="valproic_acid",
           seizures="no", extra=None):
    ans = {"Q3": seizures, "Q10a": goal, "Q11": "better"}
    if impact:
        ans["Q9"] = ["sleepiness"]; ans["Q10b_impact"] = impact
    if on_dose:
        ans["Q10c_time"] = on_dose
    if extra:
        ans.update(extra)
    snap = snapshot_from_interval(
        PatientContext(external_id="t", age_years=8, weight_kg=25),
        MedicationContext(medication=med, dose_amount=250, dose_unit="mg", frequency_per_day=2),
        PhysicianContingency(can_switch_to="lamotrigine"), ans)
    return run_engine(snap, asm_registry=REG)


def check(name, got, want):
    ok = PASS if got == want else FAIL
    results.append((ok, name, f"got={got!r} want={want!r}"))


# --- No-Seizures 6-level table x phase --------------------------------------
check("S1  no SZ / L0 / titrating -> no change", engine("no_impact").category, "no_change")
check("S2  no SZ / L2 / titrating -> slower", engine("medium_tolerable").category, "slower_uptitration")
check("S3  no SZ / L2 / steady -> no change", engine("medium_tolerable", "at_goal", "over_2_weeks").category, "no_change")
r4 = engine("medium_alot", "at_goal", "under_2_weeks")   # Holding
check("S4  no SZ / L3 / HOLDING -> hold", r4.category, "hold")
check("S4b HOLDING L3 follow-up = 4 weeks (updated sheet)", r4.followup_weeks, 4)
check("S4c HOLDING L3 shows 'getting better?' prompt", bool(r4.family_prompt), True)
r5 = engine("medium_alot", "at_goal", "over_2_weeks")    # Steady
check("S5  no SZ / L3 / STEADY follow-up = 4 weeks", r5.followup_weeks, 4)
check("S5b STEADY L3 has NO adjustment prompt", bool(r5.family_prompt), False)
r6 = engine("big")
check("S6  no SZ / L4 -> lower the dose", r6.category, "lower_dose")
check("S6b L4 orders labs (valproic acid)", bool(r6.lab_orders), True)
check("S7  no SZ / L5 -> switch", engine("not_a_fit").category, "switch_medication")

# --- Labs / uneven dosing by drug -------------------------------------------
check("S8  L3 hold on levetiracetam -> NO labs",
      engine("medium_alot", "at_goal", "under_2_weeks", med="levetiracetam").lab_orders, [])
check("S8b L3 hold on valproic acid -> uneven-dosing note",
      bool(engine("medium_alot", "at_goal", "under_2_weeks").dosing_note), True)

# --- Breakthrough triggers (adherence-first on missed dose) -----------------
bt = engine(goal="still_increasing", seizures="yes", extra={
    "Q4": ["whole_body"],
    "seizure_episodes": [{"look": ["whole_body"], "duration": "1_3min", "frequency_bucket": "weekly"}],
    "seizure_triggers": ["missed_dose", "lack_of_sleep"], "seizure_triggers_all": "yes", "Q11": "about_same"})
check("S9  breakthrough + missed-dose trigger -> no change (adherence-first)", bt.category, "no_change")
check("S9b triggers shown in clinician summary", "trigger" in bt.clinical_summary.lower(), True)

# --- Breakthrough decision tables (new 6-level scale, level >= 2) ------------
def bt(typ, dur, freq, impact, goal="still_increasing"):
    ans = {"Q3": "yes", "Q4": [typ],
           "seizure_episodes": [{"look": [typ], "duration": dur, "frequency_bucket": freq}],
           "Q9": ["sleepiness"], "Q10b_impact": impact, "Q10a": goal, "Q11": "about_same"}
    snap = snapshot_from_interval(
        PatientContext(external_id="t", age_years=8, weight_kg=25),
        MedicationContext(medication="valproic_acid", dose_amount=250, dose_unit="mg", frequency_per_day=2),
        PhysicianContingency(can_switch_to="lamotrigine"), ans)
    return run_engine(snap, asm_registry=REG)

check("S15 BT non-urgent / titrating / L2 -> no change", bt("focal", "under_1min", "monthly", "medium_tolerable").category, "no_change")
r_u = bt("focal", "1_3min", "weekly", "medium_alot")
check("S16 BT urgent / titrating / L3 -> escalate", r_u.category, "escalate")
check("S16b urgent tier classified", r_u.urgency_tier, "urgent")
r_e = bt("focal", "over_5min", "weekly", "big")
check("S17 BT emergent / titrating / L4 -> add medication", r_e.category, "add_medication")
check("S17b emergent tier classified", r_e.urgency_tier, "emergent")
check("S17c breakthrough table surfaces step guidance", bool(r_e.guidance_steps), True)
r_e5 = bt("focal", "over_5min", "weekly", "not_a_fit")
check("S18 BT emergent / L5 -> switch + urgent safety", (r_e5.category, r_e5.safety_concern_flag), ("switch_medication", True))
# S19 asserted urgency_tier is None here pre-2026-08-05; api.run_engine now sets
# decision.urgency_tier or status.breakthrough_tier for every case
# ("Recommendation.urgency_tier fixed at the source"), so a legacy-routed case
# correctly carries the classifier's tier instead of going through un-tagged.
check("S19 BT legacy 'small' impact still tiers from the classifier", bt("focal", "1_3min", "weekly", "small").urgency_tier, "urgent")

# --- PRECISE per-seizure-type urgency thresholds (exact seconds + frequency) --
def tier(typ, dur, freq="monthly", count=None):
    ep = {"look": [typ], "duration": dur, "frequency_bucket": freq}
    if count is not None:
        ep["count_4wk"] = count
    ans = {"Q3": "yes", "Q4": [typ], "seizure_episodes": [ep],
           "Q9": ["sleepiness"], "Q10b_impact": "medium_alot", "Q10a": "still_increasing", "Q11": "about_same"}
    snap = snapshot_from_interval(
        PatientContext(external_id="t", age_years=8, weight_kg=25),
        MedicationContext(medication="valproic_acid", dose_amount=250, dose_unit="mg", frequency_per_day=2),
        PhysicianContingency(can_switch_to="lamotrigine"), ans)
    return run_engine(snap, asm_registry=REG).urgency_tier

check("S20 myoclonic >=20s -> urgent", tier("sudden_jerks", "20_30s"), "urgent")
check("S21 myoclonic <20s -> non-urgent", tier("sudden_jerks", "under_20s"), "non_urgent")
check("S22 absence >=90s -> urgent", tier("brief_staring", "90s_3min"), "urgent")
check("S23 absence 30-90s -> non-urgent", tier("brief_staring", "30_90s"), "non_urgent")
check("S24 absence ~43/day (count) -> emergent", tier("brief_staring", "under_20s", count=1200), "emergent")
check("S25 focal >=3 min -> urgent", tier("focal", "3_5min"), "urgent")
check("S26 focal >=5 min -> emergent", tier("focal", "over_5min"), "emergent")
check("S27 focal >=1/day (daily) -> emergent", tier("focal", "90s_3min", "daily"), "emergent")

# --- Safety regression -------------------------------------------------------
rash = engine(impact="big", extra={"Q9": ["rash"], "Q9a": "blisters"})
check("S10 blistering rash -> safety flag raised", rash.safety_concern_flag, True)
check("S10b blistering rash -> family message withheld", rash.draft_patient_message, None)

# --- Parsing of new survey fields (web form) --------------------------------
a = clinic.parse_answers({
    "child_age": ["7"], "child_weight_lb": ["48"],
    "seizures": ["yes"], "q4": ["acting_off"], "dur_acting_off": ["under_1min"],
    "freq_acting_off": ["weekly"], "unresp_acting_off": ["yes"],
    "q_triggers": ["lack_of_sleep", "fever_illness"], "q_triggers_all": ["no"],
    "med_pref_rank1": ["add"], "med_pref_rank2": ["switch"], "med_pref_strength": ["very_strongly"],
    "q11": ["worse"]})
check("S11 weight 48 lb -> 21.8 kg", a["child_weight_kg"], 21.8)
check("S12 'acting off' seizure type parsed", a.get("seizure_episodes", [{}])[0].get("look"), ["acting_off"])
check("S13 triggers parsed", a.get("seizure_triggers"), ["lack_of_sleep", "fever_illness"])
# Preference questions removed from the survey (2026-10-15) and confirmed by Dr. Barbour
# 2026-10-25 (#68): stale fields from an old form are ignored.
check("S14 removed med-preference ranking is ignored", a.get("med_pref_rank"), None)

# --- Print report -----------------------------------------------------------
print("\n  MANUAL CONFIRMATION — engine scenarios\n  " + "=" * 56)
npass = sum(1 for r in results if r[0] == PASS)
for ok, name, detail in results:
    mark = "[x]" if ok == PASS else "[ ]"
    line = f"  {mark} {ok}  {name}"
    if ok == FAIL:
        line += f"   ({detail})"
    print(line)
print("  " + "=" * 56)
print(f"  {npass}/{len(results)} checks passed"
      + ("  — ALL GOOD" if npass == len(results) else "  — SEE FAILURES ABOVE"))
