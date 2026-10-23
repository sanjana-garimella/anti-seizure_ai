#!/usr/bin/env python3
"""
build_draft.py  —  Aim 2 draft ASM data builder  (PROPOSAL / UNVERIFIED)
========================================================================
Compiles a DRAFT per-drug titration dataset for the 11 in-scope ASMs from
PUBLIC references only:
  * Formulations / strengths  -> RxNorm SCD concepts (rxnav.nlm.nih.gov), pulled 2026-06-17
  * Pediatric dose bands      -> FDA prescribing information / standard pediatric
                                 dosing references (citations in SOURCES), pulled 2026-06-17

HARD RULES (per NON_NEGOTIABLE_RULES.md / spec 11):
  * EVERY clinical value here is an UNVERIFIED DRAFT. Nothing is authoritative.
    Dr. Barbour must verify/correct every value before any clinical use.
  * This file does NOT overwrite the engine's data/asm_rules.json. It writes a
    clearly-named *.draft.json proposal + a clinician verification sheet.
  * Schema is EXTENDED vs engine_core/asm_rules.py DoseBand: pediatric dosing is
    overwhelmingly weight-based (mg/kg/day), which the current absolute-mg-only
    DoseBand cannot represent. See PROPOSED_engine_patch.md.

Run:  python3 build_draft.py
Out:  ../asm_rules.draft.json , ../verification_rows.json , raw_rxnorm_formulations.json
"""
import json, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.dirname(HERE)            # clinician_verification/
PULLED = "2026-06-17"

SOURCES = {
    "rxnorm":      "RxNorm SCD concepts, RxNav REST API (rxnav.nlm.nih.gov/REST/rxcui/<id>/related?tty=SCD), US NLM. Pulled 2026-06-17.",
    "keppra":      "FDA Keppra (levetiracetam) Prescribing Information (accessdata.fda.gov 021035s115/021505s053, 2024); plan-doc draft.",
    "lamictal":    "FDA Lamictal (lamotrigine) Prescribing Information; Drugs.com lamotrigine dosage (pediatric mg/kg titration, valproate 50% reduction).",
    "briviact":    "FDA Briviact (brivaracetam) Prescribing Information (accessdata.fda.gov 205836s017, 2025) - weight-based peds 4-<16y.",
    "vimpat":      "FDA Vimpat (lacosamide) Prescribing Information (accessdata.fda.gov 022253s049, 2021; UCB PI) - weight-based peds.",
    "trileptal":   "FDA Trileptal (oxcarbazepine) Prescribing Information (accessdata.fda.gov 021014s036, 2017).",
    "onfi":        "FDA Onfi (clobazam) Prescribing Information (accessdata.fda.gov 203993s005, 2016) - weight-band (<=30 vs >30 kg).",
    "topamax":     "FDA Topamax (topiramate) Prescribing Information (accessdata.fda.gov 020844s041, 2012); DailyMed.",
    "tegretol":    "FDA Tegretol (carbamazepine) Prescribing Information (accessdata.fda.gov 016608s101, 2009).",
    "depakene":    "FDA Depakene (valproic acid) Prescribing Information (accessdata.fda.gov 018081s065, 2016).",
    "zarontin":    "FDA Zarontin (ethosuximide) Prescribing Information (accessdata.fda.gov 012380s031, 2009) - absence only, >=3y.",
    "zonegran":    "FDA Zonegran (zonisamide) PI (>=16y, adjunctive focal); Zonisade oral susp PI (accessdata 214273, 2022); pediatric use is OFF-LABEL (Epilepsy Foundation; pediatric literature).",
}

