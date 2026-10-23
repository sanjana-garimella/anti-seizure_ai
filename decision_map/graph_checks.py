#!/usr/bin/env python3
"""
graph_checks.py
---------------
Turn the knowledge graph from a picture into a VALIDATION GATE. Rebuilds the
graph from the current engine JSON (via build_knowledge_graph) and runs
integrity checks on the logic itself:

  1. No dangling edges (every edge endpoint exists).
  2. No orphan rule       - every decision rule reaches >=1 recommendation.
  3. No unreachable rec    - every recommendation is reachable from the survey.
  4. Provenance coverage   - every rule carries a clinical provenance tag.
  5. Both branches resolve - breakthrough & seizure-free each reach a rec.
  6. Flagged cases linked  - every PI-flagged case points at the rule/table fixed.

Pure stdlib (matches the engine's no-dependency rule). Returns a list of problem
strings; empty list == pass.

Usage:  python3 graph_checks.py           # prints report, exits 1 on failure
Import: from graph_checks import run_checks, build_fresh_graph
"""
import os
import runpy
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
_BUILDER = os.path.join(HERE, "build_knowledge_graph.py")


def build_fresh_graph():
    """Execute the builder and return its in-memory `graph` dict (nodes/edges),
    so checks always run against the CURRENT engine data (not a stale file)."""
    ns = runpy.run_path(_BUILDER)
    return ns["graph"]


def _adj(graph):
    out = {n["id"]: [] for n in graph["nodes"]}
    for e in graph["edges"]:
        if e["source"] in out:
            out[e["source"]].append(e["target"])
    return out


def _reaches(adj, start, targets):
    """True if any node in `targets` is reachable from `start` (directed BFS)."""
    seen, q = {start}, deque([start])
    while q:
        cur = q.popleft()
        if cur in targets and cur != start:
            return True
        for nxt in adj.get(cur, []):
            if nxt in targets:
                return True
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return False


def _reachable_from(adj, start):
    seen, q = {start}, deque([start])
    while q:
        cur = q.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def run_checks(graph):
    problems = []
    nodes = {n["id"]: n for n in graph["nodes"]}
    adj = _adj(graph)
    recs = {n["id"] for n in graph["nodes"] if n["type"] == "recommendation"}
    rules = [n for n in graph["nodes"] if n["type"] == "rule"]

    # 1. dangling edges
    for e in graph["edges"]:
        if e["source"] not in nodes or e["target"] not in nodes:
            problems.append(f"[dangling edge] {e['source']} -> {e['target']}")

    # 2. every rule reaches a recommendation
    for r in rules:
        if not _reaches(adj, r["id"], recs):
            problems.append(f"[orphan rule] {r['id']} ({r['label']}) reaches no recommendation")

    # 3. every recommendation reachable from the survey root
    root = "survey"
    if root in nodes:
        reach = _reachable_from(adj, root)
        for rc in recs:
            if rc not in reach:
                problems.append(f"[unreachable recommendation] {rc} not reachable from '{root}'")
    else:
        problems.append("[missing] no 'survey' input root node")

    # 4. provenance coverage on rules
    for r in rules:
        if not str(r.get("provenance") or "").strip():
            problems.append(f"[no provenance] rule {r['id']} has no clinical source tag")

    # 5. both branches resolve to a recommendation
    for b in ("branch_bt", "branch_sf"):
        if b in nodes and not _reaches(adj, b, recs):
            problems.append(f"[branch dead-end] {b} reaches no recommendation")

    # 6. flagged PI cases link to a rule/table they were fixed in
    for n in graph["nodes"]:
        if n["type"] == "case":
            if not any(e["source"] == n["id"] and e.get("rel") == "fixed in"
                       for e in graph["edges"]):
                problems.append(f"[unlinked case] {n['id']} has no 'fixed in' link")

    return problems


def main():
    graph = build_fresh_graph()
    problems = run_checks(graph)
    print(f"\nGraph integrity: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
    if not problems:
        print("PASS - no integrity problems (all rules resolve, all recommendations "
              "reachable, provenance complete, cases linked).")
        return 0
    print(f"FAIL - {len(problems)} problem(s):")
    for p in problems:
        print("  -", p)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
