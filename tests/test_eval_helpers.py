import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from run_rag_eval import first_rank, percentile, retrieval_metrics  # noqa: E402


class EvalHelperTests(unittest.TestCase):
    def test_metrics(self):
        self.assertEqual(first_rank(["a", "b", "c"], {"c", "z"}), 3)
        self.assertIsNone(first_rank(["a"], {"x"}))
        metrics = retrieval_metrics([{"rank": 1}, {"rank": 3}, {"rank": None}, {"rank": 2}])
        self.assertEqual(metrics["cases"], 4)
        self.assertEqual(metrics["recall"]["@1"], 0.25)
        self.assertEqual(metrics["recall"]["@3"], 0.75)
        self.assertAlmostEqual(metrics["mrr"], (1 + 1 / 3 + 1 / 2) / 4)
        self.assertIsNone(retrieval_metrics([])["recall"])
        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2)
        self.assertIsNone(percentile([], 0.5))


if __name__ == "__main__":
    unittest.main()
