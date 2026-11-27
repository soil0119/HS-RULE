"""스냅샷 완전성 계약(#68): 부분 수집 결과로 누락 규정을 지우지 않는다. 일회용 PostgreSQL과 대역 임베딩을 쓴다."""
import json
import os
import tempfile
import unittest
from unittest import mock

from hsrule import official_indexer
from hsrule.official_indexer import SnapshotError, read_snapshot, sync_snapshot
from hsrule.official_repository import OfficialRepository
from tests.fakes import FakeEmbeddings
from tests.test_index_review_fixes import TEXT, reg

URL = os.getenv("HSRULE_TEST_DATABASE_URL")


class ReadSnapshotTests(unittest.TestCase):
    def test_only_declared_complete_snapshots_allow_removal(self):
        items = [reg()]
        self.assertEqual(read_snapshot({"schema_version": 1, "complete": True, "failures": [], "regulations": items}),
                         (items, True))
        self.assertFalse(read_snapshot({"regulations": items})[1])  # 이전 형식은 부분 결과로 본다
        self.assertFalse(read_snapshot({"schema_version": 1, "complete": True, "failures": [{"seq": 5}],
                                        "regulations": items})[1])
        self.assertFalse(read_snapshot({"schema_version": 1, "complete": "yes", "regulations": items})[1])

    def test_unknown_version_or_shape_is_rejected(self):
        for payload in ({"schema_version": 2, "regulations": []}, {"items": []}, [], {"regulations": {}}):
            with self.subTest(payload=payload), self.assertRaises(SnapshotError):
                read_snapshot(payload)

    def test_cli_rejects_bad_snapshot_before_touching_the_db(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "s.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 9, "regulations": []}, handle)
            with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), \
                    mock.patch("hsrule.official_repository.OfficialRepository", side_effect=AssertionError), \
                    mock.patch("sys.stderr"):
                self.assertEqual(official_indexer.main(["x", path]), 2)


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class RemovalGuardTests(unittest.TestCase):
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()
        self.emb = FakeEmbeddings(TEXT)
        sync_snapshot([reg(str(n), f"{n}:1") for n in range(10, 20)], self.repo, self.emb, complete=True)

    def test_partial_snapshot_keeps_missing_regulations(self):
        report = sync_snapshot([reg("10", "10:1")], self.repo, self.emb)
        self.assertEqual(report["removed"], [])
        self.assertEqual(report["removal_skipped"]["reason"], "partial_snapshot")
        self.assertEqual(len(self.repo.regulation_ids()), 10)

    def test_complete_snapshot_removes_a_few_missing(self):
        report = sync_snapshot([reg(str(n), f"{n}:1") for n in range(10, 18)], self.repo, self.emb, complete=True)
        self.assertEqual(sorted(report["removed"]), ["18", "19"])
        self.assertNotIn("removal_skipped", report)

    def test_mass_removal_is_held_even_when_complete(self):
        report = sync_snapshot([reg("10", "10:1")], self.repo, self.emb, complete=True)
        self.assertEqual(report["removed"], [])
        self.assertEqual(report["removal_skipped"]["reason"], "too_many_removals")
        self.assertEqual(len(report["removal_skipped"]["candidates"]), 9)
        report = sync_snapshot([reg("10", "10:1")], self.repo, self.emb, complete=True, max_removal_ratio=1.0)
        self.assertEqual(len(report["removed"]), 9)  # 확인 후 비율을 올리면 지운다

    def test_index_failure_holds_removal(self):
        bad = dict(reg("11", "11:2"), official_url="")
        report = sync_snapshot([reg("10", "10:1"), bad] + [reg(str(n), f"{n}:1") for n in range(12, 19)],
                               self.repo, self.emb, complete=True)
        self.assertEqual(report["removal_skipped"]["reason"], "index_failures")
        self.assertIn("19", self.repo.regulation_ids())


if __name__ == "__main__":
    unittest.main()
