"""
ui.py - a local web interface for the Aim 2 engine (no dependencies).

It serves a clinician form, runs the REAL engine_core (single source of truth -
no logic is duplicated in the browser), and shows the recommendation, rationale,
safety banner, draft clinical note, and draft patient message.

Run from the aim2_engine/ directory:
    python3 ui.py
Then open the URL it prints (default http://127.0.0.1:8000).

SYNTHETIC DATA ONLY until IRB/security sign-off. The engine ships with empty
dosing tables, so titration numbers appear only if you tick "illustrative
dosing (demo)" - clearly labeled, not clinical values.
"""

import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine_core import (
    run_engine, snapshot_from_interval, PatientContext, MedicationContext,
    PhysicianContingency, DrugClassifier, ASMRule, DoseBand,
)
from engine_core.drug_identity import known_study_medications
from engine_core.humanize import category_label, human_reasons

HOST, PORT = os.environ.get("ASM_HOST", "127.0.0.1"), int(os.environ.get("ASM_PORT", "8000"))
MEDS = sorted(known_study_medications())
CLASSIFIER = DrugClassifier(default_by="mechanism")

CSS = """
*{box-sizing:border-box} body{margin:0;background:#f4f6f9;color:#1f2933;
font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:22px 18px 70px}
header{background:#1f5f8b;color:#fff;padding:16px 18px;border-radius:0 0 10px 10px;margin-bottom:20px}
header h1{margin:0;font-size:18px} header p{margin:4px 0 0;font-size:12.5px;opacity:.9}
.card{background:#fff;border:1px solid #dce3ec;border-radius:10px;padding:16px 18px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:.03em;color:#16435f;
border-bottom:2px solid #eef3f8;padding-bottom:7px}
label{display:block;font-weight:600;font-size:13px;margin:10px 0 4px}
input[type=text],input[type=number],input[type=date],select,textarea{width:100%;padding:8px 9px;
border:1px solid #dce3ec;border-radius:7px;font-size:14px;background:#fff}
.row{display:flex;gap:14px;flex-wrap:wrap}.row>div{flex:1 1 180px}
.radios label,.checks label{display:inline-flex;align-items:center;gap:6px;font-weight:400;margin:4px 12px 4px 0;cursor:pointer}
.radios input,.checks input{width:auto}
fieldset{border:1px dashed #dce3ec;border-radius:8px;margin-top:10px;padding:8px 12px}
legend{font-size:12px;color:#647084;font-weight:600;padding:0 5px}
.btn{background:#1f5f8b;color:#fff;border:0;border-radius:8px;padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}
.btn:hover{background:#16435f}.muted{color:#647084;font-size:12px}
a{color:#1f5f8b}
.banner{border-radius:9px;padding:13px 15px;margin-bottom:16px;font-size:14px;font-weight:600}
.ok{background:#e6f5ee;border:1px solid #b7e2cd;color:#1b7a52}
.warn{background:#fff6e0;border:1px solid #f0d68a;color:#9a6700}
.stop{background:#fbe9e9;border:1px solid #e6a6a6;color:#a31515}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;font-weight:700}
pre{white-space:pre-wrap;background:#fbfcfe;border:1px solid #dce3ec;border-radius:8px;padding:13px;
font:12.5px/1.5 ui-monospace,Menlo,Consolas,monospace}
ul{margin:6px 0;padding-left:20px}.foot{font-size:11.5px;color:#647084;text-align:center;margin-top:26px}
"""

def _opts(pairs, sel=None):
    return "".join(f"<option value='{html.escape(v)}'{' selected' if v==sel else ''}>{html.escape(l)}</option>"
                   for v, l in pairs)


def _checks(name, pairs):
    return "".join(f"<label><input type=checkbox name='{name}' value='{html.escape(v)}'>{html.escape(l)}</label>"
                   for v, l in pairs)


def page(title, body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
            "<header><div class=wrap style='padding:0;margin:0 auto'>"
            "<h1>Aim 2 Medication Assistant</h1><p>Rules-based draft decision support &middot; "
            "synthetic data only &middot; physician approves every output</p></div></header>"
            f"<div class=wrap>{body}<p class=foot>All outputs are AI-generated DRAFTS for physician "
            "review. Dosing is illustrative unless clinician tables are loaded.</p></div></body></html>")


