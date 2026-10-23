"""
Tests for validation/leakage_safe_cv.py

Verifies the leakage-safe CV utilities behave correctly and that grouped+purged
k-fold actually removes the optimism that naive k-fold shows on templated data.
Advisory/eval layer only; nothing here touches engine_core.
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "validation"))

import leakage_safe_cv as lcv  # noqa: E402


class TestSplitters(unittest.TestCase):
    def test_naive_kfold_partitions_all_indices(self):
        n, k = 37, 5
        splits = lcv.naive_kfold(n, k, seed=0)
        self.assertEqual(len(splits), k)
        covered = set()
        for _, test in splits:
            covered |= set(test)
        self.assertEqual(covered, set(range(n)))  # every item tested exactly once

    def test_naive_kfold_test_folds_disjoint(self):
        splits = lcv.naive_kfold(50, 5, seed=1)
        seen = []
        for _, test in splits:
            seen.extend(test)
        self.assertEqual(len(seen), len(set(seen)))  # no item in two test folds

    def test_group_kfold_never_splits_a_group(self):
        groups = [g for g in range(20) for _ in range(5)]  # 20 groups x 5 items
        splits = lcv.group_kfold(groups, n_splits=5, seed=0)
        for train, test in splits:
            train_groups = {groups[i] for i in train}
            test_groups = {groups[i] for i in test}
            self.assertEqual(train_groups & test_groups, set(),
                             "a group appeared in both train and test")

    def test_group_kfold_covers_every_item_once_in_test(self):
        groups = [g for g in range(12) for _ in range(3)]
        splits = lcv.group_kfold(groups, n_splits=4, seed=2)
        covered = []
        for _, test in splits:
            covered.extend(test)
        self.assertEqual(sorted(covered), list(range(len(groups))))

    def test_group_kfold_balances_folds(self):
        groups = [g for g in range(40) for _ in range(2)]  # 80 items, 40 groups
        splits = lcv.group_kfold(groups, n_splits=5, seed=0)
        sizes = [len(test) for _, test in splits]
        self.assertLessEqual(max(sizes) - min(sizes), 4)  # roughly even


class TestPurge(unittest.TestCase):
    def test_purge_removes_near_duplicates(self):
        texts = [
            "the seizure lasted about three minutes",   # 0 (train, dup of test)
            "he was very drowsy after the new dose",    # 1 (train, distinct)
            "the seizure lasted about three minutes",   # 2 (test)
        ]
        kept = lcv.purge_train([0, 1], [2], texts, threshold=0.8)
        self.assertIn(1, kept)
        self.assertNotIn(0, kept)

    def test_purge_keeps_distinct_items(self):
        texts = ["a totally different sentence here", "seizure lasted a minute"]
        kept = lcv.purge_train([0], [1], texts, threshold=0.8)
        self.assertEqual(kept, [0])

    def test_jaccard_bounds(self):
        self.assertEqual(lcv.jaccard("same words here", "same words here"), 1.0)
        self.assertEqual(lcv.jaccard("apple", "banana"), 0.0)

    def test_template_group_key_collapses_numbers(self):
        a = lcv.template_group_key("lasts 2 to 3 minutes, 3 times a day")
        b = lcv.template_group_key("lasts 4 to 5 minutes, 2 times a day")
        self.assertEqual(a, b)


class TestOptimismGap(unittest.TestCase):
    def test_grouped_purged_is_not_more_optimistic_than_naive(self):
        result = lcv._demo()
        naive = result["naive_kfold"]["mean"]
        gp = result["group_kfold_purged"]["mean"]
        # On leaky templated data the memorizer must not score HIGHER once
        # template families are held out and duplicates purged.
        self.assertGreaterEqual(naive + 1e-9, gp)

    def test_demo_reports_positive_leakage_gap(self):
        result = lcv._demo()
        # The templated dataset is deliberately leaky, so the gap should be
        # clearly positive (naive overstates performance).
        self.assertGreater(result["gap_naive_minus_grouped_purged"], 0.05)


if __name__ == "__main__":
    unittest.main()
