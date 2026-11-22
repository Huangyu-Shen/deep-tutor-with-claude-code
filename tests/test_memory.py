"""memory.py CLI contract: L1, L2/L3 documents, preferences, snapshots, recall."""

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ledger  # noqa: E402
import memory  # noqa: E402
import memory_doc as md  # noqa: E402

T0 = 1_780_000_000.0  # 2026-05-28
DAY = 86400.0
E_MISSING = "m_01HZK9ABCDEFGHJKMNPQRSTVWX"


class MemoryTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, payload=None, learner=None, now=None):
        out = io.StringIO()
        stdin = io.StringIO(json.dumps(payload) if payload is not None else "")
        base = ["--root", self.root]
        if learner:
            base += ["--learner", learner]
        if now is not None:
            base += ["--now", str(now)]
        with contextlib.redirect_stdout(out):
            code = memory.main([*base, *argv], stdin=stdin)
        return code, json.loads(out.getvalue())

    def ok(self, *argv, **kw):
        code, out = self.run_cli(*argv, **kw)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])
        return out

    def refused(self, *argv, **kw):
        code, out = self.run_cli(*argv, **kw)
        self.assertEqual(code, 1, out)
        self.assertFalse(out["ok"])
        return out

    def mem(self, learner="default"):
        return Path(self.root) / ".tutor" / "memory" / learner

    def snapshot_files(self):
        """Every file under .tutor/memory with its bytes (to prove nothing changed)."""
        base = Path(self.root) / ".tutor" / "memory"
        return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()} \
            if base.exists() else {}

    def pref(self, text, op="add", target_id=None, learner=None, now=T0):
        payload = {"op": op, "text": text}
        if target_id:
            payload["target_id"] = target_id
        return self.run_cli("preference", payload=payload, learner=learner, now=now)


