#!/usr/bin/env python3
"""
Generate an Obsidian Canvas (.canvas) visual map of the Aim 2 ASM titration
decision flow — DATA-DRIVEN so a single edit flows through to the picture.

EDIT FLOW
  • Decision tables / labels  -> edit  flow_source.json
  • Engine numeric thresholds  -> edit  ../aim2_engine/data/titration_thresholds.json
  Then run:  python3 build_canvas.py   -> rewrites decision_flow.canvas

LAYOUT (reader-friendly, 2024 rewrite)
  The map reads top-to-bottom: a compact DECISION SPINE at the top shows the
  flow; the detailed REFERENCE TABLES sit below in clearly separated, auto-sized
  groups. The spine points at tables by number (①②③④) instead of long
  cross-canvas edges, so nothing crosses.

SOURCE OF TRUTH
  The engine is JSON-driven. This canvas is a generated VIEW. Engine-threshold
  cards are read straight from titration_thresholds.json so they cannot drift.
  Never hand-edit the .canvas — edit the source files, regenerate.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(HERE, "..", "aim2_engine", "data")
SRC = json.load(open(os.path.join(HERE, "flow_source.json")))
TH = json.load(open(os.path.join(_DATA, "titration_thresholds.json")))


def _load(name):
    """Read an engine data file (source of truth); {} if absent so the canvas
    still builds. These sections are generated VIEWS - never hand-edited."""
    p = os.path.join(_DATA, name)
    return json.load(open(p)) if os.path.isfile(p) else {}


BT = _load("breakthrough_decision_tables.json")      # reconciled 2026-07-08
LADDER = _load("dose_titration_ladder.json")         # Barbour final_doses v6

NODES, EDGES = [], []
_uid = [0]


def nid(p="n"):
    _uid[0] += 1
    return f"{p}{_uid[0]}"


def node(text, x, y, w=260, h=110, color=None):
    n = {"id": nid(), "type": "text", "x": int(x), "y": int(y),
         "width": int(w), "height": int(h), "text": text}
    if color:
        n["color"] = color
    NODES.append(n)
    return n["id"]


def group_around(label, ids, color=None, pad=28, top=44):
    """Create a group node sized to enclose the given child nodes. `top` adds
    headroom so the group's title bar never overlaps the first child."""
    cs = [n for n in NODES if n["id"] in ids]
    x0 = min(c["x"] for c in cs) - pad
    y0 = min(c["y"] for c in cs) - pad - top
    x1 = max(c["x"] + c["width"] for c in cs) + pad
    y1 = max(c["y"] + c["height"] for c in cs) + pad
    g = {"id": nid("g"), "type": "group", "label": label,
         "x": int(x0), "y": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)}
    if color:
        g["color"] = color
    NODES.append(g)
    return g["id"]


def edge(a, b, label=None, fs="bottom", ts="top", color=None):
    e = {"id": nid("e"), "fromNode": a, "toNode": b, "fromSide": fs, "toSide": ts}
    if label:
        e["label"] = label
    if color:
        e["color"] = color
    EDGES.append(e)


# Obsidian colors: 1=red 2=orange 3=yellow 4=green 5=cyan 6=purple
RED, ORANGE, YELLOW, GREEN, CYAN, PURPLE = "1", "2", "3", "4", "5", "6"
LVL_COLORS = [GREEN, GREEN, YELLOW, YELLOW, ORANGE, RED]
DUR = {1: "<1 min", 2: "1–3 min", 3: "≥3 min"}

COL = 320   # spine column pitch
ROW = 150   # spine row pitch

# ============================================================================
# LEGEND  (top banner)
# ============================================================================
node("🗺  AIM 2 — ASM TITRATION DECISION FLOW\n\n"
     "Read top→bottom. The SPINE (top) is the flow; the TABLES (below) are the detail it points to (①②③④).\n"
     "Colour = direction:  🟢 stay/continue   🟡 watch / slow / labs   🟠 hold / add a med   🔴 switch / urgent.\n"
     "Generated VIEW — edit flow_source.json or titration_thresholds.json, then run build_canvas.py. Never hand-edit this canvas.",
     0, -260, 4 * COL + 40, 150, color=PURPLE)

# ============================================================================
# DECISION SPINE  (compact flow, top)
# ============================================================================
y0 = -40
start = node("①  START\nInterval check-in (survey)\n\nSeizures since last visit?\nSide effects? Which phase?",
             0, y0 + ROW, 260, 130, color=CYAN)