# RxNorm SCD formulations actually pulled 2026-06-17 (single-ingredient oral forms relevant to peds epilepsy).
# Combination products (e.g. phentermine/topiramate) and IV/injection forms are excluded as out of scope.
RXNORM = {
 "levetiracetam": {"rxcui":"114477","oral_forms":[
    {"form":"tablet (IR)","strengths_mg":[250,500,750,1000]},
    {"form":"oral solution","mg_per_ml":100},
    {"form":"tablet for oral suspension","strengths_mg":[250,500,750,1000]},
    {"form":"extended-release tablet","strengths_mg":[500,750,1000,1500]}]},
 "lamotrigine": {"rxcui":"28439","oral_forms":[
    {"form":"tablet (IR)","strengths_mg":[25,50,100,150,200,250]},
    {"form":"chewable/dispersible tablet (tab for oral susp)","strengths_mg":[2,5,25]},
    {"form":"orally disintegrating tablet (ODT)","strengths_mg":[25,50,100,200]},
    {"form":"oral suspension","mg_per_ml":10},
    {"form":"extended-release tablet (>=13y)","strengths_mg":[25,50,100,200,250,300]}]},
 "topiramate": {"rxcui":"38404","oral_forms":[
    {"form":"tablet (IR)","strengths_mg":[25,50,100,200]},
    {"form":"sprinkle capsule","strengths_mg":[15,25]},
    {"form":"capsule","strengths_mg":[50]},
    {"form":"oral solution","mg_per_ml":25},
    {"form":"extended-release capsule (Trokendi/Qudexy)","strengths_mg":[25,50,100,150,200]}]},
 "brivaracetam": {"rxcui":"1739745","oral_forms":[
    {"form":"tablet","strengths_mg":[10,25,50,75,100]},
    {"form":"oral solution","mg_per_ml":10}]},
 "clobazam": {"rxcui":"21241","oral_forms":[
    {"form":"tablet","strengths_mg":[5,10,20]},
    {"form":"oral suspension","mg_per_ml":2.5},
    {"form":"oral film","strengths_mg":[5,10,20]}]},
 "valproic_acid": {"rxcui":"11118","oral_forms":[
    {"form":"capsule","strengths_mg":[250]},
    {"form":"delayed-release capsule","strengths_mg":[125,150,250,500]},
    {"form":"delayed-release tablet","strengths_mg":[200]},
    {"form":"tablet","strengths_mg":[100]},
    {"form":"oral solution (syrup)","mg_per_ml":50},
    {"form":"oral solution","mg_per_ml":40}]},
 "lacosamide": {"rxcui":"623400","oral_forms":[
    {"form":"tablet","strengths_mg":[50,100,150,200]},
    {"form":"oral solution","mg_per_ml":10},
    {"form":"extended-release capsule","strengths_mg":[100,150,200]}]},
 "oxcarbazepine": {"rxcui":"32624","oral_forms":[
    {"form":"tablet","strengths_mg":[150,300,600]},
    {"form":"oral suspension","mg_per_ml":60},
    {"form":"extended-release tablet (Oxtellar XR, >=6y)","strengths_mg":[150,300,600]}]},
 "carbamazepine": {"rxcui":"2002","oral_forms":[
    {"form":"chewable tablet","strengths_mg":[100,200]},
    {"form":"tablet","strengths_mg":[200,400]},
    {"form":"oral suspension","mg_per_ml":20},
    {"form":"extended-release tablet","strengths_mg":[100,150,200,300,400,600]},
    {"form":"extended-release capsule","strengths_mg":[100,200,300]}]},
 "zonisamide": {"rxcui":"39998","oral_forms":[
    {"form":"capsule","strengths_mg":[25,50,100]},
    {"form":"tablet","strengths_mg":[100]},
    {"form":"oral suspension (Zonisade)","mg_per_ml":20}]},
 "ethosuximide": {"rxcui":"4135","oral_forms":[
    {"form":"capsule","strengths_mg":[250]},
    {"form":"oral solution","mg_per_ml":50}]},
}

def wb(label, min_age=2, max_age=17, min_w=None, max_w=None, start=None, tmin=None, tmax=None,
       maxkg=None, step=None, weeks=None, cap=None, freq=2, src="", note=""):
    """weight-based (mg/kg/day) band."""
    return {"label":label,"dose_basis":"weight_based_mg_per_kg_per_day",
            "min_age_years":min_age,"max_age_years":max_age,"min_weight_kg":min_w,"max_weight_kg":max_w,
            "start_mg_per_kg_per_day":start,"target_mg_per_kg_per_day_min":tmin,"target_mg_per_kg_per_day_max":tmax,
            "max_mg_per_kg_per_day":maxkg,"uptitration_step_mg_per_kg_per_day":step,
            "uptitration_interval_weeks":weeks,"absolute_max_mg_per_day":cap,"frequency_per_day":freq,
            "source":src,"verified":False,"clinician_note":note}

