#!/usr/bin/env python3
"""
export_graph.py
---------------
Export the Aim 2 knowledge graph (knowledge_graph.json, produced by
build_knowledge_graph.py) into standard graph formats so it can be loaded into
Neo4j, NetworkX, Gephi, yEd, Cytoscape, etc. All formats are generated from the
SAME source JSON so they stay in sync.

Outputs (this folder):
  knowledge_graph.cypher   - Neo4j import script (MERGE nodes + relationships)
  knowledge_graph.graphml  - GraphML (NetworkX / Gephi / yEd / Cytoscape)

Usage:
  python3 export_graph.py
  # Neo4j:    cat knowledge_graph.cypher | cypher-shell -u neo4j -p <pw>
  # NetworkX: import networkx as nx; G = nx.read_graphml("knowledge_graph.graphml")

CONFIDENTIAL - DRAFT / 100% simulated values; stays in this folder.
"""
import json
import os
from xml.sax.saxutils import escape as xesc

HERE = os.path.dirname(os.path.abspath(__file__))
G = json.load(open(os.path.join(HERE, "knowledge_graph.json")))
NODES, EDGES = G["nodes"], G["edges"]

# node "type" -> Neo4j label (CamelCase)
LABEL = {"input": "Input", "branch": "Branch", "rule": "Rule", "table": "Table",
         "level": "SideEffectLevel", "phase": "Phase", "drug": "Drug", "band": "Band",
         "labs": "LabPanel", "recommendation": "Recommendation",
         "validation": "Validation", "case": "ValidationCase"}
# edge "rel" text -> Neo4j relationship type (UPPER_SNAKE)
def rel_type(rel):
    r = (rel or "LINKS").strip().lower()
    m = {"->": "YIELDS", "-> ": "YIELDS", "uses": "USES", "gate": "GATED_BY",
         "tier from": "TIER_FROM", "selects": "SELECTS", "expects": "EXPECTS",
         "fixed in": "FIXED_IN", "next dose from": "NEXT_DOSE_FROM",
         "band": "HAS_BAND", "has": "HAS", "seizure(s)": "IF_SEIZURES", "none": "IF_NONE",
         "summary": "SUMMARY", "flagged": "FLAGGED"}
    if r in m:
        return m[r]
    return "".join(c if c.isalnum() else "_" for c in r).strip("_").upper() or "LINKS"


def _props(d, keys):
    parts = []
    for k in keys:
        v = d.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            parts.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, (int, float)):
            parts.append(f"{k}: {v}")
        else:
            s = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
            parts.append(f"{k}: '{s}'")
    return "{" + ", ".join(parts) + "}"


# ---- Neo4j Cypher ----------------------------------------------------------
def write_cypher():
    lines = ["// Aim 2 knowledge graph - Neo4j import (DRAFT / simulated).",
             "// Idempotent: MERGE on id. Run:  cat knowledge_graph.cypher | cypher-shell",
             "CREATE CONSTRAINT aim2_node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE;",
             ""]
    for n in NODES:
        lab = LABEL.get(n["type"], "Node")
        props = _props(n, ["id", "label", "type", "layer", "detail",
                           "branch", "provenance"])
        # every node also gets the generic :Node label so the constraint applies
        lines.append(f"MERGE (n:Node:{lab} {{id: '{n['id']}'}}) SET n += {props};")
    lines.append("")
    for e in EDGES:
        rt = rel_type(e.get("rel"))
        ep = _props({"rel": e.get("rel", ""), "minor": bool(e.get("minor"))}, ["rel", "minor"])
        lines.append(f"MATCH (a:Node {{id: '{e['source']}'}}), (b:Node {{id: '{e['target']}'}}) "
                     f"MERGE (a)-[r:{rt}]->(b) SET r += {ep};")
    out = os.path.join(HERE, "knowledge_graph.cypher")
    open(out, "w").write("\n".join(lines) + "\n")
    return out


# ---- GraphML (NetworkX / Gephi / yEd) --------------------------------------
def write_graphml():
    keys = {"label": "string", "type": "string", "layer": "long", "detail": "string",
            "branch": "string", "provenance": "string"}
    ekeys = {"rel": "string", "minor": "boolean"}
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    for k, t in keys.items():
        L.append(f'<key id="{k}" for="node" attr.name="{k}" attr.type="{t}"/>')
    for k, t in ekeys.items():
        L.append(f'<key id="{k}" for="edge" attr.name="{k}" attr.type="{t}"/>')
    L.append('<graph edgedefault="directed">')
    for n in NODES:
        L.append(f'<node id="{xesc(n["id"])}">')
        for k in keys:
            if n.get(k) not in (None, ""):
                L.append(f'<data key="{k}">{xesc(str(n[k]))}</data>')
        L.append("</node>")
    for i, e in enumerate(EDGES):
        L.append(f'<edge id="e{i}" source="{xesc(e["source"])}" target="{xesc(e["target"])}">')
        L.append(f'<data key="rel">{xesc(str(e.get("rel","")))}</data>')
        L.append(f'<data key="minor">{str(bool(e.get("minor"))).lower()}</data>')
        L.append("</edge>")
    L += ["</graph>", "</graphml>"]
    out = os.path.join(HERE, "knowledge_graph.graphml")
    open(out, "w").write("\n".join(L) + "\n")
    return out


if __name__ == "__main__":
    c = write_cypher()
    g = write_graphml()
    print(f"nodes={len(NODES)} edges={len(EDGES)}")
    print("wrote", os.path.basename(c), "and", os.path.basename(g))
    # optional round-trip check if networkx is installed
    try:
        import networkx as nx
        H = nx.read_graphml(g)
        print(f"networkx round-trip OK: {H.number_of_nodes()} nodes, {H.number_of_edges()} edges")
    except ImportError:
        print("networkx not installed here - GraphML still valid; "
              "load with: nx.read_graphml('knowledge_graph.graphml')")