class EmitAndTrace(MemoryTestBase):
    def test_emit_appends_to_the_days_file_and_keeps_appending(self):
        a = self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "渗透作用"}}, now=T0)
        b = self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "扩散"}}, now=T0 + 60)
        self.assertEqual(a["warnings"], [])
        files = sorted((self.mem() / "trace" / "kb").glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        lines = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual([l["payload"]["query"] for l in lines], ["渗透作用", "扩散"])
        for line in lines:
            self.assertEqual(set(line), {"id", "ts", "surface", "kind", "payload", "session_id", "turn_id"})
            self.assertTrue(md.is_trace_id(line["id"]))
        self.assertEqual(a["event"]["id"], lines[0]["id"])
        self.assertNotEqual(a["event"]["id"], b["event"]["id"])

    def test_events_on_different_utc_days_go_to_different_files(self):
        self.ok("emit", "--surface", "chat", payload={"kind": "x"}, now=T0)
        self.ok("emit", "--surface", "chat", payload={"kind": "x"}, now=T0 + DAY)
        self.assertEqual(len(list((self.mem() / "trace" / "chat").glob("*.jsonl"))), 2)

    def test_ae3_append_failure_is_a_warning_not_a_failure(self):
        trace_root = self.mem() / "trace"
        trace_root.mkdir(parents=True)
        (trace_root / "chat").write_text("a file where the directory should be", encoding="utf-8")
        out = self.ok("emit", "--surface", "chat", payload={"kind": "x"}, now=T0)
        self.assertTrue(out["warnings"])
        self.assertIn("not saved", out["warnings"][0]["problem"])

    def test_emit_rejects_bad_input_and_disabled_surfaces(self):
        self.refused("emit", "--surface", "notebook", payload={"kind": "x"})
        self.assertFalse((self.mem() / "trace" / "notebook").exists())
        self.refused("emit", "--surface", "nope", payload={"kind": "x"})
        self.refused("emit", "--surface", "chat", payload={"kind": ""})
        self.refused("emit", "--surface", "chat", payload={"kind": "x", "payload": "no"})
        code, out = self.run_cli("emit", "--surface", "chat")
        self.assertEqual(code, 1)

    def test_trace_since_skips_old_files_and_bad_lines(self):
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "old"}}, now=T0)
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "new"}}, now=T0 + 3 * DAY)
        newest = sorted((self.mem() / "trace" / "kb").glob("*.jsonl"))[-1]
        with newest.open("a", encoding="utf-8") as fh:
            fh.write("not json\n[1,2]\n{\"id\": 5}\n")
        since = memory.iso_of(T0 + DAY)
        out = self.ok("trace", "--surface", "kb", "--since", since)
        self.assertEqual([e["payload"]["query"] for e in out["events"]], ["new"])
        self.assertEqual(self.ok("trace", "--surface", "kb", "--count")["count"], 2)
        self.assertEqual(self.ok("trace", "--surface", "kb", "--latest")["latest_ts"], memory.iso_of(T0 + 3 * DAY))

    def test_since_filters_events_inside_one_day_and_backlog_counts_only_the_later_ones(self):
        base = T0 - (T0 % DAY) + 3600  # 01:00 UTC on one day
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "early"}}, now=base)
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "late"}}, now=base + 7200)
        out = self.ok("trace", "--surface", "kb", "--since", memory.iso_of(base + 3600))
        self.assertEqual([e["payload"]["query"] for e in out["events"]], ["late"])

    def test_emit_keeps_session_and_turn_ids_and_checks_their_type(self):
        out = self.ok("emit", "--surface", "chat", payload={"kind": "x", "session_id": "s1", "turn_id": "t1"}, now=T0)
        self.assertEqual((out["event"]["session_id"], out["event"]["turn_id"]), ("s1", "t1"))
        stored = self.ok("trace", "--surface", "chat")["events"][0]
        self.assertEqual((stored["session_id"], stored["turn_id"]), ("s1", "t1"))
        self.refused("emit", "--surface", "chat", payload={"kind": "x", "session_id": 5})

    def test_text_that_cannot_be_encoded_is_a_warning_not_a_crash(self):
        out = self.ok("emit", "--surface", "chat", payload={"kind": "x", "payload": {"t": "bad \ud800 text"}}, now=T0)
        self.assertTrue(out["warnings"])

    def test_a_torn_last_line_does_not_swallow_the_next_event(self):
        self.ok("emit", "--surface", "chat", payload={"kind": "a"}, now=T0)
        path = next((self.mem() / "trace" / "chat").glob("*.jsonl"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"id": "torn"')  # no newline: a crash mid-append
        self.ok("emit", "--surface", "chat", payload={"kind": "b"}, now=T0 + 1)
        self.assertEqual([e["kind"] for e in self.ok("trace", "--surface", "chat")["events"]], ["a", "b"])

    def test_trace_by_ids_across_surfaces(self):
        a = self.ok("emit", "--surface", "chat", payload={"kind": "x"}, now=T0)["event"]["id"]
        b = self.ok("emit", "--surface", "kb", payload={"kind": "y"}, now=T0 + 1)["event"]["id"]
        out = self.ok("trace", "--ids", f"{a},{b},nonsense,chat:{'0' * 26}")
        self.assertEqual(sorted(e["id"] for e in out["events"]), sorted([a, b]))

    def test_latest_of_empty_surface_is_null(self):
        self.assertIsNone(self.ok("trace", "--surface", "chat", "--latest")["latest_ts"])


class OverviewAndReading(MemoryTestBase):
    def test_empty_overview_lists_seven_l2_and_four_l3_rows(self):
        rows = self.ok("overview")["rows"]
        self.assertEqual([r["key"] for r in rows if r["layer"] == "L2"], list(md.SURFACES))
        self.assertEqual([r["key"] for r in rows if r["layer"] == "L3"], ["recent", "profile", "scope", "preferences"])
        self.assertTrue(all(not r["exists"] and r["entry_count"] == 0 and r["backlog"] == 0 for r in rows))

    def test_overview_counts_entries_and_l2_backlog(self):
        self.ok("emit", "--surface", "chat", payload={"kind": "x"}, now=T0)
        self.ok("ops", "--layer", "L2", "--key", "chat", payload={"ops": [
            {"op": "add", "section": "S", "text": "t", "refs": ["chat"]}]})
        os.utime(self.mem() / "L2" / "chat.md", (T0 + DAY, T0 + DAY))
        self.ok("emit", "--surface", "chat", payload={"kind": "x"}, now=T0 + 2 * DAY)
        row = [r for r in self.ok("overview")["rows"] if (r["layer"], r["key"]) == ("L2", "chat")][0]
        self.assertTrue(row["exists"])
        self.assertEqual(row["entry_count"], 1)
        self.assertEqual(row["backlog"], 1)  # only the event after the file's mtime
        l3 = [r for r in self.ok("overview")["rows"] if r["layer"] == "L3"]
        self.assertTrue(all(r["backlog"] == 0 for r in l3))

    def test_read_l3_fixed_text_when_empty_and_joined_when_filled(self):
        out = self.ok("read-l3")
        self.assertTrue(out["empty"])
        self.assertEqual(out["text"], "尚无记忆")
        for slot, text in (("profile", "likes proofs"), ("preferences", "answer in Chinese")):
            self.ok("ops", "--layer", "L3", "--key", slot, payload={"ops": [
                {"op": "add", "section": "S", "text": text, "refs": ["chat"]}]})
        out = self.ok("read-l3")
        self.assertFalse(out["empty"])
        self.assertIn("likes proofs", out["text"])
        self.assertIn("answer in Chinese", out["text"])
        self.assertIn("\n\n---\n\n", out["text"].replace("\n---\n\n[^1]", "X"))
        self.assertLess(out["text"].index("likes proofs"), out["text"].index("answer in Chinese"))


class Preferences(MemoryTestBase):
    def test_ae6_duplicate_with_different_case_and_whitespace_points_at_first(self):
        code, first = self.pref("回答用中文")
        self.assertEqual(code, 0, first)
        self.assertEqual(first["status"], "added")
        code, second = self.pref("  回答用中文 ")
        self.assertEqual((code, second["status"], second["entry_id"]), (0, "duplicate", first["entry_id"]))
        code, third = self.pref("Answer IN   English", now=T0 + 1)
        self.assertEqual(third["status"], "added")
        code, fourth = self.pref("answer in english")
        self.assertEqual((fourth["status"], fourth["entry_id"]), ("duplicate", third["entry_id"]))
        doc = md.parse((self.mem() / "L3" / "preferences.md").read_text(encoding="utf-8"))
        self.assertEqual(len(doc.all_entries()), 2)

    def test_whitespace_inside_cjk_is_not_removed_by_the_key(self):
        self.pref("回答用中文")
        code, out = self.pref("回答用 中文")
        self.assertEqual(out["status"], "added")  # upstream key only collapses runs of whitespace

    def test_edit_needs_target_and_rewrites_the_entry(self):
        _, first = self.pref("short answers")
        code, out = self.pref("long answers", op="edit")
        self.assertEqual(code, 1)
        code, out = self.pref("long answers", op="edit", target_id=E_MISSING)
        self.assertEqual(code, 1)
        code, out = self.pref("long answers", op="edit", target_id=first["entry_id"])
        self.assertEqual((code, out["status"]), (0, "edited"))
        doc = md.parse((self.mem() / "L3" / "preferences.md").read_text(encoding="utf-8"))
        self.assertEqual([e.text for e in doc.all_entries()], ["long answers"])

    def test_a_line_break_in_a_preference_is_collapsed_and_the_entry_survives(self):
        code, out = self.pref("line1\n## Injected\nline2")
        self.assertEqual((code, out["status"]), (0, "added"))
        doc = md.parse((self.mem() / "L3" / "preferences.md").read_text(encoding="utf-8"))
        self.assertEqual([e.text for e in doc.all_entries()], ["line1 ## Injected line2"])
        self.assertEqual([name for name, _ in doc.sections], ["Preferences"])

    def test_text_limits_and_blank(self):
        self.assertEqual(self.pref("x" * 241)[0], 1)
        self.assertEqual(self.pref("x" * 240)[0], 0)
        self.assertEqual(self.pref("   ")[0], 1)
        self.assertEqual(self.run_cli("preference", payload={"op": "remove", "text": "t"})[0], 1)

    def test_entry_cites_the_event_that_was_just_written(self):
        _, out = self.pref("多举例子")
        events = self.ok("trace", "--surface", "chat")["events"]
        self.assertEqual([e["kind"] for e in events], ["preference_stated"])
        self.assertEqual(events[0]["id"], out["event_id"])
        doc = md.parse((self.mem() / "L3" / "preferences.md").read_text(encoding="utf-8"))
        self.assertEqual(doc.all_entries()[0].refs, [out["event_id"]])
        self.assertEqual(doc.all_entries()[0].section, "Preferences")

    def test_ae3_preference_is_still_written_when_l1_fails(self):
        trace_root = self.mem() / "trace"
        trace_root.mkdir(parents=True)
        (trace_root / "chat").write_text("blocker", encoding="utf-8")
        code, out = self.pref("多举例子")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["warnings"])
        self.assertEqual(len(md.parse((self.mem() / "L3" / "preferences.md").read_text(encoding="utf-8")).all_entries()), 1)

    def test_rejected_preference_leaves_no_event(self):
        self.pref("x" * 241)
        self.assertEqual(self.ok("trace", "--surface", "chat", "--count")["count"], 0)

    def test_duplicate_still_writes_an_event_like_upstream(self):
        self.pref("多举例子")
        self.pref("多举例子", now=T0 + 5)
        self.assertEqual(self.ok("trace", "--surface", "chat", "--count")["count"], 2)


class ManualEditing(MemoryTestBase):
    def setUp(self):
        super().setUp()
        self.ok("ops", "--layer", "L3", "--key", "profile", payload={"ops": [
            {"op": "add", "section": "About", "text": "first", "refs": ["chat"]},
            {"op": "add", "section": "About", "text": "second", "refs": ["chat:S1"]}]})
        self.path = self.mem() / "L3" / "profile.md"
        self.ids = [e["id"] for e in self.ok("doc-get", "--layer", "L3", "--key", "profile",
                                              "--format", "json")["document"]["sections"][0]["entries"]]

    def test_doc_put_then_doc_get_roundtrips(self):
        text = "# Mine\n\n## S\n\n- hand written <!--%s-->\n" % self.ids[0]
        self.ok("doc-put", "--layer", "L3", "--key", "profile", payload={"markdown": text})
        self.assertEqual(self.ok("doc-get", "--layer", "L3", "--key", "profile")["markdown"], text)

    def test_doc_put_without_a_title_writes_nothing(self):
        before = self.path.read_bytes()
        self.refused("doc-put", "--layer", "L3", "--key", "profile", payload={"markdown": "just prose"})
        self.refused("doc-put", "--layer", "L3", "--key", "profile", payload={"markdown": ""})
        self.assertEqual(self.path.read_bytes(), before)

    def test_batch_with_one_bad_op_leaves_the_file_byte_identical(self):
        before = self.path.read_bytes()
        out = self.refused("ops", "--layer", "L3", "--key", "profile", payload={"ops": [
            {"op": "add", "section": "About", "text": "third", "refs": ["chat"]},
            {"op": "delete", "target_id": self.ids[0], "reason": "because"}]})
        self.assertIn("nothing written", out["error"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["profile.md"])

    def test_rejected_batch_does_not_even_normalise_a_hand_written_file(self):
        # parses fully, but a rewrite would renumber the footnote: [^2] becomes [^1]
        text = "# Mine\n\n## S\n\n- odd [^2] <!--%s-->\n\n---\n\n[^2]: chat:b\n" % self.ids[0]
        self.ok("doc-put", "--layer", "L3", "--key", "profile", payload={"markdown": text})
        before = self.path.read_bytes()
        self.refused("ops", "--layer", "L3", "--key", "profile", payload={"ops": [
            {"op": "delete", "target_id": self.ids[0], "reason": "because"}]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_line_break_in_a_batch_is_refused_not_stored(self):
        before = self.path.read_bytes()
        self.refused("ops", "--layer", "L3", "--key", "profile", payload={"ops": [
            {"op": "add", "section": "About", "text": "a\n## Injected", "refs": ["chat"]}]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_edits_never_erase_lines_the_format_does_not_understand(self):
        text = "# Mine\n\nhand note the learner wrote\n\n## S\n\n- anchored <!--%s-->\n\n## Other\n\n- no anchor here\n" % self.ids[0]
        self.ok("doc-put", "--layer", "L3", "--key", "preferences", payload={"markdown": text})
        path = self.mem() / "L3" / "preferences.md"
        before = path.read_bytes()
        out = self.refused("ops", "--layer", "L3", "--key", "preferences", payload={"ops": [
            {"op": "add", "section": "S", "text": "t", "refs": ["chat"]}]})
        self.assertIn("erase", out["error"])
        self.assertEqual(self.pref("a new preference")[0], 1)
        self.refused("entry-delete", "--layer", "L3", "--key", "preferences", "--id", self.ids[0])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.ok("doc-get", "--layer", "L3", "--key", "preferences")["markdown"], text)  # reading still works

    def test_overview_names_a_damaged_document(self):
        self.path.write_text("no title here\n- bullet\n", encoding="utf-8")
        row = [r for r in self.ok("overview")["rows"] if (r["layer"], r["key"]) == ("L3", "profile")][0]
        self.assertTrue(row["exists"])
        self.assertIn("damaged", row["problem"])
        ok_row = [r for r in self.ok("overview")["rows"] if (r["layer"], r["key"]) == ("L3", "scope")][0]
        self.assertIsNone(ok_row["problem"])

    def test_malformed_ops_are_rejected(self):
        for ops in ("x", [1], [{"op": "zap"}], [{"op": "add", "section": "S"}],
                    [{"op": "add", "section": "S", "text": "t", "refs": "chat"}]):
            self.refused("ops", "--layer", "L3", "--key", "profile", payload={"ops": ops})

    def test_ae9_single_delete_needs_no_reason(self):
        out = self.ok("entry-delete", "--layer", "L3", "--key", "profile", "--id", self.ids[1])
        self.assertEqual(out["entry_count"], 1)
        before = self.path.read_bytes()
        self.refused("entry-delete", "--layer", "L3", "--key", "profile", "--id", E_MISSING)
        self.assertEqual(self.path.read_bytes(), before)
        self.refused("entry-delete", "--layer", "L2", "--key", "chat", "--id", self.ids[0])

    def test_ae9_batch_delete_without_reason_is_rejected(self):
        before = self.path.read_bytes()
        self.refused("ops", "--layer", "L3", "--key", "profile",
                  payload={"ops": [{"op": "delete", "target_id": self.ids[0]}]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_bad_layer_or_key(self):
        self.refused("doc-get", "--layer", "L1", "--key", "chat")
        self.refused("doc-get", "--layer", "L3", "--key", "chat")
        self.refused("doc-get", "--layer", "L2", "--key", "profile")

    def test_damaged_document_is_an_error_and_is_not_overwritten(self):
        self.path.write_text("some text with no title\n- bullet\n", encoding="utf-8")
        before = self.path.read_bytes()
        out = self.refused("doc-get", "--layer", "L3", "--key", "profile")
        self.assertIn("damaged", out["error"])
        self.refused("ops", "--layer", "L3", "--key", "profile", payload={"ops": [
            {"op": "add", "section": "S", "text": "t", "refs": ["chat"]}]})
        self.refused("entry-delete", "--layer", "L3", "--key", "profile", "--id", self.ids[0])
        self.assertEqual(self.pref("a new preference")[0], 0)  # other slots are unaffected
        self.assertEqual(self.path.read_bytes(), before)


class Isolation(MemoryTestBase):
    def test_two_learners_do_not_see_each_other(self):
        self.pref("alice prefers diagrams", learner="alice")
        self.pref("bob prefers text", learner="bob")
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "alice q"}}, learner="alice", now=T0)
        alice = self.ok("read-l3", learner="alice")["text"]
        bob = self.ok("read-l3", learner="bob")["text"]
        self.assertIn("alice prefers", alice)
        self.assertNotIn("bob", alice)
        self.assertIn("bob prefers", bob)
        self.assertNotIn("alice", bob)
        self.assertEqual(self.ok("trace", "--surface", "kb", "--count", learner="bob")["count"], 0)
        rows = self.ok("overview", learner="bob")["rows"]
        self.assertEqual(sum(r["entry_count"] for r in rows), 1)
        self.assertEqual(self.ok("read-l3", learner="carol")["text"], "尚无记忆")

    def test_bad_learner_names_are_rejected(self):
        for name in ("../x", "a/b", ".hidden", "", "a b", ".."):
            code, out = self.run_cli("overview", learner=name) if name else (None, None)
            if name:
                self.assertEqual(code, 1, name)
        self.assertFalse((Path(self.root) / ".tutor").exists())


class LedgerUntouched(MemoryTestBase):
    def test_r27_ledger_bytes_do_not_change(self):
        out = io.StringIO()
        event = {"type": "kp_define", "kb": "bio", "name": "Osmosis", "knowledge_type": "concept", "sources": []}
        with contextlib.redirect_stdout(out):
            ledger.main(["--root", self.root, "record"], stdin=io.StringIO(json.dumps(event)))
        log = Path(self.root) / ".tutor" / "learner" / "default" / "events.jsonl"
        before = log.read_bytes()
        self.pref("多举例子")
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "q"}}, now=T0)
        self.ok("overview")
        self.ok("read-l3")
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual({p.name for p in (Path(self.root) / ".tutor").iterdir()}, {"learner", "memory"})


KB = "bio"
KP = "bio::osmosis"


class SnapshotBase(MemoryTestBase):
    def ledger(self, event, learner="default"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ledger.main(["--root", self.root, "--learner", learner, "record"],
                               stdin=io.StringIO(json.dumps(event)))
        self.assertEqual(code, 0, out.getvalue())

    def define(self, name="Osmosis", kb=KB, learner="default"):
        self.ledger({"type": "kp_define", "kb": kb, "name": name, "knowledge_type": "concept",
                     "sources": []}, learner)

    def attempt(self, ts, qid="q1", result="correct", answer="water moves", kp=KP,
                assessment_type="quiz", learner="default", **extra):
        event = {"type": "attempt", "kp": kp, "result": result, "assessment_type": assessment_type,
                 "ts": ts, "question_id": qid, "learner_answer": answer}
        event.update(extra)
        self.ledger(event, learner)

    def write_bank(self, questions, kb=KB):
        path = Path(self.root) / ".tutor" / "question_bank" / f"{kb}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "kb": kb, "questions": questions,
                                    "references": {}}), encoding="utf-8")
        return path

    def make_kb(self, name, sources):
        d = Path(self.root) / ".tutor" / "kb" / name
        d.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(d / "index.sqlite")
        conn.execute("CREATE TABLE sources (source_id TEXT PRIMARY KEY, display TEXT NOT NULL, "
                     "path TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL, unit TEXT NOT NULL, "
                     "units INTEGER NOT NULL, ingested_at REAL NOT NULL)")
        for i, (display, sha, ts) in enumerate(sources):
            conn.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                         (f"s{i}", display, f"/p/{display}", sha, "pdf", "page", 3, ts))
        conn.commit()
        conn.close()
        return d / "index.sqlite"

    def refresh(self, surface, *extra, now=T0):
        return self.run_cli("snapshot", "refresh", "--surface", surface, *extra, now=now)

    def state(self, surface):
        return json.loads((self.mem() / "snapshot" / surface / "state.json").read_text(encoding="utf-8"))

    def changes_bytes(self, surface):
        p = self.mem() / "snapshot" / surface / "changes.jsonl"
        return p.read_bytes() if p.exists() else b""