def fb(label, min_age=2, max_age=17, min_w=None, max_w=None, schedule=None, maxmg=None,
       step_interval_days=7, freq=2, src="", note="",
       # current-engine compatibility (absolute mg) so this band loads & runs unmodified:
       eng_dose_max=None, eng_max_daily=None, eng_step=None, eng_weeks=None):
    """fixed / weight-band fixed-mg band (loads in CURRENT engine via eng_* fields)."""
    return {"label":label,"dose_basis":"weight_band_fixed_mg_per_day",
            "min_age_years":min_age,"max_age_years":max_age,"min_weight_kg":min_w,"max_weight_kg":max_w,
            "fixed_titration_schedule":schedule,"max_mg_per_day":maxmg,
            "uptitration_interval_days":step_interval_days,"frequency_per_day":freq,
            "source":src,"verified":False,"clinician_note":note,
            # ---- fields consumed by the UNMODIFIED engine_core/asm_rules.DoseBand ----
            "dose_range_max_mg_per_day":eng_dose_max,"max_daily_mg":eng_max_daily,
            "standard_uptitration_step_mg":eng_step,"standard_uptitration_interval_weeks":eng_weeks}

DRUGS = {
 "levetiracetam": {
   "display_name":"Levetiracetam (Keppra)","in_study":True,
   "indications":["focal (partial-onset)","myoclonic (JME, >=12y)","primary generalized tonic-clonic (>=6y)"],
   "fda_pediatric_age":"focal from 1 month; myoclonic (JME) from 12y; PGTC from 6y",
   "off_label_in_study_ages":False,
   "dose_bands":[ wb("Children/adolescents 2-17y, weight-based", start=20, tmin=40, tmax=60, maxkg=60,
                     step=20, weeks=2, cap=3000, freq=2, src="keppra",
                     note="Oral solution recommended if <=20 kg. Renally cleared (study excludes renal impairment). Watch behavioral/mood effects.") ],
   "key_safety":"Behavioral/psychiatric effects (irritability, mood). Renal clearance -> reduce in renal impairment (excluded from study).",
   "key_interactions":"Few clinically significant PK interactions.",
 },
 "lamotrigine": {
   "display_name":"Lamotrigine (Lamictal)","in_study":True,
   "indications":["adjunctive focal","adjunctive PGTC","adjunctive LGS (>=2y)"],
   "fda_pediatric_age":"adjunctive, ages 2 and older",
   "off_label_in_study_ages":False,
   "SAFETY_PRIORITY":"BOXED WARNING: serious rash (SJS/TEN). Risk increased by concomitant valproate, by exceeding the initial dose, and by escalating faster than recommended. Slow titration is essential. Titration is CONCOMITANT-MED DEPENDENT (see 3 regimens).",
   "dose_bands":[
     wb("2-17y, WITHOUT valproate and WITHOUT enzyme inducer", start=0.3, tmin=4.5, tmax=7.5, maxkg=None,
        step=0.6, weeks=2, cap=300, freq=2, src="lamictal",
        note="Wk1-2: 0.3 mg/kg/day; wk3-4: 0.6 mg/kg/day; then +0.6 mg/kg/day q1-2wk. Target 4.5-7.5 mg/kg/day (cap ~300 mg/day adjunctive)."),
     wb("2-17y, WITH valproate (much lower & slower)", start=0.15, tmin=1.0, tmax=5.0, maxkg=None,
        step=0.3, weeks=2, cap=200, freq=2, src="lamictal",
        note="VALPROATE INTERACTION: ~50% reduction at each step. Wk1-2: 0.15 mg/kg/day; wk3-4: 0.3 mg/kg/day; then +0.3 mg/kg/day q1-2wk. Target 1-5 mg/kg/day (<=2.5 mg/kg/day or 200 mg/day). HIGHEST rash risk."),
     wb("2-17y, WITH enzyme inducer (carbamazepine/phenytoin/phenobarbital/primidone) and WITHOUT valproate",
        start=0.6, tmin=5.0, tmax=15.0, maxkg=None, step=1.2, weeks=2, cap=400, freq=2, src="lamictal",
        note="Wk1-2: 0.6 mg/kg/day; wk3-4: 1.2 mg/kg/day; then +1.2 mg/kg/day q1-2wk. Target 5-15 mg/kg/day (cap ~400 mg/day)."),
   ],
   "key_safety":"SJS/TEN boxed warning (esp first 8 weeks). Do NOT exceed initial dose or recommended escalation rate.",
   "key_interactions":"Valproate raises lamotrigine levels & rash risk (MAJOR -> lower/slower). Enzyme inducers lower levels (higher/faster). Engine must select the correct regimen from concomitant meds.",
 },
 "topiramate": {
   "display_name":"Topiramate (Topamax)","in_study":True,
   "indications":["adjunctive focal","adjunctive PGTC","adjunctive LGS","monotherapy (>=2y)"],
   "fda_pediatric_age":"adjunctive and monotherapy from 2y",
   "off_label_in_study_ages":False,
   "dose_bands":[ wb("2-17y, weight-based (adjunctive)", start=1, tmin=5, tmax=9, maxkg=9,
                     step=2, weeks=1, cap=400, freq=2, src="topamax",
                     note="Start ~1-3 mg/kg/day (=25 mg) nightly wk1; increase 1-3 mg/kg/day q1-2wk. Target 5-9 mg/kg/day. step shown = mid of 1-3 mg/kg/day range - confirm.") ],
   "key_safety":"Metabolic acidosis; oligohidrosis/hyperthermia (esp children, hot weather); cognitive/word-finding; weight loss; nephrolithiasis.",
   "key_interactions":"Levels lowered by enzyme inducers (carbamazepine, phenytoin).",
 },
 "oxcarbazepine": {
   "display_name":"Oxcarbazepine (Trileptal)","in_study":True,
   "indications":["focal - monotherapy (>=4y)","focal - adjunctive (>=2y)"],
   "fda_pediatric_age":"adjunctive from 2y; monotherapy from 4y",
   "off_label_in_study_ages":False,
   "dose_bands":[ wb("2-17y, weight-based (adjunctive)", start=9, tmin=30, tmax=46, maxkg=60,
                     step=10, weeks=1, cap=2400, freq=2, src="trileptal",
                     note="Start 8-10 mg/kg/day (<=600 mg/day); reach maintenance over 2-4 wks; target 30-46 mg/kg/day by weight tier; max 60 mg/kg/day. Label also gives weight-tiered absolute targets (e.g. 20-29kg ~900 mg/day; 29.1-39kg ~1200; >39kg ~1800).") ],
   "key_safety":"Hyponatremia (check sodium); HLA-B*1502 -> SJS risk (screen patients of Asian ancestry).",
   "key_interactions":"Dose-dependent enzyme induction; lowers hormonal contraceptives; interacts with lamotrigine/phenytoin.",
 },
 "carbamazepine": {
   "display_name":"Carbamazepine (Tegretol)","in_study":True,
   "indications":["focal","generalized tonic-clonic","mixed seizure patterns"],
   "fda_pediatric_age":"all ages (epilepsy)",
   "off_label_in_study_ages":False,
   "dose_bands":[
     wb("2 to <6y, weight-based", min_age=2, max_age=5.99, start=10, tmin=10, tmax=20, maxkg=35,
        step=5, weeks=1, cap=None, freq=3, src="tegretol",
        note="Start 10-20 mg/kg/day divided BID-TID (QID for suspension); increase weekly; maintenance generally <35 mg/kg/day."),
     fb("6 to 12y, fixed-mg titration", min_age=6, max_age=12, schedule=[{"day":1,"total_mg_per_day":200},{"day":8,"total_mg_per_day":300},{"day":15,"total_mg_per_day":400}],
        maxmg=1000, step_interval_days=7, freq=2, src="tegretol",
        note="Start 100 mg BID; add up to 100 mg/day weekly; target 400-800 mg/day; max 1000 mg/day (6-12y).",
        eng_dose_max=800, eng_max_daily=1000, eng_step=100, eng_weeks=1),
     fb(">12 to 17y, fixed-mg titration", min_age=12.01, max_age=17, schedule=[{"day":1,"total_mg_per_day":400},{"day":8,"total_mg_per_day":600},{"day":15,"total_mg_per_day":800}],
        maxmg=1200, step_interval_days=7, freq=2, src="tegretol",
        note="Start 200 mg BID; add up to 200 mg/day weekly; target 800-1200 mg/day; max 1000 mg/day (12-15y), 1200 mg/day (15-17y).",
        eng_dose_max=1000, eng_max_daily=1200, eng_step=200, eng_weeks=1),
   ],
   "key_safety":"HLA-B*1502 (SJS/TEN; screen Asian ancestry); AUTOINDUCTION (levels fall ~wk3-5, may need re-titration); hyponatremia; rare aplastic anemia/agranulocytosis.",
   "key_interactions":"Strong enzyme inducer (lowers lamotrigine, oral contraceptives, many drugs); levels raised by CYP3A4 inhibitors.",
 },
 "valproic_acid": {
   "display_name":"Valproic acid (Depakene; cf. divalproex/Depakote)","in_study":True,
   "indications":["complex partial","absence (simple & complex)","multiple seizure types"],
   "fda_pediatric_age":"complex partial >=10y; absence broad",
   "off_label_in_study_ages":False,
   "dose_bands":[ wb("2-17y, weight-based", start=12.5, tmin=15, tmax=60, maxkg=60,
                     step=7.5, weeks=1, cap=3000, freq=2, src="depakene",
                     note="Start 10-15 mg/kg/day; increase 5-10 mg/kg/week; max 60 mg/kg/day. step/start shown = midpoints - confirm. Divalproex (Depakote) is ~1:1 valproic acid equivalent.") ],
   "key_safety":"BOXED: hepatotoxicity (highest <2y & polytherapy; <2y excluded by study age >=2), pancreatitis, teratogenicity (counsel patients of childbearing potential). Also thrombocytopenia, hyperammonemia.",
   "key_interactions":"Raises lamotrigine levels & rash risk (MAJOR). Levels affected by enzyme inducers/carbapenems.",
 },
 "clobazam": {
   "display_name":"Clobazam (Onfi/Sympazan)","in_study":True,
   "indications":["adjunctive Lennox-Gastaut syndrome (>=2y)"],
   "fda_pediatric_age":"from 2y (LGS adjunctive)",
   "off_label_in_study_ages":False,
   "dose_bands":[
     fb("<=30 kg body weight", min_age=2, max_age=17, min_w=0, max_w=30,
        schedule=[{"day":1,"total_mg_per_day":5},{"day":8,"total_mg_per_day":10},{"day":15,"total_mg_per_day":20}],
        maxmg=20, step_interval_days=7, freq=2, src="onfi",
        note="<=30kg: 5 mg/day -> day8 10 mg/day -> day15 20 mg/day (max 20). Doses >5 mg/day are divided BID.",
        eng_dose_max=20, eng_max_daily=20, eng_step=5, eng_weeks=1),
     fb(">30 kg body weight", min_age=2, max_age=17, min_w=30.01,
        schedule=[{"day":1,"total_mg_per_day":10},{"day":8,"total_mg_per_day":20},{"day":15,"total_mg_per_day":40}],
        maxmg=40, step_interval_days=7, freq=2, src="onfi",
        note=">30kg: 10 mg/day -> day8 20 mg/day -> day15 40 mg/day (max 40).",
        eng_dose_max=40, eng_max_daily=40, eng_step=10, eng_weeks=1),
   ],
   "key_safety":"Benzodiazepine: somnolence/sedation, physical dependence & withdrawal (TAPER, do not stop abruptly). CYP2C19 poor metabolizers -> lower dose. SJS/TEN reported (esp first 8 weeks).",
   "key_interactions":"CNS depressants (additive sedation); CYP2C19 substrate.",
 },
 "lacosamide": {
   "display_name":"Lacosamide (Vimpat)","in_study":True,
   "indications":["focal (partial-onset) - monotherapy & adjunctive"],
   "fda_pediatric_age":"from 1 month",
   "off_label_in_study_ages":False,
   "dose_bands":[
     wb("11 to <30 kg, weight-based", min_age=2, max_age=17, min_w=11, max_w=29.99, start=2, tmin=6, tmax=12, maxkg=12,
        step=2, weeks=1, cap=None, freq=2, src="vimpat",
        note="Start ~2 mg/kg/day; titrate <= once/week; maintenance 6-12 mg/kg/day (<30kg)."),
     wb("30 to <50 kg, weight-based", min_age=2, max_age=17, min_w=30, max_w=49.99, start=2, tmin=4, tmax=8, maxkg=8,
        step=2, weeks=1, cap=None, freq=2, src="vimpat",
        note="Start ~2 mg/kg/day; maintenance 4-8 mg/kg/day (30-<50kg)."),
     fb(">=50 kg, fixed-mg", min_age=2, max_age=17, min_w=50,
        schedule=[{"day":1,"total_mg_per_day":100},{"day":8,"total_mg_per_day":200}],
        maxmg=400, step_interval_days=7, freq=2, src="vimpat",
        note=">=50kg: start 50 mg BID (100 mg/day); maintenance 200-400 mg/day. Confirm max (label notes 300 mg/day recommended in some peds contexts vs 400 adult).",
        eng_dose_max=400, eng_max_daily=400, eng_step=100, eng_weeks=1),
   ],
   "key_safety":"PR-interval prolongation (caution with cardiac conduction problems/concomitant PR-prolonging drugs); dizziness/ataxia. Schedule V.",
   "key_interactions":"Additive PR prolongation; modest with strong CYP inducers.",
 },
 "brivaracetam": {
   "display_name":"Brivaracetam (Briviact)","in_study":True,
   "indications":["focal (partial-onset)"],
   "fda_pediatric_age":"from 1 month",
   "off_label_in_study_ages":False,
   "dose_bands":[
     wb("11 to <20 kg, weight-based", min_age=2, max_age=17, min_w=11, max_w=19.99, start=1.5, tmin=2.5, tmax=5.0, maxkg=5.0,
        step=None, weeks=None, cap=None, freq=2, src="briviact",
        note="Weight-based BID. 11-<20kg: 0.5-2.5 mg/kg BID (1-5 mg/kg/day); typical start ~1.25 mg/kg BID. Can start at target (no forced titration)."),
     wb("20 to <50 kg, weight-based", min_age=2, max_age=17, min_w=20, max_w=49.99, start=1.0, tmin=2.0, tmax=4.0, maxkg=4.0,
        step=None, weeks=None, cap=None, freq=2, src="briviact",
        note="20-<50kg: 0.5-2 mg/kg BID (1-4 mg/kg/day); typical start 1 mg/kg BID."),
     fb(">=50 kg, fixed-mg", min_age=2, max_age=17, min_w=50,
        schedule=[{"day":1,"total_mg_per_day":100}], maxmg=200, step_interval_days=0, freq=2, src="briviact",
        note=">=50kg: start 50 mg BID (100 mg/day); range 25-100 mg BID (50-200 mg/day). No titration required.",
        eng_dose_max=200, eng_max_daily=200, eng_step=50, eng_weeks=1),
   ],
   "key_safety":"Behavioral/psychiatric reactions (irritability, anxiety). Schedule V. Reduce in hepatic impairment (excluded from study).",
   "key_interactions":"Levels lowered by strong enzyme inducers (carbamazepine, phenytoin); rifampin lowers levels.",
 },
 "zonisamide": {
   "display_name":"Zonisamide (Zonegran; Zonisade susp)","in_study":True,
   "indications":["adjunctive focal (FDA >=16y); pediatric use is OFF-LABEL"],
   "fda_pediatric_age":">=16y (Zonegran). Pediatric <16y is OFF-LABEL (Zonisade oral suspension has a partial-seizure indication - confirm pediatric age).",
   "off_label_in_study_ages":True,
   "dose_bands":[ wb("2 to <16y, weight-based (OFF-LABEL)", min_age=2, max_age=15.99, start=1.5, tmin=4, tmax=8, maxkg=12,
                     step=1.5, weeks=2, cap=600, freq=1, src="zonegran",
                     note="OFF-LABEL <16y. Start 1-2 mg/kg/day; increase 1-2 mg/kg/day q1-2wk; target 4-8 mg/kg/day; max ~12 mg/kg/day or 600 mg/day. Long half-life (QD-BID)."),
                  fb("16-17y (FDA-approved range)", min_age=16, max_age=17,
                     schedule=[{"day":1,"total_mg_per_day":100},{"day":15,"total_mg_per_day":200},{"day":29,"total_mg_per_day":300}],
                     maxmg=600, step_interval_days=14, freq=1, src="zonegran",
                     note="16y+: 100 mg/day start; increase by 100 mg/day q>=2wk; max 600 mg/day.",
                     eng_dose_max=400, eng_max_daily=600, eng_step=100, eng_weeks=2) ],
   "key_safety":"OFF-LABEL in children. Sulfonamide (avoid if sulfa allergy). Oligohidrosis/hyperthermia in children (IMPORTANT - heat illness). Metabolic acidosis; renal stones; long half-life.",
   "key_interactions":"Levels lowered by enzyme inducers.",
 },
 "ethosuximide": {
   "display_name":"Ethosuximide (Zarontin)","in_study":True,
   "indications":["absence (petit mal) ONLY - no efficacy for focal/GTC"],
   "fda_pediatric_age":"from 3y",
   "off_label_in_study_ages":False,
   "dose_bands":[
     fb("3 to 6y, fixed-mg start", min_age=3, max_age=6,
        schedule=[{"day":1,"total_mg_per_day":250}], maxmg=1500, step_interval_days=5, freq=2, src="zarontin",
        note="3-6y: start 250 mg/day; increase 250 mg every 4-7 days; optimal ~20 mg/kg/day; max 1500 mg/day.",
        eng_dose_max=1000, eng_max_daily=1500, eng_step=250, eng_weeks=1),
     fb(">6 to 17y, fixed-mg start", min_age=6.01, max_age=17,
        schedule=[{"day":1,"total_mg_per_day":500}], maxmg=1500, step_interval_days=5, freq=2, src="zarontin",
        note=">6y: start 500 mg/day; increase 250 mg every 4-7 days; optimal ~20 mg/kg/day; max 1500 mg/day.",
        eng_dose_max=1000, eng_max_daily=1500, eng_step=250, eng_weeks=1),
   ],
   "key_safety":"ABSENCE ONLY - does not protect against GTC/focal (if mixed epilepsy, a broad-spectrum agent is needed). Rare blood dyscrasias; SJS reported; GI upset.",
   "key_interactions":"Valproate may raise ethosuximide levels.",
 },
}

