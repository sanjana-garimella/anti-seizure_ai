#!/usr/bin/env python3
"""
build_knowledge_graph.py
------------------------
Generate an INTERPRETABLE, LAYERED knowledge graph of the ENTIRE Aim 2 titration
engine - not just the latest additions. It reads left -> right as a flow:

  Inputs -> Safety gates -> Branch -> Decision rules -> Data tables / Dose ladder
         -> Recommendations -> Validation (gold cases + PI review)

Every engine rule (R0-R18, BT, NS) is a node tagged with the recommendation it
yields and its clinical provenance (Barbour theme/date). Drugs -> weight bands ->
dose ladder and the decision tables are included, plus the 63 gold cases and Dr.
Barbour's PI validation review (with the flagged cases linked to the rule/table
that was fixed on 2026-07-08).

DATA-DRIVEN where possible (reads engine JSON = source of truth). Outputs, in
this folder:
    knowledge_graph.json   - nodes/edges with layer/lane for a deterministic layout
    knowledge_graph.html   - self-contained OFFLINE viewer: fixed layered layout,
                             legend, layer/lane filters, search, click-for-detail,
                             pan/zoom/drag. No external/CDN dependencies.

CONFIDENTIAL - DRAFT / 100% simulated values; stays in this folder.
Run:  python3 build_knowledge_graph.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(HERE, "..", "aim2_engine", "data")
_VAL = os.path.join(HERE, "..", "aim2_engine", "validation")


def _load(path, default=None):
    return json.load(open(path)) if os.path.isfile(path) else (default or {})


ASM = _load(os.path.join(_DATA, "asm_rules.json"))
LADDER = _load(os.path.join(_DATA, "dose_titration_ladder.json"))
BT = _load(os.path.join(_DATA, "breakthrough_decision_tables.json"))
NS = _load(os.path.join(_DATA, "no_seizures_decision_table.json"))
URG = _load(os.path.join(_DATA, "breakthrough_urgency_thresholds.json"))
GOLD = _load(os.path.join(_VAL, "gold_cases.json"))
REVIEW = _load(os.path.join(_VAL, "kristen_review_summary.json"))

# Layer indices (columns, left -> right) and human labels.
LAYERS = ["Inputs", "Safety gates", "Branch", "Decision rules",
          "Tables & dose ladder", "Recommendations", "Validation"]

nodes, edges, _seen = [], [], set()


def add_node(nid, label, ntype, layer, detail="", **attrs):
    if nid in _seen:
        return nid
    _seen.add(nid)
    n = {"id": nid, "label": label, "type": ntype, "layer": layer, "detail": detail}
    n.update(attrs)
    nodes.append(n)
    return nid


def add_edge(a, b, rel=""):
    if a in _seen and b in _seen:
        edges.append({"source": a, "target": b, "rel": rel})


# --- Recommendation category nodes (layer 5) --------------------------------
CATS = {
    "no_change": "Keep current dose", "escalate": "Increase the dose",
    "slower_uptitration": "Increase more slowly", "hold": "Hold at current dose",
    "lower_dose": "Lower the dose", "add_medication": "Add another medicine",
    "switch_medication": "Switch medicine", "insufficient_data": "Need more info / defer",
}
for k, lab in CATS.items():
    add_node(f"cat_{k}", lab, "recommendation", 5, f"Engine recommendation: {lab}.")

# --- Engine rules R0-R18 (layer 1 safety gates + layer 3 branch rules) -------
# (rule_id, label, layer, branch, category, provenance, detail)
RULES = [
    ("R0", "R0 · Spreading/blistering rash", 1, "safety", "switch_medication",
     "Barbour T11/T12 (SJS/TEN)", "Serious skin-reaction concern -> stop + switch; urgent review; family message withheld."),
    ("R0b", "R0b · Seizure emergency", 1, "safety", "escalate",
     "Barbour T13", "Prolonged/repeated seizures -> urgent review; if helping, increase faster; if not helping after min trial -> switch."),
    ("R0c", "R0c · Other urgent safety", 1, "safety", "insufficient_data",
     "safety", "Breathing/ER signal -> withhold automated rec; immediate physician review."),
    ("R1", "R1 · Dose changed + seizures", 1, "safety", "insufficient_data",
     "Barbour I4", "Regimen recently changed and seizures continue -> confirm regimen, contact clinic (not enough info)."),
    ("R2", "R2 · Stopped (side effects/cost)", 1, "safety", "switch_medication",
     "Barbour T10", "Family stopped for a switch-worthy reason -> switch to a better-tolerated/feasible option."),
    ("R3", "R3 · Drug not indicated", 1, "safety", "switch_medication",
     "Barbour T14", "Drug does not cover the reported seizure type (e.g. ethosuximide = absence only) -> switch."),
    ("R4", "R4 · Small rash + SJS workup", 1, "safety", "switch_medication",
     "Barbour T11", "Small rash on an SJS-risk drug + a workup criterion (ancestry/meds/missed doses/time/no viral illness) -> switch."),
    ("R4b", "R4b · Can't reach min dose (SE)", 1, "safety", "switch_medication",
     "Barbour 2026-06", "Side effects prevent even a minimum effective dose -> switch."),
    ("R5", "R5 · Adherence-driven breakthrough", 1, "safety", "no_change",
     "adherence", "Seizures coincide with missed doses -> support adherence before any dose change."),
    ("R-defer-se", "R-defer · Unknown SE impact", 1, "safety", "insufficient_data",
     "E3 defer-on-ambiguity", "Side-effect impact answer not recognized -> physician review (no confident rec)."),
    # breakthrough branch (layer 3)
    ("BT", "BT · Breakthrough table", 3, "breakthrough", None,
     "Barbour 2026-06-25; gate fixed 2026-07-08 (Kristen B1/B2/B3)",
     "Action by urgency tier x phase x side-effect level. Levels 1-5 route here (L1 only when duration captured)."),
    ("R5b", "R5b · Helping but dose-limited", 3, "breakthrough", "add_medication",
     "Barbour 2026-06", "Medicine helps but side effects block further increases while seizures continue -> ADD a second medicine."),
    ("R6", "R6 · Intolerable SE", 3, "breakthrough", "switch_medication",
     "T7 boundary", "Breakthrough + intolerable side effects -> switch."),
    ("R7", "R7 · Family prefers slower", 3, "breakthrough", "slower_uptitration",
     "family preference", "Breakthrough but family prefers a slower/changed dose -> slower uptitration."),
    ("R8", "R8 · No SE, no duration", 3, "breakthrough", "insufficient_data",
     "Barbour T1/T4", "Breakthrough, no side effects, but duration not captured -> need duration to decide continue vs faster."),
    ("R9", "R9 · Frequent/long -> faster", 3, "breakthrough", "escalate",
     "Barbour T1/T4", "Frequent and/or long breakthrough -> increase FASTER toward goal (per duration/frequency thresholds)."),
    ("R10", "R10 · Short/infrequent titrating", 3, "breakthrough", "no_change",
     "Barbour T1/T4", "Short/infrequent breakthrough while titrating -> continue the plan (keep increasing on schedule)."),
    ("R11", "R11 · Below faster thresholds", 3, "breakthrough", "no_change",
     "Barbour T1/T4", "Breakthrough below the increase-faster thresholds -> continue the current titration plan."),
    ("R12g", "R12g · At goal + medium SE", 3, "breakthrough", "insufficient_data",
     "Barbour 2026-06", "At goal, medium-impact SE, ongoing seizures -> ask family: tolerate & increase, or add a second med."),
    ("R12", "R12 · Manageable, no duration", 3, "breakthrough", "insufficient_data",
     "Barbour", "Breakthrough + manageable SE but no duration -> need duration to weigh increasing vs holding."),
    ("R13", "R13 · Titrating + medium SE", 3, "breakthrough", "slower_uptitration",
     "Barbour 2026-06", "Titrating + medium-impact SE -> slow uptitration; reassess after a 1-week adjustment."),
    # seizure-free branch (layer 3)
    ("NS", "NS · No-Seizures table", 3, "seizure_free", None,
     "Barbour 2026-06-25; follow-up fixed 2026-07-08 (Kristen D4)",
     "Action by phase x side-effect level when seizure-free. 'Continue' cells schedule no follow-up (Kristen D4)."),
    ("R14", "R14 · Free + intolerable SE", 3, "seizure_free", "switch_medication",
     "legacy", "Seizure-free but intolerable side effects -> switch."),
    ("R15", "R15 · Family wants different med", 3, "seizure_free", "insufficient_data",
     "family preference", "Family would like a different medicine (no clinical trigger) -> offer switch options for physician."),
    ("R16", "R16 · Free titrating, family slower", 3, "seizure_free", "slower_uptitration",
     "family preference", "Seizure-free, titrating, family prefers slower -> slower uptitration."),
    ("R17", "R17 · Free + manageable SE", 3, "seizure_free", "no_change",
     "legacy", "Seizure-free + manageable side effects -> no change; reassess as body adjusts."),
    ("R18", "R18 · Free + no SE", 3, "seizure_free", "no_change",
     "legacy", "Seizure-free, no side effects -> no change; recheck on schedule if still titrating."),
]

# Inputs (layer 0) + branch hubs (layer 2)
SURVEY = add_node("survey", "Between-visit survey", "input", 0,
                  "Interval check-in: seizures since last visit? side effects (level)? phase? adherence? "
                  "duration/frequency? rash? dose goal? (kristen/BetweenVisit_SurveyDraft)")
BR_BT = add_node("branch_bt", "BREAKTHROUGH seizures", "branch", 2, "One or more breakthrough seizures this interval.")
BR_SF = add_node("branch_sf", "SEIZURE-FREE", "branch", 2, "No seizures this interval.")
add_edge(SURVEY, BR_BT, "seizure(s)")
add_edge(SURVEY, BR_SF, "none")

for rid, lab, layer, branch, cat, prov, detail in RULES:
    nid = add_node(f"rule_{rid}", lab, "rule", layer, f"{detail}\n\nProvenance: {prov}",
                   branch=branch, provenance=prov)
    if layer == 1:
        add_edge(SURVEY, nid, "gate")
    elif branch == "breakthrough":
        add_edge(BR_BT, nid, "")
    elif branch == "seizure_free":
        add_edge(BR_SF, nid, "")
    if cat:
        add_edge(nid, f"cat_{cat}", "-> ")

# --- Data tables + dose ladder (layer 4) ------------------------------------
levels = ["L0 none", "L1 small", "L2 med-tol", "L3 med-alot", "L4 big", "L5 not-fit"]
lvl_ids = [add_node(f"lvl{i}", levels[i], "level", 4, f"Side-effect impact level {i}.") for i in range(6)]
phase_ids = {ph: add_node(f"phase_{ph}", ph, "phase", 4, f"Titration phase: {ph}.")
             for ph in ("titrating", "holding", "steady")}

ns_tbl = add_node("tbl_no_seizures", "No-Seizures table", "table", 4,
                  f"Action by phase x level when seizure-free. verified={NS.get('verified')}")
add_edge("rule_NS", ns_tbl, "uses")
for p in phase_ids.values():
    add_edge(ns_tbl, p, "")
for l in lvl_ids:
    add_edge(ns_tbl, l, "")
# No-Seizures table -> the recommendations its action tokens can yield.
_NS_TOKEN_CAT = {"continue": "no_change", "no_change": "no_change", "slower": "slower_uptitration",
                 "hold": "hold", "lower": "lower_dose", "switch": "switch_medication"}
for _acts in (NS.get("actions_by_phase_level") or {}).values():
    for _tok in _acts:
        _c = _NS_TOKEN_CAT.get(_tok)
        if _c:
            add_edge(ns_tbl, f"cat_{_c}", "yields")

_CAT_HINT = {"no_change": "stay", "escalate": "increase", "slower_uptitration": "slow",
             "hold": "hold", "add_medication": "add med", "lower_dose": "lower",
             "switch_medication": "switch", "insufficient_data": "defer"}
TIER_LABEL = {"non_urgent": "Breakthrough · non-urgent", "urgent": "Breakthrough · urgent",
              "emergent": "Breakthrough · emergent"}
for tier, phases in (BT.get("tables") or {}).items():
    lines = []
    for ph, rows in phases.items():
        lines.append(f"{ph}: " + " | ".join(_CAT_HINT.get(c.get("category"), c.get("category")) for c in rows))
    tid = add_node(f"tbl_bt_{tier}", TIER_LABEL.get(tier, tier), "table", 4,
                   "\n".join(lines) + f"\nverified={BT.get('verified')}")
    add_edge("rule_BT", tid, "uses")
    for ph in phases:
        add_edge(tid, phase_ids[ph], "")
    for l in lvl_ids:
        add_edge(tid, l, "")
    # table -> every recommendation its cells can yield (incl. at-max category),
    # so reachability from a dispatcher rule (BT/NS) to a recommendation is complete.
    _cats = set()
    for rows in phases.values():
        for c in rows:
            _cats.add(c.get("category"))
            if c.get("at_max_category"):
                _cats.add(c["at_max_category"])
    for c in _cats:
        add_edge(tid, f"cat_{c}", "yields")
if URG:
    uid = add_node("tbl_urgency", "Urgency thresholds", "table", 4,
                   f"Per seizure-type duration/frequency cutoffs -> breakthrough tier. verified={URG.get('verified')}")
    add_edge("rule_BT", uid, "tier from")

# dose ladder hub + drugs + bands + labs
LAD = add_node("ladder_hub", "Dose ladder", "table", 4,
               "Per drug x band explicit rungs (min-maint -> eff-max -> max; high range -> † labs). "
               "Barbour final_doses v6, DRAFT.")
add_edge("cat_escalate", LAD, "next dose from")
add_edge("cat_lower_dose", LAD, "next dose from")
for key, drug in ASM.items():
    if not isinstance(drug, dict) or "display_name" not in drug:
        continue
    disp = drug.get("display_name", key)
    did = add_node(f"drug_{key}", disp.split(" (")[0], "drug", 4,
                   f"{disp}\nSJS risk: {bool(drug.get('sjs_risk'))}\n"
                   + (drug.get("SAFETY_PRIORITY") or drug.get("key_safety") or "")[:200])
    for bi, band in enumerate((LADDER.get("bands_by_drug") or {}).get(key, [])):
        mm = (band.get("min_maintenance_dose") or {}).get("raw")
        em = (band.get("effective_max") or {}).get("raw")
        am = band.get("max_absolute_mg_per_day")
        bid = add_node(f"band_{key}_{bi}", f"{disp.split(' (')[0]} · {band.get('band_label','')}", "band", 4,
                       f"{band.get('band_label','')}\ninitial {(band.get('initial_dose') or {}).get('raw')}\n"
                       f"min-maint {mm} -> eff-max {em} -> max {am} mg/d\n"
                       f"up {len(band.get('up_increments') or [])} · higher(†) {len(band.get('high_increments_often_not_tolerated') or [])}")
        add_edge(did, bid, "")
        add_edge(bid, LAD, "")
    labs = (LADDER.get("high_dose_labs_by_drug") or {}).get(key)
    if labs:
        lid = add_node(f"labs_{key}", f"† labs {disp.split(' (')[0]}", "labs", 4,
                       "High-dose labs:\n- " + "\n- ".join(labs))
        add_edge(did, lid, "")

# --- Validation (layer 6): gold cases + PI review ---------------------------
if GOLD.get("cases"):
    from collections import Counter
    by_cat = Counter(c.get("gold_standard_category") for c in GOLD["cases"])
    gid = add_node("gold", f"Gold cases (n={len(GOLD['cases'])})", "validation", 6,
                   "Frozen expected outputs:\n" + "\n".join(f"- {k}: {v}" for k, v in by_cat.items()))
    # link each gold bucket to its recommendation category
    for k in by_cat:
        if f"cat_{k}" in _seen:
            add_edge(gid, f"cat_{k}", "expects")
if REVIEW:
    pid = add_node("pi_review", "PI validation review", "validation", 6,
                   f"Reviewed by {REVIEW.get('reviewed_by')}\ntotal={REVIEW.get('total_cases')} "
                   f"agreement={REVIEW.get('agreement')}")
    _CASE_TO = {"B1": "rule_BT", "B2": "rule_BT", "B3": "rule_BT", "D4": "rule_NS"}
    for f in REVIEW.get("flagged_cases", []):
        cid = str(f.get("case"))
        nid = add_node(f"case_{cid}", f"Flagged {cid}", "case", 6,
                       f"{f.get('scenario')}\nengine: {f.get('engine')}\ncomment: {f.get('comment')}\n\n"
                       f"RESOLUTION: {f.get('resolution')}")
        add_edge(pid, nid, "")
        if _CASE_TO.get(cid):
            add_edge(nid, _CASE_TO[cid], "fixed in")


# ---------------------------------------------------------------------------
# DETERMINISTIC LAYOUT: spread each layer into readable sub-lanes so the big
# layer-4 cluster (tables/levels/phases/drugs/bands/labs) does not stack into
# one tall column with converging edges. Positions are written into the JSON;
# the viewer just reads x,y (still draggable).
# ---------------------------------------------------------------------------
# ordered list of (column-key, [types in this column]) left -> right
COLUMNS = [
    ("inputs", ["input"]),
    ("safety", ["rule@1"]),
    ("branch", ["branch"]),
    ("rules", ["rule@3"]),
    ("axes", ["level", "phase"]),
    ("tables", ["table"]),
    ("drugs", ["drug"]),
    ("bands", ["band", "labs"]),
    ("recs", ["recommendation"]),
    ("valid", ["validation", "case"]),
]
COLW, ROWH = 300, 46


def _colkey(n):
    if n["type"] == "rule":
        return "rule@1" if n["layer"] == 1 else "rule@3"
    return n["type"]


col_x = {}
x = 0
for key, types in COLUMNS:
    members = [n for n in nodes if _colkey(n) in types]
    if not members:
        continue
    for i, n in enumerate(members):
        n["x"] = x
        n["y"] = i * ROWH - len(members) * ROWH / 2
    col_x[key] = x
    x += COLW

# tag low-value many-to-many "axis" edges so the viewer can hide them by default
_AXIS = {"level", "phase"}
_by_id = {n["id"]: n for n in nodes}
for e in edges:
    a, b = _by_id.get(e["source"]), _by_id.get(e["target"])
    if not a or not b:
        continue
    minor = ((a["type"] == "table" and b["type"] in _AXIS) or
             (b["type"] == "table" and a["type"] in _AXIS) or
             (b["id"] == "ladder_hub" and a["type"] == "band"))
    e["minor"] = bool(minor)

graph = {"nodes": nodes, "edges": edges, "layers": LAYERS, "columns": [c[0] for c in COLUMNS],
         "meta": {"generated_from": "engine JSON + decision.py rules (source of truth)",
                  "note": "DRAFT / 100% simulated. Confidential - in-folder only."}}
with open(os.path.join(HERE, "knowledge_graph.json"), "w") as fh:
    json.dump(graph, fh, indent=2, ensure_ascii=False)

# --- self-contained interactive HTML ----------------------------------------
HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Aim 2 — Knowledge Graph</title>
<style>
 :root{color-scheme:dark}*{box-sizing:border-box}
 body{margin:0;font:13px/1.45 system-ui,Segoe UI,Roboto,sans-serif;background:#0e1320;color:#e8ecf3}
 #wrap{display:flex;height:100vh}
 #side{width:300px;flex:none;padding:14px 16px;background:#151b2a;overflow:auto;border-right:1px solid #263149}
 h1{font-size:15px;margin:0 0 2px}.sub{color:#93a0b8;font-size:11px;margin-bottom:10px}
 #search{width:100%;padding:7px 9px;margin:6px 0 10px;border-radius:6px;border:1px solid #263149;background:#0e1320;color:#e8ecf3}
 .sec{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#7f8ca6;margin:12px 0 6px}
 .row{display:flex;align-items:center;gap:8px;margin:4px 0;cursor:pointer;user-select:none}
 .row .sw{width:13px;height:13px;border-radius:3px;flex:none}.row.off{opacity:.3}
 label.tg{display:flex;gap:7px;align-items:center;margin:5px 0;cursor:pointer}
 #detail{margin-top:12px;padding:10px;background:#0e1320;border:1px solid #263149;border-radius:8px;white-space:pre-wrap;font-size:12px;min-height:70px}
 #detail b{color:#7fb0ff}
 canvas{flex:1;display:block;cursor:grab;background:radial-gradient(circle at 30% 15%,#131b2c,#0b0f18)}
 .hint{color:#6b7688;font-size:11px;margin-top:10px}
</style></head><body><div id="wrap">
<div id="side">
 <h1>Aim 2 — Knowledge Graph</h1>
 <div class="sub">Whole engine, left→right. <b>Hover or click a node</b> to light up just its links (the rest fade). DRAFT / simulated.</div>
 <input id="search" placeholder="search nodes…">
 <div class="sec">View</div>
 <label class="tg"><input type="checkbox" id="focus" checked> Focus mode (dim unrelated)</label>
 <label class="tg"><input type="checkbox" id="axis"> Show table↔level/phase & band→ladder links</label>
 <div class="sec">Columns</div><div id="layers"></div>
 <div class="sec">Node types</div><div id="legend"></div>
 <div id="detail">Hover a node to preview; click to pin its details. Drag background to pan; scroll to zoom.</div>
 <div class="hint">Generated from engine JSON + decision.py — do not hand-edit.</div>
</div>
<canvas id="c"></canvas></div>
<script>
const G=__DATA__;const byId={};G.nodes.forEach(n=>byId[n.id]=n);
const TYPE_COLOR={input:"#8fd694",branch:"#7fb0ff",rule:"#c586ff",table:"#e06c9f",
 level:"#88d3c8",phase:"#b48ead",drug:"#4ec9b0",band:"#3f8ef0",labs:"#d7a24c",
 recommendation:"#f2c14e",validation:"#ffb454",case:"#ff6b6b"};
// adjacency
const adj={};G.nodes.forEach(n=>adj[n.id]=new Set());
G.edges.forEach(e=>{if(adj[e.source]&&adj[e.target]){adj[e.source].add(e.target);adj[e.target].add(e.source);}});
const offType={},offCol={};let showAxis=false,focusMode=true;
const cv=document.getElementById('c'),ctx=cv.getContext('2d');
let scale=0.8,ox=70,oy=0,W,H;
function resize(){W=cv.width=cv.clientWidth*devicePixelRatio;H=cv.height=cv.clientHeight*devicePixelRatio;}
addEventListener('resize',resize);
function vis(n){return !offType[n.type]&&!offCol[n.layer];}
function nodeW(n){return Math.min(228,86+n.label.length*6.1);}
let hov=null,sel=null;
function active(){return sel||hov;}
function edgeShown(e){ if(e.minor&&!showAxis){ const A=active(); return A&&(e.source===A.id||e.target===A.id);} return true; }
function draw(){
 ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,W,H);
 ctx.setTransform(scale*devicePixelRatio,0,0,scale*devicePixelRatio,ox*devicePixelRatio,H/2+oy*devicePixelRatio);
 const A=active();
 // column headers
 ctx.font='bold 14px system-ui';ctx.textBaseline='alphabetic';ctx.fillStyle='#5b6b86';
 const seenCols={};G.nodes.forEach(n=>{if(seenCols[n.x]===undefined)seenCols[n.x]=n.layer;});
 // edges
 for(const e of G.edges){const a=byId[e.source],b=byId[e.target];if(!a||!b||!vis(a)||!vis(b)||!edgeShown(e))continue;
   const related=A&&(e.source===A.id||e.target===A.id);
   if(focusMode&&A&&!related){ctx.strokeStyle='rgba(120,140,170,.05)';ctx.lineWidth=.6;}
   else if(related){ctx.strokeStyle='rgba(150,190,255,.85)';ctx.lineWidth=1.7;}
   else{ctx.strokeStyle='rgba(130,150,180,.13)';ctx.lineWidth=.7;}
   const aw=nodeW(a),x1=a.x+aw/2,y1=a.y,x2=b.x-nodeW(b)/2,y2=b.y,mx=(x1+x2)/2;
   ctx.beginPath();ctx.moveTo(x1,y1);ctx.bezierCurveTo(mx,y1,mx,y2,x2,y2);ctx.stroke();
 }
 // nodes
 ctx.textBaseline='middle';
 for(const n of G.nodes){if(!vis(n))continue;
   const related=A&&(n===A||adj[A.id].has(n.id));const faded=focusMode&&A&&!related;
   const w=nodeW(n),h=32,x=n.x-w/2,y=n.y-h/2,c=TYPE_COLOR[n.type]||'#889';
   ctx.globalAlpha=faded?0.25:1;
   ctx.fillStyle=(n===A)?'#20304f':'#141b2b';ctx.strokeStyle=c;ctx.lineWidth=(n===A)?2.6:1.4;
   rr(x,y,w,h,8);ctx.fill();ctx.stroke();
   ctx.fillStyle=c;ctx.beginPath();ctx.arc(x+11,n.y,4,0,6.29);ctx.fill();
   ctx.fillStyle='#e8ecf3';ctx.font='11px system-ui';let t=n.label;if(t.length>30)t=t.slice(0,29)+'…';
   ctx.fillText(t,x+22,n.y);ctx.globalAlpha=1;
 }
}
function rr(x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);
 ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}
let drag=null,pan=false,px,py;
function toWorld(mx,my){return{x:(mx*devicePixelRatio-ox*devicePixelRatio)/(scale*devicePixelRatio),
  y:(my*devicePixelRatio-H/2-oy*devicePixelRatio)/(scale*devicePixelRatio)};}
function pick(mx,my){const w=toWorld(mx,my);for(const n of G.nodes){if(!vis(n))continue;
  const ww=nodeW(n);if(Math.abs(n.x-w.x)<ww/2&&Math.abs(n.y-w.y)<17)return n;}return null;}
cv.addEventListener('mousemove',ev=>{if(drag){const w=toWorld(ev.offsetX,ev.offsetY);drag.x=w.x;drag.y=w.y;return;}
  if(pan){ox+=ev.offsetX-px;oy+=ev.offsetY-py;px=ev.offsetX;py=ev.offsetY;return;}
  const n=pick(ev.offsetX,ev.offsetY);hov=n;cv.style.cursor=n?'pointer':'grab';if(n&&!sel)showDetail(n);});
cv.addEventListener('mousedown',ev=>{const n=pick(ev.offsetX,ev.offsetY);
  if(n){drag=n;sel=n;showDetail(n);}else{sel=null;pan=true;px=ev.offsetX;py=ev.offsetY;}});
addEventListener('mouseup',()=>{drag=null;pan=false;});
cv.addEventListener('wheel',ev=>{ev.preventDefault();scale=Math.max(0.25,Math.min(2.4,scale*(ev.deltaY<0?1.1:.9)));},{passive:false});
function showDetail(n){document.getElementById('detail').innerHTML='<b>'+n.label+'</b>  ['+n.type+' · '+(G.layers[n.layer]||'')+']\n\n'+(n.detail||'');}
document.getElementById('focus').onchange=e=>focusMode=e.target.checked;
document.getElementById('axis').onchange=e=>showAxis=e.target.checked;
// column filter (by layer index/label)
const layEl=document.getElementById('layers');
(G.layers||[]).forEach((lab,i)=>{const d=document.createElement('div');d.className='row';
 d.innerHTML='<span class="sw" style="background:#41506e"></span>'+(i+1)+'. '+lab;
 d.onclick=()=>{offCol[i]=!offCol[i];d.classList.toggle('off',offCol[i]);};layEl.appendChild(d);});
const legEl=document.getElementById('legend');
[...new Set(G.nodes.map(n=>n.type))].forEach(t=>{const d=document.createElement('div');d.className='row';
 d.innerHTML='<span class="sw" style="background:'+(TYPE_COLOR[t]||'#889')+'"></span>'+t;
 d.onclick=()=>{offType[t]=!offType[t];d.classList.toggle('off',offType[t]);};legEl.appendChild(d);});
document.getElementById('search').addEventListener('input',ev=>{const q=ev.target.value.toLowerCase();if(!q)return;
 const hit=G.nodes.find(n=>n.label.toLowerCase().includes(q)||(n.detail||'').toLowerCase().includes(q));
 if(hit){sel=hit;ox=-hit.x*scale+340;oy=-hit.y*scale;showDetail(hit);}});
resize();(function loop(){draw();requestAnimationFrame(loop);})();
</script></body></html>"""
with open(os.path.join(HERE, "knowledge_graph.html"), "w") as fh:
    fh.write(HTML.replace("__DATA__", json.dumps(graph, ensure_ascii=False)))

from collections import Counter
print(f"nodes={len(nodes)} edges={len(edges)} minor_edges={sum(1 for e in edges if e.get('minor'))}")
print("columns x:", {k: col_x[k] for k in col_x})
print("wrote knowledge_graph.json and knowledge_graph.html")
