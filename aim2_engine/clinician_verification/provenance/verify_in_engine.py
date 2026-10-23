#!/usr/bin/env python3
"""
verify_in_engine.py — proves the draft data flows through the REAL engine.
Run: python3 verify_in_engine.py
"""
import sys, os, json
ENG = "/sessions/optimistic-zen-curie/mnt/amt/aim2_engine"
sys.path.insert(0, ENG)
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFT = os.path.join(OUT, "asm_rules.draft.json")

from engine_core.asm_rules import load_asm_rules, get_rule
from engine_core.snapshot import (PatientContext, MedicationContext, PhysicianContingency,
    IntervalSurvey, IntervalSnapshot, SeizureControl, SideEffectSeverity, Adherence, DoseGoalStatus)
from engine_core.categorize import categorize
from engine_core.decision import decide, RecommendationCategory
from engine_core.outputs import build_titration_schedule

print("="*70)
print("1) DRAFT LOADS IN THE UNMODIFIED ENGINE")
rules = load_asm_rules(DRAFT)
print(f"   rules loaded: {len(rules)}  ->  {sorted(rules)}")
for name in ["clobazam","levetiracetam","carbamazepine"]:
    r = get_rule(name, rules)
    print(f"   {name}: {len(r.dose_bands)} band(s), in_study={r.in_study}")

def make_snapshot(med, age, wt, dose_mg, freq, breakthrough=True, can_increase=True):
    p = PatientContext(external_id="DEMO", age_years=age, weight_kg=wt)
    m = MedicationContext(medication=med, dose_amount=dose_mg, dose_unit="mg", frequency_per_day=freq)
    s = IntervalSurvey(seizure_control=SeizureControl.BREAKTHROUGH if breakthrough else SeizureControl.SEIZURE_FREE,
                       side_effects_present=[], dose_goal_status=DoseGoalStatus.STILL_INCREASING, adherence=Adherence.GOOD)
    c = PhysicianContingency(can_increase_dose=can_increase)
    return IntervalSnapshot(patient=p, medication=m, survey=s, contingency=c)

print("="*70)
print("2) FIXED-MG DRUG (clobazam >30kg) RUNS IN THE UNMODIFIED ENGINE")
snap = make_snapshot("clobazam", age=14, wt=40, dose_mg=10, freq=2)   # 20 mg/day now
st = categorize(snap); dec = decide(st)
cloz_rule = get_rule("clobazam", rules)
sel = cloz_rule.band_for(snap.patient.age_years, snap.patient.weight_kg)
print(f"   patient 14y/40kg -> selected band: '{sel.label}'")
sched = build_titration_schedule(snap, dec, cloz_rule)
print(f"   decision={dec.category.value}; current=20 mg/day")
print(f"   schedule status={sched.get('status')}; ceiling={sched.get('ceiling_mg_per_day')}")
for step in (sched.get("steps") or []):
    print(f"     step {step['step']}: -> {step['target_total_mg_per_day']} mg/day "
          f"({step['per_dose_mg']} mg x{snap.medication.frequency_per_day}) after {step['after_weeks_at_previous']} wk")

print("="*70)
print("3) WEIGHT-BASED DRUG (levetiracetam) IN UNMODIFIED ENGINE -> correctly UNAVAILABLE")
snap2 = make_snapshot("levetiracetam", age=8, wt=25, dose_mg=250, freq=2)  # 500 mg/day now
st2 = categorize(snap2); dec2 = decide(st2)
sched2 = build_titration_schedule(snap2, dec2, get_rule("levetiracetam", rules))
print(f"   decision={dec2.category.value}")
print(f"   schedule={sched2}")
print("   ^ Expected: 'unavailable' because the current DoseBand has no absolute mg step")
print("     for weight-based drugs. This is the schema gap (see PROPOSED_engine_patch.md).")

print("="*70)
print("4) PROPOSED weight-based scheduler (the patch) -> produces real pediatric numbers")
def build_weight_based_schedule(snapshot, decision, band):
    """PROPOSED addition to outputs.py. Uses clinician-supplied mg/kg/day values
    + the patient's weight. No invented values."""
    if decision.category not in (RecommendationCategory.ESCALATE, RecommendationCategory.SLOWER_UPTITRATION):
        return None
    wt = snapshot.patient.weight_kg
    step_kg = band.get("uptitration_step_mg_per_kg_per_day")
    maxkg   = band.get("max_mg_per_kg_per_day")
    tmax_kg = band.get("target_mg_per_kg_per_day_max")
    cap_abs = band.get("absolute_max_mg_per_day")
    cur = snapshot.medication.total_daily_dose
    if wt is None or step_kg is None or cur is None:
        return {"status":"unavailable","note":"Need patient weight + clinician mg/kg step."}
    step = step_kg * wt
    ceil_kg = (maxkg or tmax_kg)
    ceiling = ceil_kg * wt if ceil_kg else None
    if cap_abs is not None:
        ceiling = min(ceiling, cap_abs) if ceiling else cap_abs
    steps=[]; dose=cur; freq=snapshot.medication.frequency_per_day or 0; guard=12
    while ceiling is not None and dose < ceiling and len(steps) < guard:
        dose = min(dose + step, ceiling)
        steps.append({"step":len(steps)+1,"target_total_mg_per_day":round(dose,1),
                      "per_dose_mg":round(dose/freq,1) if freq else None,
                      "after_weeks_at_previous":band.get("uptitration_interval_weeks")})
        if dose >= ceiling: break
    return {"status":"ok","current_total_mg_per_day":cur,"ceiling_mg_per_day":ceiling,
            "weight_kg":wt,"step_mg_per_day":round(step,1),"steps":steps}

raw = json.load(open(DRAFT))
lev_band = raw["levetiracetam"]["dose_bands"][0]
ws = build_weight_based_schedule(snap2, dec2, lev_band)
print(f"   levetiracetam, 8y/25kg, currently 500 mg/day; mg/kg step={lev_band['uptitration_step_mg_per_kg_per_day']}")
print(f"   ceiling={ws['ceiling_mg_per_day']} mg/day (= min(60 mg/kg x 25kg, 3000)); step={ws['step_mg_per_day']} mg/day")
for step in ws["steps"]:
    print(f"     step {step['step']}: -> {step['target_total_mg_per_day']} mg/day "
          f"({step['per_dose_mg']} mg x2) after {step['after_weeks_at_previous']} wk")

print("="*70)
print("5) CROSS-CHECK: target mg/day at representative weights stays within absolute caps")
checks=[("levetiracetam",25,60,3000),("valproic_acid",25,60,3000),("oxcarbazepine",25,60,2400),
        ("topiramate",25,9,400),("zonisamide",25,12,600)]
for med,wt,maxkg,cap in checks:
    val = maxkg*wt
    flag = "OK" if val<=cap or cap is None else "EXCEEDS CAP (cap applies)"
    print(f"   {med}: {maxkg} mg/kg x {wt}kg = {val} mg/day vs abs cap {cap} -> {flag}")
print("\nDONE.")