# attach formulations
for k,v in DRUGS.items():
    v["formulations_rxnorm"] = RXNORM[k]["oral_forms"]
    v["rxcui"] = RXNORM[k]["rxcui"]

doc = {
 "_meta": {
   "title":"DRAFT ASM titration rules - Aim 2 (pediatric epilepsy medication assistant)",
   "status":"UNVERIFIED DRAFT. Every clinical value requires verification and correction by Dr. Barbour (PI) before any clinical use. Provided only to make the rule-setting session efficient - 'something to edit rather than a blank page.'",
   "generated":PULLED,
   "scope":"Ages 2-17, core titration only (dose bands, ceilings, uptitration step/interval, formulations). Interactions/monitoring summarized briefly only where they drive titration (e.g. lamotrigine+valproate).",
   "provenance":"Formulations from RxNorm (pulled 2026-06-17). Dose bands compiled from FDA prescribing information / standard pediatric references (per-drug 'source' keys map to _sources).",
   "schema_note":"EXTENDED vs engine_core/asm_rules.py DoseBand. Weight-based bands use *_mg_per_kg_per_day fields (most pediatric ASM dosing is weight-based and the current absolute-mg DoseBand cannot represent it). Fixed/weight-band bands ALSO include the engine's existing absolute fields (dose_range_max_mg_per_day, max_daily_mg, standard_uptitration_step_mg, standard_uptitration_interval_weeks) so they load and run in the UNMODIFIED engine. See PROPOSED_engine_patch.md to make weight-based bands produce schedules.",
   "openfda_currency_note":"openFDA label dataset updates were reported suspended in 2025; values were cross-checked against current FDA PI pages where possible but still require clinician verification.",
   "hard_rules":[
     "Clinical decisions remain RULE-BASED and clinician-defined; this file is data, not logic.",
     "No value here is authoritative until the PI verifies it (verified=false on every band).",
     "Confidential: keep in the study folder only.",
   ],
   "_sources":SOURCES,
 }
}
doc.update(DRUGS)