# branch row
no_sz = node("NO SEIZURES", COL, y0, 220, 70, color=GREEN)
breakthrough = node("BREAKTHROUGH\nSEIZURE(S)", COL, y0 + 2 * ROW, 220, 70, color=ORANGE)
edge(start, no_sz, label="none", fs="right", ts="left")
edge(start, breakthrough, label="seizure(s)", fs="right", ts="left")

# no-seizures path
phase = node("PHASE — which barometer?\n• Still Titrating\n• Adjustment — Holding\n• Steady State",
             2 * COL, y0 - 10, 260, 110, color=PURPLE)
ns_action = node("→ look up action in\nTABLE ①  (No-Seizures matrix)\nby phase × side-effect level",
                 3 * COL, y0 - 10, 260, 110, color=GREEN)
edge(no_sz, phase, fs="right", ts="left")
edge(phase, ns_action, fs="right", ts="left")

# breakthrough path
triage = node("Triage urgency\n→ TABLE ②  (per seizure type)",
              2 * COL, y0 + 2 * ROW - 10, 260, 90, color=ORANGE)
edge(breakthrough, triage, fs="right", ts="left")
nonurg = node("NON-URGENT\n→ med-management (TABLE ①)\n→ if dose change weighed: TABLE ④",
              3 * COL, y0 + 1.6 * ROW, 260, 90, color=GREEN)
urg = node("URGENT\n→ safety flag to physician\n→ thresholds: TABLE ③",
           3 * COL, y0 + 2.5 * ROW, 260, 90, color=RED)
edge(triage, nonurg, label="non-urgent", fs="right", ts="left")
edge(triage, urg, label="urgent", fs="right", ts="left")

spine_ids = [start, no_sz, breakthrough, phase, ns_action, triage, nonurg, urg]
group_around("DECISION SPINE  (start here)", spine_ids, color=CYAN, pad=34, top=52)

# ============================================================================
# REFERENCE TABLES  (stacked below, clearly separated)
# ============================================================================
LEFT = 0
cursor = 660     # running y; clears the spine group below its lowest node
GAP = 130        # vertical breathing room between sections

# ---- TABLE ① : NO-SEIZURES action matrix -----------------------------------
levels = SRC["side_effect_levels"]
ncols = len(levels)
cw, gap, ch = 200, 16, 84
ids = []
hy = cursor
for i, lbl in enumerate(levels):
    x = LEFT + i * (cw + gap)
    ids.append(node(lbl, x, hy, cw, 52, color=LVL_COLORS[i]))
ry = hy + 92
for r, (ph, actions) in enumerate(SRC["no_seizures_table"].items()):
    band_y = ry + r * (ch + 70)
    ids.append(node("▸ " + ph, LEFT, band_y, ncols * (cw + gap) - gap, 30, color=PURPLE))
    for i, act in enumerate(actions):
        x = LEFT + i * (cw + gap)
        ids.append(node(act, x, band_y + 40, cw, ch, color=LVL_COLORS[i]))
foot_y = ry + 3 * (ch + 70)
ids.append(node(SRC["no_seizures_footnotes"], LEFT, foot_y, ncols * (cw + gap) - gap, 96, color=YELLOW))
g1 = group_around("①  NO SEIZURES — action by phase × side-effect level (read across)",
                  ids, color=GREEN)
cursor = next(n for n in NODES if n["id"] == g1)["y"] + next(n for n in NODES if n["id"] == g1)["height"] + GAP

# ---- TABLE ② : breakthrough urgency thresholds -----------------------------
ids = []
hy = cursor
node_type_w, col_w2 = 380, 300
ids.append(node("Seizure type", LEFT, hy, node_type_w, 46, color=ORANGE))
ids.append(node("NON-URGENT  (duration & frequency)", LEFT + node_type_w + gap, hy, col_w2, 46, color=GREEN))
ids.append(node("URGENT  (duration OR frequency)", LEFT + node_type_w + gap + col_w2 + gap, hy, col_w2, 46, color=RED))
ry = hy + 60
last_non = last_urg = None
for i, (typ, nonu, urg2) in enumerate(SRC["urgency_table"]):
    y = ry + i * 64
    ids.append(node(typ, LEFT, y, node_type_w, 56, color=ORANGE))
    last_non = node(nonu, LEFT + node_type_w + gap, y, col_w2, 56, color=GREEN); ids.append(last_non)
    last_urg = node(urg2, LEFT + node_type_w + gap + col_w2 + gap, y, col_w2, 56, color=RED); ids.append(last_urg)
oy = ry + len(SRC["urgency_table"]) * 64 + 16
no_out = node("NON-URGENT → medication-management flow (TABLE ①)",
              LEFT + node_type_w + gap, oy, col_w2, 56, color=GREEN); ids.append(no_out)
