"""memory_snap: fingerprints, diff ordering, recall shaping (AE12)."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import memory_snap as ms  # noqa: E402

NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)


def stamp(label, ts, i="x"):
    return ms.EntityStamp(id=i, label=label, fingerprint="fp", ts=ts)


class Fingerprint(unittest.TestCase):
    def test_parts_are_separated_so_boundaries_matter(self):
        self.assertNotEqual(ms.fingerprint("a", "b"), ms.fingerprint("ab"))

    def test_none_is_skipped_and_dict_order_does_not_matter(self):
        self.assertEqual(ms.fingerprint("a", None, "b"), ms.fingerprint("a", "b"))
        self.assertEqual(ms.fingerprint({"b": 1, "a": 2}), ms.fingerprint({"a": 2, "b": 1}))

    def test_sixteen_hex_chars(self):
        fp = ms.fingerprint("中文")
        self.assertEqual(len(fp), 16)
        int(fp, 16)

    def test_changing_any_part_changes_it(self):
        self.assertNotEqual(ms.fingerprint("a", "b", "c"), ms.fingerprint("a", "b", "d"))


class Diff(unittest.TestCase):
    def test_order_is_added_removed_modified_each_by_id(self):
        changes = ms.diff_snapshots(
            {"a": "1", "b": "1", "y": "1"},
            {"z": "1", "b": "2", "c": "1"},
            label_map={"z": "Z", "b": "B", "c": "C"},
            prev_label_map={"y": "Y"},
            now="T",
        )
        self.assertEqual([(c.kind, c.entity_id) for c in changes],
                         [("added", "c"), ("added", "z"), ("removed", "a"), ("removed", "y"), ("modified", "b")])
        removed_a = [c for c in changes if c.entity_id == "a"][0]
        self.assertEqual(removed_a.label, "a")  # no old label: falls back to the id

    def test_empty_current_against_non_empty_state_is_all_removed(self):
        changes = ms.diff_snapshots({"a": "1", "b": "2"}, {}, label_map={})
        self.assertEqual([c.kind for c in changes], ["removed", "removed"])

    def test_no_change_gives_nothing(self):
        self.assertEqual(ms.diff_snapshots({"a": "1"}, {"a": "1"}, label_map={"a": "A"}), [])


class Recall(unittest.TestCase):
    def test_ae12_same_label_same_surface_keeps_only_newest(self):
        events = [
            types.SimpleNamespace(kind="query", payload={"query": "渗透作用是什么"}, ts="2026-03-0%dT00:00:00+00:00" % d)
            for d in range(4, 9)
        ]
        hits = ms.shape_queries({"kb": events}, now=NOW)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].ts, "2026-03-08T00:00:00+00:00")

    def test_different_surfaces_keep_both(self):
        by_surface = {"chat": [stamp("same", "2026-03-09T00:00:00+00:00")],
                      "quiz": [stamp("same", "2026-03-09T01:00:00+00:00")]}
        self.assertEqual(len(ms.shape_recent(by_surface, now=NOW)), 2)

    def test_window_drops_old_and_undated(self):
        by_surface = {"chat": [stamp("old", "2026-02-01T00:00:00+00:00"), stamp("undated", ""),
                               stamp("junk", "nope"), stamp("fresh", "2026-03-09T00:00:00+00:00")]}
        self.assertEqual([h.label for h in ms.shape_recent(by_surface, now=NOW)], ["fresh"])

    def test_future_timestamp_reads_zero_days(self):
        self.assertEqual(ms.days_ago("2026-03-12T00:00:00+00:00", now=NOW), 0)

    def test_limit_clamps(self):
        self.assertEqual(ms.clamp_limit(None), 20)
        self.assertEqual(ms.clamp_limit(0), 20)
        self.assertEqual(ms.clamp_limit(-1), 20)
        self.assertEqual(ms.clamp_limit(5), 5)
        self.assertEqual(ms.clamp_limit(1000), 100)

    def test_label_is_one_line_and_bounded(self):
        by_surface = {"chat": [stamp("  a\n\n b\t c ", "2026-03-09T00:00:00+00:00", "1"),
                               stamp("x" * 300, "2026-03-08T00:00:00+00:00", "2")]}
        labels = [h.label for h in ms.shape_recent(by_surface, now=NOW)]
        self.assertEqual(labels[0], "a b c")
        self.assertEqual(len(labels[1]), 200)

    def test_surface_filter_falls_back_to_all_on_unknown(self):
        by_surface = {"chat": [stamp("c", "2026-03-09T00:00:00+00:00")]}
        self.assertEqual(len(ms.shape_recent(by_surface, now=NOW, surfaces=["nonsense"])), 1)
        self.assertEqual(len(ms.shape_recent(by_surface, now=NOW, surfaces=["quiz"])), 0)


if __name__ == "__main__":
    unittest.main()