with open(os.path.join(OUT,"asm_rules.draft.json"),"w") as f:
    json.dump(doc, f, indent=2)

with open(os.path.join(HERE,"raw_rxnorm_formulations.json"),"w") as f:
    json.dump({"pulled":PULLED,"source":SOURCES["rxnorm"],"data":RXNORM}, f, indent=2)

# ---- flatten to verification rows (one row per editable clinical value) ----
rows=[]
def add(drug, band, field, value, src, note):
    rows.append({"drug":DRUGS[drug]["display_name"],"band":band.get("label",""),
                 "field":field,"draft_value":value,"source":SOURCES.get(src,src),
                 "verified_correct_Y_N":"","corrected_value":"","clinician_notes":note})
for dkey,d in DRUGS.items():
    for b in d["dose_bands"]:
        src=b.get("source","")
        if b["dose_basis"]=="weight_based_mg_per_kg_per_day":
            for fld in ["start_mg_per_kg_per_day","target_mg_per_kg_per_day_min","target_mg_per_kg_per_day_max",
                        "max_mg_per_kg_per_day","uptitration_step_mg_per_kg_per_day","uptitration_interval_weeks",
                        "absolute_max_mg_per_day","frequency_per_day"]:
                if b.get(fld) is not None:
                    add(dkey,b,fld,b[fld],src,b.get("clinician_note",""))
        else:
            add(dkey,b,"fixed_titration_schedule",json.dumps(b.get("fixed_titration_schedule")),src,b.get("clinician_note",""))
            for fld in ["max_mg_per_day","uptitration_interval_days","frequency_per_day"]:
                if b.get(fld) is not None:
                    add(dkey,b,fld,b[fld],src,"")
    # one safety row per drug
    add(dkey,{"label":"(drug-level)"} ,"key_safety",d.get("SAFETY_PRIORITY",d.get("key_safety","")),d["dose_bands"][0].get("source",""),
        "Confirm safety framing; this drives the safety-check layer.")

