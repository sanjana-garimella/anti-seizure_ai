"""
test_graph_integrity.py
-----------------------
Turns the Aim 2 knowledge graph into a VALIDATION GATE in the test suite: the
graph is rebuilt from the CURRENT engine JSON and its logic is checked - every
decision rule must reach a recommendation, every recommendation must be
reachable from the survey, every rule must carry clinical provenance, both
branches must resolve, and every PI-flagged case must link to the rule/table it
was fixed in. If a future edit orphans a rule or strands a recommendation, this
test fails.

Skips cleanly if the decision_map tooling isn't present (engine stays runnable
stand-alone). Pure stdlib.
"""
import contextlib
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DECISION_MAP = os.path.join(_ROOT, "decision_map")


@unittest.skipUnless(
    os.path.isfile(os.path.join(_DECISION_MAP, "graph_checks.py")),
    "decision_map/graph_checks.py not present")
class TestKnowledgeGraphIntegrity(unittest.TestCase):
    def test_graph_logic_is_sound(self):
        if _DECISION_MAP not in sys.path:
            sys.path.insert(0, _DECISION_MAP)
        import graph_checks
        # build silently (the builder prints progress) so test output stays clean
        with contextlib.redirect_stdout(io.StringIO()):
            graph = graph_checks.build_fresh_graph()
            problems = graph_checks.run_checks(graph)
        self.assertEqual(problems, [], "knowledge-graph integrity problems:\n"
                         + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