class ChatEntities(SnapshotBase):
    PAYLOAD = {"question": "讲讲渗透作用", "kps": [KP],
               "points": [{"text": "水从低浓度向高浓度移动", "source": "bio.pdf p.3"}]}

    def put(self, call_id="c1", payload=None, now=T0):
        return self.run_cli("chat-put", "--call-id", call_id, payload=payload or self.PAYLOAD, now=now)

    def test_ae8_script_side_entity_has_the_question_verbatim_and_the_points(self):
        code, out = self.put()
        self.assertEqual((code, out["entity_id"], out["created"]), (0, "learn-c1", True))
        entity = json.loads((self.mem() / "entities" / "chat" / "learn-c1.json").read_text(encoding="utf-8"))
        self.assertIn("讲讲渗透作用", entity["content"])
        self.assertIn("水从低浓度向高浓度移动 (bio.pdf p.3)", entity["content"])
        self.assertEqual(entity["metadata"]["question"], "讲讲渗透作用")
        self.assertEqual(entity["ts"], memory.iso_of(T0))

    def test_same_payload_again_leaves_the_file_byte_identical_even_later(self):
        self.put()
        path = self.mem() / "entities" / "chat" / "learn-c1.json"
        before = path.read_bytes()
        code, out = self.put(now=T0 + 3600)
        self.assertEqual((code, out["created"], out["updated"]), (0, False, False))
        self.assertEqual(path.read_bytes(), before)

    def test_changed_points_replace_the_entity_and_the_next_refresh_says_modified(self):
        self.put()
        self.assertEqual(self.refresh("chat")[1]["added"], 1)
        changed = dict(self.PAYLOAD, points=[{"text": "渗透需要半透膜", "source": "bio.pdf p.4"}])
        code, out = self.put(payload=changed, now=T0 + 10)
        self.assertEqual((code, out["updated"]), (0, True))
        self.assertEqual(len(list((self.mem() / "entities" / "chat").glob("*.json"))), 1)
        code, res = self.refresh("chat", now=T0 + 20)
        self.assertEqual((res["added"], res["modified"], res["removed"]), (0, 1, 0))

    def test_check_is_recorded_when_a_later_put_adds_it(self):
        self.put()
        code, out = self.put(payload=dict(self.PAYLOAD, check={"kp": KP, "result": "partial"}))
        self.assertTrue(out["updated"])
        entity = json.loads((self.mem() / "entities" / "chat" / "learn-c1.json").read_text(encoding="utf-8"))
        self.assertIn(f"**Check**: {KP}: partial", entity["content"])

    def test_bad_payloads_and_call_ids_write_nothing(self):
        bad = [
            {"kps": []},
            {"question": "  "},
            {"question": "q", "points": [{"text": "x" * 241, "source": "s"}]},
            {"question": "q", "points": [{"text": "t", "source": "s"}] * 9},
            {"question": "q", "points": [{"text": "t"}]},
            {"question": "q", "points": "no"},
            {"question": "q", "kps": [1]},
            {"question": "q", "check": {"kp": "k", "result": "great"}},
        ]
        for payload in bad:
            self.assertEqual(self.put(payload=payload)[0], 1, payload)
        for call_id in ("has space", "", "x" * 65, "a/b", "a.b"):
            code, _ = self.run_cli("chat-put", "--call-id", call_id, payload=self.PAYLOAD)
            self.assertEqual(code, 1, call_id)
        self.assertFalse((self.mem() / "entities").exists())

    def test_eight_points_of_240_characters_are_accepted(self):
        payload = {"question": "q", "points": [{"text": "p" * 240, "source": "s"}] * 8}
        self.assertEqual(self.put(payload=payload)[0], 0)

    def test_two_calls_make_two_entities_and_a_damaged_one_blocks_refresh(self):
        self.put("c1")
        self.put("c2", now=T0 + 1)
        self.assertEqual(self.refresh("chat")[1]["added"], 2)
        (self.mem() / "entities" / "chat" / "learn-c2.json").write_text("{broken", encoding="utf-8")
        before = self.changes_bytes("chat"), self.state("chat")
        code, out = self.refresh("chat", now=T0 + 5)
        self.assertEqual(code, 1)
        self.assertEqual((self.changes_bytes("chat"), self.state("chat")), before)