ur_out = node("URGENT → safety flag (esp. whole-body ≥3 min = danger)",
              LEFT + node_type_w + gap + col_w2 + gap, oy, col_w2, 56, color=RED); ids.append(ur_out)
edge(last_non, no_out)
edge(last_urg, ur_out)
g2 = group_around("②  BREAKTHROUGH — urgency thresholds (per seizure type)", ids, color=ORANGE)
cursor = next(n for n in NODES if n["id"] == g2)["y"] + next(n for n in NODES if n["id"] == g2)["height"] + GAP

# ---- TABLE ③ : engine thresholds (read straight from JSON) -----------------
wb, ef, co, sw, fu = (TH["whole_body_danger"], TH["escalate_faster"],
                      TH["continue_ok"], TH["switch_when_not_helpful"], TH["followup"])
cards = [
    (f"whole_body_danger\nGeneralized T-C, duration {DUR[wb['min_duration_rank']]} → URGENT", RED),
    (f"escalate_faster\n>{ef['rule_a_freq_per_week_gt']}/wk @ {DUR[ef['rule_a_min_duration_rank']]}  OR  "
     f">{ef['rule_b_freq_per_week_gt']}/wk @ {DUR[ef['rule_b_min_duration_rank']]}\n→ increase faster (every {TH['faster_cadence_interval_days']} days)", ORANGE),
    (f"continue_ok\nAll {DUR[co['still_titrating_max_duration_rank_lt']-1]} while titrating → stay course", GREEN),
    (f"switch_when_not_helpful\nUnchanged ≥ {sw['unchanged_min_weeks']} wks  OR  worse ≥ {sw['worse_min_weeks']} wks → switch", YELLOW),
    (f"follow-up\nTitrating: {fu['while_titrating_weeks']} wks • tolerable side effect: {fu['tolerable_side_effect_reassess_weeks']} wk", CYAN),
]
ids = []
verified = "VERIFIED" if TH.get("verified") else "DRAFT — unverified"
tcw = 360
for i, (txt, col) in enumerate(cards):
    ids.append(node(txt, LEFT + i * (tcw + gap), cursor, tcw, 96, color=col))
g3 = group_around(f"③  ENGINE THRESHOLDS  (titration_thresholds.json — {verified})", ids, color=PURPLE)
cursor = next(n for n in NODES if n["id"] == g3)["y"] + next(n for n in NODES if n["id"] == g3)["height"] + GAP

# ---- TABLE ④ : family choice ------------------------------------------------
fc = SRC["family_choice"]
ids = []
q = node(fc["question"], LEFT + 360, cursor, 320, 120, color=CYAN); ids.append(q)
yes = node(fc["yes"], LEFT, cursor + 170, 380, 100, color=GREEN); ids.append(yes)
no = node(fc["no"], LEFT + 720, cursor + 170, 380, 100, color=ORANGE); ids.append(no)
shared = node(fc["shared"], LEFT + 360, cursor + 300, 320, 70, color=PURPLE); ids.append(shared)
edge(q, yes, label="YES", fs="left", ts="top")
edge(q, no, label="NO", fs="right", ts="top")
edge(yes, shared, fs="bottom", ts="left")
edge(no, shared, fs="bottom", ts="right")
g4 = group_around("④  FAMILY CHOICE  (still seizing + side effects present)", ids, color=CYAN)
cursor = next(n for n in NODES if n["id"] == g4)["y"] + next(n for n in NODES if n["id"] == g4)["height"] + GAP

