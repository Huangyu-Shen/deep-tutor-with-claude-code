"""memory_doc: identifiers, document round-trip, atomic batch operations (AE1, AE2)."""

from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import memory_doc as md  # noqa: E402

E1 = "m_01HZK4ABCDEFGHJKMNPQRSTVWX"
E2 = "m_01HZK5ABCDEFGHJKMNPQRSTVWX"
E3 = "m_01HZK6ABCDEFGHJKMNPQRSTVWX"

SAMPLE = (
    "# Chat memory\n\n"
    "## Concepts\n\n"
    "- Limit uses pairing [^1] <!--%s-->\n"
    "- Continuity implies sequential [^1], [^2] <!--%s-->\n\n"
    "## Mastery\n\n"
    "- Geometric intuition [^2] <!--%s-->\n\n"
    "---\n\n"
    "[^1]: chat:A\n"
    "[^2]: chat:B\n"
) % (E1, E2, E3)


class Identifiers(unittest.TestCase):
    def test_ulid_shape(self):
        u = md.new_ulid()
        self.assertEqual(len(u), 26)
        self.assertTrue(set(u) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ"))

    def test_ulid_time_prefix_sorts(self):
        first = md.new_ulid(now_ms=1_000_000)
        second = md.new_ulid(now_ms=1_000_005)
        self.assertLess(first[:10], second[:10])
        a = md.new_ulid()
        time.sleep(0.005)
        b = md.new_ulid()
        self.assertLessEqual(a[:10], b[:10])

    def test_ids_and_refs(self):
        self.assertTrue(md.is_entry_id(md.new_entry_id()))
        tid = md.new_trace_id("chat")
        self.assertTrue(md.is_trace_id(tid))
        self.assertFalse(md.is_entry_id(tid))
        for surface in md.SURFACES:
            self.assertTrue(md.is_shortname_ref(surface))
            self.assertTrue(md.is_valid_ref(surface))
        for bad in ("CHAT", "co-writer", "not-an-id", "", "kb:has space", "kb:has,comma"):
            self.assertFalse(md.is_valid_ref(bad), bad)
        self.assertTrue(md.is_valid_ref("quiz:session:question-1"))
        self.assertTrue(md.is_valid_ref("chat:learn-abc_1"))

    def test_unique(self):
        self.assertEqual(len({md.new_ulid() for _ in range(500)}), 500)


class Documents(unittest.TestCase):
    def test_ae1_roundtrip_is_byte_identical_and_shares_footnotes(self):
        doc = md.parse(SAMPLE)
        self.assertEqual(md.serialize(doc), SAMPLE)
        self.assertEqual(doc.sections[0][1][0].refs, ["chat:A"])
        self.assertEqual(doc.sections[0][1][1].refs, ["chat:A", "chat:B"])
        self.assertEqual(SAMPLE.count("[^1]: chat:A"), 1)

    def test_empty_section_not_written(self):
        doc = md.Document(title="X")
        doc.sections.append(("Empty", []))
        self.assertNotIn("Empty", md.serialize(doc))
        self.assertEqual(md.serialize(md.Document()), "\n")

    def test_entry_without_refs_has_only_id_comment(self):
        doc = md.Document(title="X")
        doc.section_entries("S").append(md.Entry(id=E1, section="S", text="plain"))
        self.assertIn("- plain <!--%s-->" % E1, md.serialize(doc))

    def test_find_remove_section_entries(self):
        doc = md.parse(SAMPLE)
        self.assertIsNotNone(doc.find(E2))
        self.assertTrue(doc.remove(E2))
        self.assertIsNone(doc.find(E2))
        self.assertFalse(doc.remove(E2))
        entries = doc.section_entries("New")
        self.assertIs(doc.section_entries("New"), entries)

    def test_old_footnote_format_is_not_read(self):
        old = "# T\n\n## S\n- text[^%s]\n\n---\n\n[^%s]: chat:A\n" % (E1, E1)
        self.assertEqual(md.parse(old).all_entries(), [])

    def test_chinese_text_survives(self):
        doc = md.Document(title="偏好")
        doc.section_entries("Preferences").append(
            md.Entry(id=E1, section="Preferences", text="回答用中文", refs=["chat:A"])
        )
        again = md.parse(md.serialize(doc))
        self.assertEqual(again.find(E1).text, "回答用中文")


class Operations(unittest.TestCase):
    def setUp(self):
        self.doc = md.parse(SAMPLE)
        self.before = md.serialize(self.doc)

    def test_ae2_edit_and_delete_same_id_rejects_whole_batch(self):
        ops = [md.EditOp(E1, "e", ["chat"]), md.DeleteOp(E1, "stale")]
        report = md.apply(self.doc, ops)
        self.assertFalse(report.accepted)
        self.assertEqual(md.serialize(self.doc), self.before)
        report = md.apply(self.doc, list(reversed(ops)))
        self.assertFalse(report.accepted)
        self.assertEqual(md.serialize(self.doc), self.before)

    def test_ae2_overlong_add_rejects_the_valid_ones_too(self):
        ops = [md.AddOp("Concepts", "fine", ["chat"]), md.AddOp("Concepts", "x" * 241, ["chat"])]
        report = md.apply(self.doc, ops)
        self.assertFalse(report.accepted)
        self.assertEqual(md.serialize(self.doc), self.before)

    def test_each_validation_failure_rejects(self):
        bad_batches = [
            [md.AddOp("S", "t", [])],
            [md.AddOp("S", "t", ["not-an-id"])],
            [md.AddOp("s" * 81, "t", ["chat"])],
            [md.AddOp("", "t", ["chat"])],
            [md.AddOp("S", "", ["chat"])],
            [md.EditOp("m_01HZK9ABCDEFGHJKMNPQRSTVWX", "t", ["chat"])],
            [md.EditOp(E1, "t", [])],
            [md.DeleteOp(E1, "because")],
            [md.DeleteOp("m_01HZK9ABCDEFGHJKMNPQRSTVWX", "stale")],
        ]
        for batch in bad_batches:
            self.assertFalse(md.apply(self.doc, batch).accepted, batch)
            self.assertEqual(md.serialize(self.doc), self.before)

    def test_successful_batch_assigns_valid_ids_in_order(self):
        ops = [
            md.AddOp("Prefs", "one", ["chat"]),
            md.AddOp("Prefs", "two", ["chat:S1"]),
            md.EditOp(E3, "edited", ["chat:B"]),
            md.DeleteOp(E1, "superseded"),
        ]
        report = md.apply(self.doc, ops)
        self.assertTrue(report.accepted)
        ids = [r.entry_id for r in report.results if r.entry_id]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(md.is_entry_id(i) for i in ids))
        self.assertEqual([e.text for e in self.doc.section_entries("Prefs")], ["one", "two"])
        self.assertIsNone(self.doc.find(E1))
        self.assertEqual(self.doc.find(E3).text, "edited")

    def test_boundaries_are_accepted(self):
        ops = [md.AddOp("s" * 80, "y" * 240, ["chat"])]
        self.assertTrue(md.apply(self.doc, ops).accepted)

    def test_all_delete_reasons(self):
        for reason in ("contradicted", "superseded", "stale", "low-signal"):
            doc = md.parse(SAMPLE)
            self.assertTrue(md.apply(doc, [md.DeleteOp(E1, reason)]).accepted, reason)

    def test_line_breaks_and_comment_markers_are_refused(self):
        for bad in ("line1\nline2", "a\r\nb", "a\u2028b", "x <!--m_01HZK9ABCDEFGHJKMNPQRSTVWX-->", "tail -->"):
            self.assertFalse(md.apply(self.doc, [md.AddOp("S", bad, ["chat"])]).accepted, repr(bad))
            self.assertFalse(md.apply(self.doc, [md.AddOp(bad, "t", ["chat"])]).accepted, repr(bad))
            self.assertFalse(md.apply(self.doc, [md.EditOp(E1, bad, ["chat"])]).accepted, repr(bad))
        self.assertEqual(md.serialize(self.doc), self.before)

    def test_an_accepted_entry_always_survives_a_round_trip(self):
        report = md.apply(self.doc, [md.AddOp("Prefs", "ends like a footnote [^1]", ["chat"])])
        # a marker-looking tail is the one shape the line pattern could swallow; it must not be lost
        again = md.parse(md.serialize(self.doc))
        self.assertEqual(len(again.all_entries()), len(self.doc.all_entries()))
        self.assertTrue(report.accepted)

    def test_trailing_newline_is_not_part_of_an_id_or_ref(self):
        self.assertFalse(md.is_entry_id(E1 + "\n"))
        self.assertFalse(md.is_trace_id("chat:01HZK4ABCDEFGHJKMNPQRSTVWX\n"))
        self.assertFalse(md.is_valid_ref("quiz:abc\n"))

    def test_unparsed_lines_names_what_a_rewrite_would_lose(self):
        self.assertEqual(md.unparsed_lines(SAMPLE), [])
        text = SAMPLE + "\nsome hand note\n"
        self.assertEqual(md.unparsed_lines(text), ["some hand note"])
        self.assertEqual(md.unparsed_lines("# T\n\n- orphan <!--%s-->\n" % E1), ["- orphan <!--%s-->" % E1])
        self.assertEqual(md.unparsed_lines("# T\n\n## S\n\n- no anchor\n"), ["- no anchor"])

    def test_unknown_op_and_empty_batch(self):
        self.assertFalse(md.apply(self.doc, [object()]).accepted)
        self.assertTrue(md.apply(self.doc, []).accepted)


if __name__ == "__main__":
    unittest.main()