def form_page():
    med_opts = _opts([("", "- select -")] + [(m, m.replace("_", " ").title()) for m in MEDS])
    switch_opts = _opts([("", "- none -")] + [(m, m.replace("_", " ").title()) for m in MEDS])
    return page("New analysis", f"""
<form method=post action='/analyze'>
<div class=card><h2>Patient &amp; medication</h2>
  <div class=row>
    <div><label>Patient ID</label><input type=text name=patient_id value='DEMO01'></div>
    <div><label>Age (yr)</label><input type=number step=0.1 name=age value='8'></div>
    <div><label>Weight (kg)</label><input type=number step=0.1 name=weight value='30'></div>
  </div>
  <div class=row>
    <div><label>Medication</label><select name=med required>{med_opts}</select></div>
    <div><label>Amount/dose</label><input type=number step=any name=amt value='250'></div>
    <div><label>Unit</label><select name=unit>{_opts([('mg','mg'),('mL','mL'),('tablets','tablets')])}</select></div>
    <div><label>Times/day</label><select name=freq>{_opts([('1','1'),('2','2'),('3','3'),('4','4')],'2')}</select></div>
  </div>
  <label class=muted><input type=checkbox name=demo_dosing value=1 style='width:auto'>
    &nbsp;Use illustrative dosing (demo only) to show titration numbers</label>
</div>

<div class=card><h2>Physician contingency</h2>
  <label class=muted><input type=checkbox name=can_increase value=1 checked style='width:auto'>&nbsp;Approves further dose increase</label>
  <label>Approved switch target</label><select name=switch_to>{switch_opts}</select>
</div>

<div class=card><h2>Adherence (Q2)</h2>
  <select name=adherence>{_opts([('almost_never','Almost never misses'),('sometimes','Sometimes/often misses'),('stopped','Stopped taking it')])}</select>
</div>

<div class=card><h2>Seizures (Q3-Q7)</h2>
  <div class=radios><label><input type=radio name=seizures value=no checked>None since last update</label>
    <label><input type=radio name=seizures value=yes>Breakthrough seizures</label></div>
  <fieldset><legend>If breakthrough</legend>
    <label>Type(s) (Q4)</label>{_checks('q4',[('brief_staring','Brief staring'),('sudden_jerks','Sudden jerks'),('stiffening','Stiffening'),('whole_body','Whole-body shaking'),('focal','One part of body'),('not_sure','Not sure')])}
    <div class=row><div><label>Count in 4 wks (Q5)</label><input type=number name=q5></div>
      <div><label>Unit</label><select name=q5_unit>{_opts([('','-'),('per_day','per day'),('per_week','per week'),('per_month','per month')])}</select></div>
      <div><label>Most recent (Q5b)</label><input type=date name=q5b></div></div>
    <div class=row><div><label>Trend (Q6)</label><select name=q6>{_opts([('','-'),('fewer','Fewer'),('about_same','About same'),('more','More frequent'),('new_type','New type')])}</select></div>
      <div><label>On meds 2 days prior? (Q7)</label><select name=q7>{_opts([('','-'),('yes','Yes'),('no','No - missed meds')])}</select></div></div>
  </fieldset>
</div>

<div class=card><h2>Side effects (Q8-Q10)</h2>
  <div class=radios><label><input type=radio name=se value=no checked>None</label>
    <label><input type=radio name=se value=yes>Side effects reported</label></div>
  <fieldset><legend>If side effects</legend>
    <label>Which (Q9)</label>{_checks('q9',[('sleepiness','Sleepiness'),('irritability','Irritability'),('behavior','Behavior'),('dizziness','Dizziness'),('rash','Rash'),('nausea','Nausea')])}
    <div class=row><div><label>Rash (Q9a)</label><select name=q9a>{_opts([('','No rash'),('small','Small/localized'),('large','Large'),('blisters','Spreading/blistering')])}</select></div>
      <div><label>Impact (Q10b/c)</label><select name=impact>{_opts([('','-'),('no_impact','No impact'),('small','Small'),('medium_big','A lot - tolerable'),('big_wants_change','Intolerable - wants change')])}</select></div>
      <div><label>Dose/goal (Q10a)</label><select name=q10a>{_opts([('not_sure','Not sure'),('still_increasing','Still increasing'),('at_goal','At goal dose')])}</select></div></div>
  </fieldset>
</div>

<div class=card><h2>Overall &amp; preference (Q11-Q13)</h2>
  <div class=row><div><label>Overall (Q11)</label><select name=q11>{_opts([('','-'),('better','Better'),('about_same','About same'),('worse','Worse'),('not_sure','Not sure')])}</select></div>
    <div><label>Family preference (Q12)</label><select name=q12>{_opts([('no_preference','No preference'),('change_dose','Change/slower dose'),('different_medicine','Different medicine'),('other','Other')])}</select></div></div>
  <label>Anything else (Q13)</label><textarea name=q13 rows=2></textarea>
  <label>Physician name (for draft message)</label><input type=text name=physician value='Dr. Barbour'>
</div>
<button class=btn type=submit>Generate draft recommendation</button>
</form>""")


def _one(f, k, d=""):
    v = f.get(k)
    return v[0] if v else d


def _many(f, k):
    return f.get(k, [])