with open(os.path.join(OUT,"verification_rows.json"),"w") as f:
    json.dump(rows, f, indent=2)

# ---------------- SANITY CHECKS ----------------
problems=[]
for dkey,d in DRUGS.items():
    for b in d["dose_bands"]:
        lbl=f"{dkey}/{b.get('label')}"
        if b["dose_basis"]=="weight_based_mg_per_kg_per_day":
            s,tmn,tmx,mx=b.get("start_mg_per_kg_per_day"),b.get("target_mg_per_kg_per_day_min"),b.get("target_mg_per_kg_per_day_max"),b.get("max_mg_per_kg_per_day")
            if tmn and tmx and tmn>tmx: problems.append(f"{lbl}: target_min>target_max")
            if mx and tmx and tmx>mx: problems.append(f"{lbl}: target_max>max_mg_per_kg")
            if s and mx and s>mx: problems.append(f"{lbl}: start>max")
            if b.get("uptitration_step_mg_per_kg_per_day") is not None and b["uptitration_step_mg_per_kg_per_day"]<=0:
                problems.append(f"{lbl}: step<=0")
        else:
            sched=b.get("fixed_titration_schedule") or []
            tot=[x["total_mg_per_day"] for x in sched]
            if tot!=sorted(tot): problems.append(f"{lbl}: fixed schedule not non-decreasing")
            if b.get("max_mg_per_day") and tot and tot[-1]>b["max_mg_per_day"]:
                problems.append(f"{lbl}: schedule exceeds max_mg_per_day")
print("Drugs:",len(DRUGS)," bands:",sum(len(d['dose_bands']) for d in DRUGS.values()))
print("Verification rows:",len(rows))
print("Sanity problems:",problems if problems else "NONE")
print("Wrote:",os.path.join(OUT,"asm_rules.draft.json"))