class RefreshAndGuards(SnapshotBase):
    def test_first_refresh_adds_all_then_no_change_writes_nothing_ae5(self):
        self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        self.make_kb("chem", [("b.pdf", "sha-b", T0 + 5)])
        code, out = self.refresh("kb")
        self.assertEqual((code, out["added"], out["removed"], out["modified"]), (0, 2, 0, 0))
        changes, state = self.changes_bytes("kb"), (self.mem() / "snapshot" / "kb" / "state.json").read_bytes()
        code, out = self.refresh("kb", now=T0 + 99)
        self.assertEqual((out["added"], out["written"]), (0, False))
        self.assertEqual(self.changes_bytes("kb"), changes)
        self.assertEqual((self.mem() / "snapshot" / "kb" / "state.json").read_bytes(), state)
        self.assertEqual(self.state("kb")["last_refresh"], memory.iso_of(T0))

    def test_changes_are_ordered_and_paged_newest_first_and_capped(self):
        self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        self.refresh("kb", now=T0)
        self.make_kb("chem", [("b.pdf", "sha-b", T0)])
        self.refresh("kb", now=T0 + 1)
        out = self.ok("snapshot", "changes", "--surface", "kb")
        self.assertEqual([c["entity_id"] for c in out["changes"]], ["chem", "bio"])
        out = self.ok("snapshot", "changes", "--surface", "kb", "--limit", "1", "--offset", "1")
        self.assertEqual([c["entity_id"] for c in out["changes"]], ["bio"])
        self.assertEqual(len(self.ok("snapshot", "changes", "--surface", "kb", "--limit", "99999")["changes"]), 2)

    def test_changes_page_is_capped_at_1000(self):
        directory = self.mem() / "snapshot" / "kb"
        directory.mkdir(parents=True)
        rows = [json.dumps({"ts": "t", "kind": "added", "entity_id": f"e{i:04d}", "label": "l",
                            "prev_fingerprint": None, "new_fingerprint": "f"}) for i in range(1005)]
        (directory / "changes.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
        out = self.ok("snapshot", "changes", "--surface", "kb", "--limit", "5000")
        self.assertEqual(len(out["changes"]), 1000)
        self.assertEqual(out["changes"][0]["entity_id"], "e1004")  # newest first

    def test_preview_writes_nothing(self):
        self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        code, out = self.run_cli("snapshot", "preview", "--surface", "kb", now=T0)
        self.assertEqual((code, out["added"], out["written"]), (0, 1, False))
        self.assertFalse((self.mem() / "snapshot").exists())

    def test_ae4_adapter_failure_writes_nothing(self):
        index = self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        self.refresh("kb")
        before = (self.changes_bytes("kb"), (self.mem() / "snapshot" / "kb" / "state.json").read_bytes())
        index.write_bytes(b"this is not a sqlite file at all" * 20)
        code, out = self.refresh("kb", now=T0 + 10)
        self.assertEqual(code, 1)
        self.assertIn("unreadable", out["error"])
        self.assertEqual((self.changes_bytes("kb"), (self.mem() / "snapshot" / "kb" / "state.json").read_bytes()), before)

    def test_ae4_damaged_quiz_bank_is_an_adapter_failure_too(self):
        self.define()
        self.attempt(T0, qid="q1")
        self.write_bank({"q1": {"prompt": "p", "answer": "a"}}).write_text("{nope", encoding="utf-8")
        self.assertEqual(self.refresh("quiz")[0], 1)
        self.assertFalse((self.mem() / "snapshot").exists())

    def test_empty_read_over_non_empty_state_is_refused_unless_allowed(self):
        index = self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        self.refresh("kb")
        import shutil
        shutil.rmtree(index.parent)
        before = (self.changes_bytes("kb"), (self.mem() / "snapshot" / "kb" / "state.json").read_bytes())
        code, out = self.refresh("kb", now=T0 + 10)
        self.assertEqual(code, 1)
        self.assertIn("--allow-empty", out["error"])
        self.assertEqual((self.changes_bytes("kb"), (self.mem() / "snapshot" / "kb" / "state.json").read_bytes()), before)
        code, out = self.refresh("kb", "--allow-empty", now=T0 + 20)
        self.assertEqual((code, out["removed"]), (0, 1))
        self.assertEqual(self.state("kb")["fingerprints"], {})
        # an empty read over an empty state is fine and writes nothing more
        size = len(self.changes_bytes("kb"))
        self.assertEqual(self.refresh("kb", now=T0 + 30)[0], 0)
        self.assertEqual(len(self.changes_bytes("kb")), size)

    def test_deleting_one_kb_directory_records_one_removed(self):
        self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        gone = self.make_kb("chem", [("b.pdf", "sha-b", T0)])
        self.refresh("kb")
        import shutil
        shutil.rmtree(gone.parent)
        code, out = self.refresh("kb", now=T0 + 1)
        self.assertEqual((out["added"], out["removed"], out["modified"]), (0, 1, 0))
        self.assertEqual(out["changes"][0]["entity_id"], "chem")
        self.assertEqual(out["changes"][0]["label"], "chem")

    def test_damaged_state_is_an_error_not_a_blank_slate(self):
        self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        self.refresh("kb")
        (self.mem() / "snapshot" / "kb" / "state.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.refresh("kb", now=T0 + 1)[0], 1)

    def test_disabled_surfaces_are_refused_without_creating_anything(self):
        for surface in ("notebook", "book", "partner", "cowriter"):
            for action in ("refresh", "preview", "changes"):
                code, out = self.run_cli("snapshot", action, "--surface", surface)
                self.assertEqual(code, 1, (surface, action))
                self.assertIn("not enabled", out["error"])
        self.assertEqual(self.run_cli("recent", "--surface", "notebook")[0], 0)
        self.assertFalse((self.mem() / "snapshot").exists())


class KbEntities(SnapshotBase):
    def test_kb_entity_uses_the_earliest_ingest_time_and_source_count(self):
        self.make_kb("bio", [("late.pdf", "sha-2", T0 + 100), ("early.pdf", "sha-1", T0)])
        self.make_kb("chem", [("c.pdf", "sha-3", T0 + 50)])
        entities = memory.read_kb_entities(memory_args(self.root)).entities
        by_id = {e.id: e for e in entities}
        self.assertEqual(sorted(by_id), ["bio", "chem"])
        self.assertEqual(by_id["bio"].ts, memory.iso_of(T0))
        self.assertEqual(by_id["bio"].metadata["source_count"], 2)
        self.assertIn("Sources: 2", by_id["bio"].content)

    def test_adding_a_source_modifies_the_kb_entity(self):
        index = self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        self.refresh("kb")
        conn = sqlite3.connect(index)
        conn.execute("INSERT INTO sources VALUES ('s9','b.pdf','/p/b','sha-b','pdf','page',1,?)", (T0 + 9,))
        conn.commit()
        conn.close()
        code, out = self.refresh("kb", now=T0 + 20)
        self.assertEqual((out["added"], out["modified"], out["removed"]), (0, 1, 0))

    def test_a_knowledge_base_under_a_path_with_special_characters_opens(self):
        self.make_kb("bio", [("a.pdf", "sha-a", T0)])
        odd = Path(self.root) / ".tutor" / "kb" / "weird#name?x"
        odd.mkdir(parents=True)
        (odd / "index.sqlite").write_bytes((Path(self.root) / ".tutor" / "kb" / "bio" / "index.sqlite").read_bytes())
        code, out = self.refresh("kb")
        self.assertEqual((code, out["added"]), (0, 2))

    def test_kb_dir_without_an_index_is_not_a_knowledge_base(self):
        (Path(self.root) / ".tutor" / "kb" / "empty").mkdir(parents=True)
        self.assertEqual(self.refresh("kb")[1]["added"], 0)


class QuizEntities(SnapshotBase):
    def setUp(self):
        super().setUp()
        self.define()
        self.write_bank({
            "q1": {"prompt": "What is osmosis?", "answer": "diffusion of water across a membrane",
                   "explanation": "through a semipermeable membrane", "difficulty": "easy"},
            "q2": {"prompt": "Unanswered question?", "answer": "SECRET-ANSWER-TEXT",
                   "explanation": "SECRET-EXPLANATION", "difficulty": "hard"}})

    def entities(self):
        return {e.id: e for e in memory.read_quiz_entities(memory_args(self.root)).entities}

    def test_each_quiz_or_review_attempt_is_an_entity_and_feynman_is_not(self):
        self.attempt(T0, qid="q1", id="a1")
        self.attempt(T0 + 1, qid="q1", id="a2", assessment_type="review", result="incorrect", answer="wrong")
        self.attempt(T0 + 2, qid="", id="a3", assessment_type="qualitative")
        ents = self.entities()
        self.assertEqual(sorted(ents), ["a1", "a2"])
        a1 = ents["a1"]
        self.assertIn("diffusion of water across a membrane", a1.content)
        self.assertIn("semipermeable", a1.content)
        self.assertEqual(a1.metadata["difficulty"], "easy")
        self.assertTrue(a1.metadata["is_correct"])
        self.assertEqual(a1.label, "What is osmosis?")
        self.assertFalse(ents["a2"].metadata["is_correct"])

    def test_ae11_an_unanswered_question_and_its_answer_never_appear(self):
        self.attempt(T0, qid="q1", id="a1")
        code, out = self.refresh("quiz")
        self.assertEqual(out["added"], 1)
        blob = json.dumps([e.to_dict() for e in self.entities().values()], ensure_ascii=False) \
            + self.changes_bytes("quiz").decode("utf-8") + (self.mem() / "snapshot" / "quiz" / "state.json").read_text()
        self.assertNotIn("Unanswered question?", blob)
        self.assertNotIn("SECRET-ANSWER-TEXT", blob)
        self.assertNotIn("SECRET-EXPLANATION", blob)

    def test_attempt_whose_question_is_not_in_the_bank_keeps_event_fields_only(self):
        self.attempt(T0, qid="gone", id="a1", question="Inline question text")
        e = self.entities()["a1"]
        self.assertIn("Inline question text", e.content)
        self.assertNotIn("Correct answer", e.content)
        self.assertIsNone(e.metadata["difficulty"])

    def test_missing_bank_is_fine(self):
        (Path(self.root) / ".tutor" / "question_bank" / f"{KB}.json").unlink()
        self.attempt(T0, qid="q1", id="a1")
        self.assertEqual(list(self.entities()), ["a1"])

    def test_one_damaged_ledger_line_is_a_degraded_read_that_records_no_removals(self):
        self.attempt(T0, qid="q1", id="a1")
        self.attempt(T0 + 1, qid="q1", id="a2", answer="another")
        self.assertEqual(self.refresh("quiz")[1]["added"], 2)
        log = Path(self.root) / ".tutor" / "learner" / "default" / "events.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        damaged = [l if '"id": "a2"' not in l and '"id":"a2"' not in l else "{broken json" for l in lines]
        log.write_text("\n".join(damaged) + "\n", encoding="utf-8")
        self.attempt(T0 + 2, qid="q1", id="a3", answer="third")
        code, out = self.refresh("quiz", now=T0 + 10)
        self.assertEqual(code, 0, out)
        self.assertEqual((out["added"], out["removed"]), (1, 0))
        self.assertTrue(out["warnings"])
        self.assertIn("a2", self.state("quiz")["fingerprints"])
        self.assertNotIn('"kind":"removed"', self.changes_bytes("quiz").decode("utf-8"))

    def test_a_ledger_kb_name_with_path_parts_never_reaches_the_filesystem(self):
        self.ledger({"type": "kp_define", "kb": "../outside", "name": "Evil", "knowledge_type": "concept", "sources": []})
        secret = Path(self.root) / ".tutor" / "outside.json"
        secret.write_text(json.dumps({"questions": {"q1": {"prompt": "LEAKED", "answer": "LEAKED-ANSWER"}}}), encoding="utf-8")
        self.attempt(T0, qid="q1", id="e1", kp="../outside::evil")
        result = memory.read_quiz_entities(memory_args(self.root))
        self.assertTrue(result.warnings)
        self.assertNotIn("LEAKED", json.dumps([e.to_dict() for e in result.entities]))

    def test_the_other_learners_ledger_is_not_used(self):
        self.define(learner="bob")
        self.attempt(T0, qid="q1", id="b1", learner="bob")
        self.assertEqual(self.entities(), {})


class Recall(SnapshotBase):
    def test_recent_lists_chat_and_quiz_with_days_ago_and_skips_kb(self):
        self.define()
        self.write_bank({"q1": {"prompt": "What is osmosis?", "answer": "a"}})
        self.attempt(T0 - 2 * DAY, qid="q1", id="a1")
        self.run_cli("chat-put", "--call-id", "c1", payload={"question": "讲讲扩散"}, now=T0 - DAY)
        self.make_kb("bio", [("a.pdf", "s", T0 - DAY)])
        out = self.ok("recent", now=T0)
        self.assertEqual([(h["surface"], h["label"], h["days_ago"]) for h in out["hits"]],
                         [("chat", "讲讲扩散", 1), ("quiz", "What is osmosis?", 2)])
        self.assertEqual(self.ok("recent", "--surface", "quiz", now=T0)["hits"][0]["surface"], "quiz")
        self.assertEqual(self.ok("recent", "--days", "1", "--limit", "5", now=T0 + 3 * DAY)["hits"], [])

    def test_ae12_five_identical_queries_in_a_week_give_one_hit_and_old_ones_drop(self):
        for i in range(5):
            self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "渗透作用是什么"}},
                    now=T0 - 5 * DAY + i * DAY)
        self.ok("emit", "--surface", "kb", payload={"kind": "query", "payload": {"query": "很久以前"}}, now=T0 - 30 * DAY)
        self.ok("emit", "--surface", "kb", payload={"kind": "other", "payload": {"query": "x"}}, now=T0 - DAY)
        out = self.ok("recent-queries", now=T0)
        self.assertEqual([(h["label"], h["days_ago"]) for h in out["hits"]], [("渗透作用是什么", 1)])
        wide = self.ok("recent-queries", "--days", "60", now=T0)
        self.assertEqual([h["label"] for h in wide["hits"]], ["渗透作用是什么", "很久以前"])

    def test_recent_reports_a_failing_surface_as_a_warning_and_still_answers(self):
        self.run_cli("chat-put", "--call-id", "c1", payload={"question": "q"}, now=T0 - DAY)
        (self.mem() / "entities" / "chat" / "learn-bad.json").write_text("{broken", encoding="utf-8")
        out = self.ok("recent", now=T0)
        self.assertTrue(out["warnings"])
        self.assertEqual(out["hits"], [])  # the chat surface failed as a whole; quiz is empty

    def test_commands_never_touch_the_ledger_or_bank_or_index(self):
        self.define()
        self.write_bank({"q1": {"prompt": "p", "answer": "a"}})
        self.attempt(T0, qid="q1", id="a1")
        index = self.make_kb("bio", [("a.pdf", "s", T0)])
        watched = [Path(self.root) / ".tutor" / "learner" / "default" / "events.jsonl",
                   Path(self.root) / ".tutor" / "question_bank" / f"{KB}.json", index]
        before = [p.read_bytes() for p in watched]
        for surface in ("chat", "quiz", "kb"):
            self.refresh(surface)
        self.ok("recent", now=T0)
        self.ok("recent-queries", now=T0)
        self.run_cli("snapshot", "preview", "--surface", "quiz", now=T0)
        self.assertEqual([p.read_bytes() for p in watched], before)


def memory_args(root, learner="default", now=None):
    return type("Args", (), {"root": root, "learner": learner, "now": now})()


if __name__ == "__main__":
    unittest.main()