def build_and_run(f):
    answers = {"Q2": _one(f, "adherence", "almost_never"),
               "Q3": _one(f, "seizures", "no"),
               "Q11": _one(f, "q11") or None, "Q12": _one(f, "q12", "no_preference"),
               "Q13": _one(f, "q13") or None}
    if answers["Q3"] == "yes":
        answers.update({"Q4": _many(f, "q4"), "Q5": _one(f, "q5") or None,
                        "Q5_unit": _one(f, "q5_unit") or None, "Q5b": _one(f, "q5b") or None,
                        "Q6": _one(f, "q6") or None, "Q7": _one(f, "q7") or None})
    if _one(f, "se") == "yes":
        answers.update({"Q9": _many(f, "q9") or ["other"], "Q9a": _one(f, "q9a") or None,
                        "Q10b_impact": _one(f, "impact") or None, "Q10a": _one(f, "q10a", "not_sure")})
    else:
        answers["Q10a"] = _one(f, "q10a", "not_sure")

    patient = PatientContext(external_id=_one(f, "patient_id", "DEMO"),
                             age_years=float(_one(f, "age", "0") or 0),
                             weight_kg=float(_one(f, "weight", "0") or 0))
    med = MedicationContext(medication=_one(f, "med"), dose_amount=float(_one(f, "amt", "0") or 0),
                            dose_unit=_one(f, "unit", "mg"), frequency_per_day=int(_one(f, "freq", "2") or 2))
    cont = PhysicianContingency(can_increase_dose=bool(_one(f, "can_increase")),
                                can_switch_to=_one(f, "switch_to") or None)
    snap = snapshot_from_interval(patient, med, cont, answers)

    asm_rule = None
    if _one(f, "demo_dosing") == "1":
        asm_rule = ASMRule(medication=med.medication, dose_bands=[DoseBand(
            label="ILLUSTRATIVE-not-clinical", min_age_years=0, max_age_years=120,
            dose_range_max_mg_per_day=(med.dose_amount * med.frequency_per_day * 3) or 1500,
            max_daily_mg=(med.dose_amount * med.frequency_per_day * 3) or 1500,
            standard_uptitration_step_mg=(med.dose_amount or 250),
            standard_uptitration_interval_weeks=2)])
    return run_engine(snap, asm_rule=asm_rule, drug_classifier=CLASSIFIER,
                      physician_name=_one(f, "physician", "[physician name]"))


def results_page(rec):
    cat = rec.category
    if rec.safety_concern_flag:
        banner = "<div class='banner stop'>&#9940; SAFETY STOP - urgent physician review; nothing auto-sent.</div>"
        pill = "<span class='pill stop'>SAFETY STOP</span>"
    elif rec.medication_flag:
        banner = "<div class='banner warn'>&#9888; Medication name not recognized - verify (see below).</div>"
        pill = f"<span class='pill warn'>{html.escape(category_label(cat))}</span>"
    else:
        banner = "<div class='banner ok'>&#10003; Draft ready for physician review.</div>"
        pill = f"<span class='pill ok'>{html.escape(category_label(cat))}</span>"

    parts = [banner, "<div class=card><h2>Recommendation</h2><p>", pill]
    if rec.switch_target:
        parts.append(f" &nbsp; <strong>Switch target:</strong> {html.escape(rec.switch_target)}")
    parts.append("</p>")
    if rec.medication_flag:
        parts.append(f"<p class=muted>{html.escape(rec.medication_flag)}</p>")
    if rec.switch_family_note:
        parts.append(f"<p class=muted>{html.escape(rec.switch_family_note)}</p>")
    parts.append("</div>")

    if rec.titration_schedule:
        ts = rec.titration_schedule
        if ts.get("status") == "ok":
            rows = "".join(f"<li>Step {s['step']}: {s['target_total_mg_per_day']} mg/day "
                           f"({s['per_dose_mg']} mg × dose)</li>" for s in ts["steps"])
            parts.append(f"<div class=card><h2>Titration (illustrative)</h2><ul>{rows}</ul></div>")
        else:
            parts.append(f"<div class=card><h2>Titration</h2><p class=muted>{html.escape(ts.get('note',''))}</p></div>")

    reason = "".join(f"<li>{html.escape(x)}</li>" for x in human_reasons(rec.rationale))
    parts.append(f"<div class=card><h2>Why this is suggested</h2><ul>{reason}</ul></div>")
    parts.append(f"<div class=card><h2>Draft clinical note</h2><pre>{html.escape(rec.clinical_summary)}</pre></div>")
    msg = rec.draft_patient_message or "[withheld - safety concern; physician must handle]"
    parts.append(f"<div class=card><h2>Draft patient message</h2><pre>{html.escape(msg)}</pre></div>")
    parts.append("<a class=btn href='/'>&larr; New analysis</a>")
    return page("Recommendation", "".join(parts))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        if self.path.split("?")[0] == "/":
            self._send(form_page())
        else:
            self._send(page("Not found", "<div class=card>Not found. <a href='/'>Home</a></div>"), 404)

    def do_POST(self):
        if self.path != "/analyze":
            self._send(page("Not found", "<div class=card>Not found.</div>"), 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        try:
            rec = build_and_run(form)
        except Exception as e:  # surface input errors cleanly
            self._send(page("Error", f"<div class='banner stop'>Could not analyze.</div>"
                            f"<div class=card><p>{html.escape(str(e))}</p>"
                            "<a class=btn href='/'>Back</a></div>"), 400)
            return
        self._send(results_page(rec))


def main():
    print(f"\n  Aim 2 Medication Assistant - local UI")
    print(f"  Open:  http://{HOST}:{PORT}")
    print(f"  Medications loaded: {len(MEDS)}   (synthetic data only; Ctrl+C to stop)\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