# ---- TABLE ⑤ : BREAKTHROUGH decision tables (reconciled 2026-07-08) ---------
# Read straight from the engine JSON so the picture cannot drift from the rules.
_CAT_SHORT = {
    "no_change": ("stay", GREEN), "escalate": ("↑ increase", YELLOW),
    "slower_uptitration": ("slow", YELLOW), "hold": ("hold", ORANGE),
    "add_medication": ("＋ add med", ORANGE), "lower_dose": ("↓ lower", ORANGE),
    "switch_medication": ("switch", RED), "insufficient_data": ("defer", CYAN),
}
_TIER_COLOR = {"non_urgent": GREEN, "urgent": ORANGE, "emergent": RED}
if BT.get("tables"):
    lvl_hdrs = ["L0", "L1", "L2", "L3", "L4", "L5"]
    cw5, ch5, gp5 = 118, 62, 12
    phase_w = 150
    ids = []
    hy = cursor
    ids.append(node("BREAKTHROUGH — action by urgency tier × phase × side-effect level.\n"
                    "Cells read from breakthrough_decision_tables.json (DRAFT). Hover a cell in the "
                    "engine output for the verbatim steps.", LEFT, hy, phase_w + 6 * (cw5 + gp5) - gp5, 60, color=ORANGE))
    ry = hy + 74
    row = 0
    for tier in ("non_urgent", "urgent", "emergent"):
        tbl = BT["tables"].get(tier, {})
        # tier band + level headers
        ids.append(node(tier.replace("_", "-").upper(), LEFT, ry + row * (ch5 + 8),
                        phase_w, ch5, color=_TIER_COLOR[tier]))
        for j, h in enumerate(lvl_hdrs):
            ids.append(node(h, LEFT + phase_w + gp5 + j * (cw5 + gp5), ry + row * (ch5 + 8),
                            cw5, 30, color=LVL_COLORS[j]))
        row += 1
        for phase in ("titrating", "holding", "steady"):
            rows_ = tbl.get(phase, [])
            ids.append(node(phase, LEFT, ry + row * (ch5 + 8), phase_w, ch5, color=PURPLE))
            for j, cell in enumerate(rows_):
                cat = cell.get("at_max_category") or cell.get("category")  # show primary
                cat = cell.get("category")
                label, col = _CAT_SHORT.get(cat, (cat, CYAN))
                extra = ""
                if cell.get("labs"): extra += " •labs"
                if cell.get("at_max_category"): extra += f"\n(max→{_CAT_SHORT.get(cell['at_max_category'],(cell['at_max_category'],''))[0]})"
                ids.append(node(label + extra, LEFT + phase_w + gp5 + j * (cw5 + gp5),
                                ry + row * (ch5 + 8), cw5, ch5, color=col))
            row += 1
    g5 = group_around("⑤  BREAKTHROUGH DECISION TABLES  (tier × phase × level — reconciled with Kristen 2026-07-08)",
                      ids, color=ORANGE)
    cursor = next(n for n in NODES if n["id"] == g5)["y"] + next(n for n in NODES if n["id"] == g5)["height"] + GAP

# ---- TABLE ⑥ : DOSE LADDER summary (Barbour final_doses v6) -----------------
if LADDER.get("bands_by_drug"):
    ids = []
    hy = cursor
    ids.append(node("DOSE TITRATION LADDER — exact rungs per drug × weight/age band "
                    "(dose_titration_ladder.json, DRAFT). The engine reads the explicit rungs; it "
                    "does not interpolate. Reaching the HIGH range (often less tolerated) orders the "
                    "† labs. Flags below are cells for PI to confirm.",
                    LEFT, hy, 3 * (300 + 16) - 16, 74, color=PURPLE));
    ry = hy + 90
    dcw, dch, dgap = 300, 96, 16
    drugs = sorted(LADDER["bands_by_drug"].keys())
    for i, drug in enumerate(drugs):
        bands = LADDER["bands_by_drug"][drug]
        col = i % 3; rr = i // 3
        txt = f"{drug}\n{len(bands)} band(s)"
        # summarize the first band's min-maint → eff-max → abs-max as an example
        b0 = bands[0]
        mm = (b0.get("min_maintenance_dose") or {}).get("raw")
        em = (b0.get("effective_max") or {}).get("raw")
        am = b0.get("max_absolute_mg_per_day")
        txt += f"\ne.g. {b0.get('band_label','')}:\nmin-maint {mm} → eff-max {em} → max {am} mg/d"
        ids.append(node(txt, LEFT + col * (dcw + dgap), ry + rr * (dch + dgap), dcw, dch, color=CYAN))
    # data-quality flags card
    flags = LADDER.get("data_quality_flags") or []
    if flags:
        fy = ry + ((len(drugs) + 2) // 3) * (dch + dgap)
        ftxt = "⚠ DATA-QUALITY FLAGS (PI to confirm — not auto-corrected):\n" + "\n".join(
            f"• {f.get('drug')} {f.get('band')}: {f.get('issue')}" for f in flags)
        ids.append(node(ftxt, LEFT, fy, 3 * (dcw + dgap) - dgap, 150, color=YELLOW))
    g6 = group_around("⑥  DOSE LADDER  (per drug × band; exact rungs — Barbour final_doses v6, DRAFT)",
                      ids, color=PURPLE)

# ============================================================================
canvas = {"nodes": NODES, "edges": EDGES}
out = os.path.join(HERE, "decision_flow.canvas")
with open(out, "w") as f:
    json.dump(canvas, f, indent=2)
print(f"wrote {out}  ({len(NODES)} nodes, {len(EDGES)} edges)")
