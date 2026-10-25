import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ledger  # noqa: E402
import learner_log  # noqa: E402
import scheduler as sch  # noqa: E402

T0 = 1_700_000_000.0
DAY = 86400.0
KB = "bio"
NAME = "Photosynthesis  Light Reactions"
KP = ledger.kp_id(KB, NAME)


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def cli(self, *argv, event=None, now=None):
        args = ["--root", self.root]
        if now is not None:
            args += ["--now", str(now)]
        args += list(argv)
        out = io.StringIO()
        stdin = io.StringIO(json.dumps(event) if event is not None else "")
        with contextlib.redirect_stdout(out):
            code = ledger.main(args, stdin=stdin)
        return code, json.loads(out.getvalue())

    def ok(self, *argv, **kw):
        code, data = self.cli(*argv, **kw)
        self.assertEqual(code, 0, data)
        self.assertTrue(data["ok"])
        return data

    def err(self, *argv, **kw):
        code, data = self.cli(*argv, **kw)
        self.assertEqual(code, 1, data)
        self.assertFalse(data["ok"])
        return data["error"]

    def define(self, ktype="concept", ts=T0):
        return self.ok("record", event={"type": "kp_define", "kb": KB, "name": NAME,
                                        "knowledge_type": ktype, "sources": ["notes.pdf p.3"], "ts": ts})

    def attempt(self, result, ts, qid="q1", **extra):
        ev = {"type": "attempt", "kp": KP, "result": result, "ts": ts, "question_id": qid, **extra}
        return self.ok("record", event=ev)

    def log_file(self):
        return Path(self.root) / ".tutor" / "learner" / "default" / "events.jsonl"


class RecordValidation(LedgerCase):
    def test_kp_id_is_normalized(self):
        self.assertEqual(KP, "bio::photosynthesis light reactions")

    def test_unknown_kp_rejected_and_not_written(self):
        err = self.err("record", event={"type": "attempt", "kp": KP, "result": "correct"})
        self.assertIn("unknown kp", err)
        self.assertFalse(self.log_file().exists())

    def test_bad_values_rejected(self):
        self.define()
        bad = [
            {"type": "attempt", "kp": KP, "result": "maybe"},
            {"type": "attempt", "kp": KP, "result": "correct", "quality": 1.5},
            {"type": "attempt", "kp": KP, "result": "correct", "hints_used": -1},
            {"type": "attempt", "kp": KP, "result": "correct", "error_type": "application"},
            {"type": "attempt", "kp": KP, "result": "incorrect", "error_type": "silly"},
            {"type": "attempt", "kp": KP, "result": "correct", "ts": "yesterday"},
            {"type": "nonsense"},
        ]
        for event in bad:
            with self.subTest(event):
                self.err("record", event=event)
        self.assertEqual(len(self.log_file().read_text().splitlines()), 1)

    def test_kp_define_checks(self):
        self.err("record", event={"type": "kp_define", "kb": KB, "name": "x", "knowledge_type": "vibes"})
        self.define("concept")
        self.assertIn("cannot change", self.err("record", event={
            "type": "kp_define", "kb": KB, "name": NAME, "knowledge_type": "memory"}))

    def test_duplicate_id_rejected(self):
        self.define()
        self.attempt("correct", T0, id="fixed")
        err = self.err("record", event={"type": "attempt", "kp": KP, "result": "correct", "id": "fixed"})
        self.assertIn("duplicate", err)

    def test_blank_wrong_answer_is_metacognitive_else_application(self):
        self.define()
        self.attempt("incorrect", T0, qid="a", learner_answer="  ")
        self.attempt("incorrect", T0 + 1, qid="b", learner_answer="chlorophyll is a gas")
        by_q = {e["question_id"]: e["error_type"] for e in self.ok("errors")["errors"]}
        self.assertEqual(by_q, {"a": "metacognitive", "b": "application"})

    def test_invalid_stdin(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ledger.main(["--root", self.root, "record"], stdin=io.StringIO("{nope"))
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", json.loads(out.getvalue())["error"])

    def test_learner_name_cannot_escape_directory(self):
        self.assertIn("learner name", self.err("--learner", "../x", "state"))


class ReplayAndSchedule(LedgerCase):
    def test_state_matches_scheduler_replay_exactly(self):
        self.define("concept")
        results = [("correct", 0), ("correct", 1), ("incorrect", 5), ("correct", 6), ("correct", 12)]
        for i, (res, day) in enumerate(results):
            self.attempt(res, T0 + day * DAY, qid=f"q{i}")
        state = self.ok("state", now=T0 + 20 * DAY)["knowledge_points"][0]
        evidence = [
            sch.LearningEvidence(knowledge_point_id=KP, timestamp=T0 + d * DAY, result=r, source="deep_question",
                                 question_id=f"q{i}")
            for i, (r, d) in enumerate(results)
        ]
        expected = sch.SpacedRepetitionScheduler().replay("concept", evidence)
        for key in ("stability", "difficulty", "next_review_at", "lapse_count", "review_count"):
            self.assertAlmostEqual(state["schedule"][key], getattr(expected, key), places=9, msg=key)

    def test_replay_is_deterministic_and_read_only(self):
        self.define()
        self.attempt("correct", T0)
        self.attempt("incorrect", T0 + DAY)
        before = self.log_file().read_bytes()
        a = self.ok("state", now=T0 + 3 * DAY)
        b = self.ok("state", now=T0 + 3 * DAY)
        self.ok("due", now=T0 + 3 * DAY)
        self.assertEqual(a, b)
        self.assertEqual(before, self.log_file().read_bytes())

    def test_mastery_capped_with_little_evidence(self):
        self.define()
        self.attempt("correct", T0)
        self.assertAlmostEqual(self.ok("state", now=T0)["knowledge_points"][0]["mastery"], 0.5)

    def test_out_of_order_timestamp_is_accepted(self):
        self.define()
        self.attempt("correct", T0 + 5 * DAY)
        self.attempt("correct", T0 + 2 * DAY, qid="q2")
        self.assertEqual(self.ok("state", now=T0 + 6 * DAY)["knowledge_points"][0]["attempts"], 2)

    def test_kp_without_attempts_is_listed_but_not_scheduled(self):
        self.define()
        kp = self.ok("state")["knowledge_points"][0]
        self.assertNotIn("schedule", kp)
        self.assertEqual(self.ok("due")["tracked_count"], 0)


class ErrorLifecycle(LedgerCase):
    def statuses(self):
        return [e["status"] for e in self.ok("errors")["errors"]]

    def test_wrong_then_two_correct_graduates(self):
        self.define()
        self.attempt("incorrect", T0)
        self.assertEqual(self.statuses(), ["active"])
        self.attempt("incorrect", T0 + DAY)
        self.assertEqual(self.statuses(), ["retrying"])
        self.attempt("correct", T0 + 2 * DAY)
        self.assertEqual(self.statuses(), ["review"])
        self.attempt("correct", T0 + 9 * DAY)
        self.assertEqual(self.statuses(), ["graduated"])
        self.attempt("incorrect", T0 + 30 * DAY)
        self.assertEqual(self.statuses(), ["retrying"])

    def test_wrong_between_corrects_resets_streak(self):
        self.define()
        self.attempt("incorrect", T0)
        self.attempt("correct", T0 + DAY)
        self.attempt("incorrect", T0 + 2 * DAY)
        self.attempt("correct", T0 + 3 * DAY)
        self.assertEqual(self.statuses(), ["review"])

    def test_errors_are_per_question_and_filterable(self):
        self.define()
        self.attempt("incorrect", T0, qid="a")
        self.attempt("incorrect", T0 + 1, qid="b")
        self.attempt("correct", T0 + DAY, qid="a")
        self.assertEqual(len(self.ok("errors")["errors"]), 2)
        self.assertEqual(len(self.ok("errors", "--status", "active")["errors"]), 1)

    def test_open_error_gets_top_priority_until_it_graduates(self):
        self.define()
        self.attempt("correct", T0)
        self.assertEqual(self.ok("due", now=T0 + 30 * DAY)["queue"][0]["priority"], 3)  # concept
        self.attempt("incorrect", T0 + 29 * DAY, qid="z")
        item = self.ok("due", now=T0 + 60 * DAY)["queue"][0]
        self.assertEqual(item["priority"], 1)
        self.assertIn("lapse", item["reason"])
        self.attempt("correct", T0 + 61 * DAY, qid="z")
        self.attempt("correct", T0 + 70 * DAY, qid="z")
        self.assertEqual(self.ok("due", now=T0 + 400 * DAY)["queue"][0]["priority"], 3)


class DueQueue(LedgerCase):
    def test_due_orders_by_risk_and_filters_by_kb_and_limit(self):
        for kb, name in [("bio", "a"), ("bio", "b"), ("chem", "c")]:
            self.ok("record", event={"type": "kp_define", "kb": kb, "name": name, "knowledge_type": "memory", "ts": T0})
        self.ok("record", event={"type": "attempt", "kp": "bio::a", "result": "correct", "ts": T0})
        self.ok("record", event={"type": "attempt", "kp": "bio::b", "result": "incorrect", "ts": T0})
        self.ok("record", event={"type": "attempt", "kp": "chem::c", "result": "correct", "ts": T0 + 40 * DAY})
        now = T0 + 41 * DAY
        due = self.ok("due", now=now)
        self.assertGreaterEqual(due["due_count"], 2)
        risks = [q["forgetting_risk"] for q in due["queue"]]
        self.assertEqual(risks, sorted(risks, reverse=True))
        self.assertEqual({q["kp"] for q in self.ok("due", "--kb", "chem", now=now)["queue"]} - {"chem::c"}, set())
        self.assertEqual(len(self.ok("due", "--limit", "1", now=now)["queue"]), 1)

    def test_not_yet_due_items_are_excluded_but_counted(self):
        self.define("design")
        self.attempt("correct", T0)
        due = self.ok("due", now=T0 + 1 * DAY)
        self.assertEqual((due["due_count"], due["tracked_count"]), (0, 1))


class DamagedLog(LedgerCase):
    def test_bad_lines_are_reported_not_swallowed(self):
        self.define()
        self.attempt("correct", T0, id="good")
        with self.log_file().open("a") as fh:
            fh.write("{broken json\n")
            fh.write(json.dumps({"id": "good", "ts": T0, "type": "attempt", "kp": KP, "result": "correct"}) + "\n")
            fh.write(json.dumps({"id": "orphan", "ts": T0, "type": "attempt", "kp": "nope::x", "result": "correct"}) + "\n")
            fh.write("[1, 2]\n")
        data = self.ok("state", now=T0 + DAY)
        self.assertEqual(data["knowledge_points"][0]["attempts"], 1)
        lines = {w["line"] for w in data["warnings"]}
        self.assertEqual(lines, {3, 4, 5, 6})
        self.assertTrue(all("skipped" in w["problem"] for w in data["warnings"]))

    def test_new_records_still_append_after_damage(self):
        self.define()
        with self.log_file().open("a") as fh:
            fh.write("garbage\n")
        self.attempt("correct", T0)
        self.assertEqual(self.ok("state")["knowledge_points"][0]["attempts"], 1)


class Export(LedgerCase):
    def test_json_and_markdown(self):
        self.define()
        self.attempt("incorrect", T0)
        data = self.ok("export")
        self.assertEqual(len(data["events"]), 2)
        self.assertEqual(len(data["errors"]), 1)
        md = self.ok("export", "--format", "md")["markdown"]
        self.assertIn("Photosynthesis", md.replace("photosynthesis", "Photosynthesis"))
        self.assertIn("status=active", md)

    def test_unicode_survives(self):
        self.ok("record", event={"type": "kp_define", "kb": "物理", "name": "牛顿第三定律", "knowledge_type": "concept"})
        self.assertIn("牛顿第三定律", self.log_file().read_text(encoding="utf-8"))
        self.assertEqual(self.ok("state")["knowledge_points"][0]["name"], "牛顿第三定律")


class ReviewRegressions(LedgerCase):
    """Each test reproduces a defect found in the independent review."""

    def rec(self, event):
        return self.cli("record", event=event)

    def test_null_means_not_given_and_bad_types_are_rejected(self):
        self.define()
        code, data = self.rec({"type": "kp_define", "kb": KB, "name": "Other idea", "knowledge_type": "memory", "sources": None})
        self.assertEqual(code, 0, data)
        for bad in ({"sources": "x"}, {"sources": [1]}):
            code, data = self.rec({"type": "kp_define", "kb": KB, "name": "Third", "knowledge_type": "memory", **bad})
            self.assertEqual(code, 1)
        for field in ("question_id", "learner_answer", "feedback", "question"):
            code, data = self.rec({"type": "attempt", "kp": KP, "result": "correct", field: ["a"]})
            self.assertEqual(code, 1, field)
        self.ok("state")  # the log is still healthy

    def test_non_finite_and_huge_numbers_are_rejected_with_json(self):
        self.define()
        for raw in ('{"type":"attempt","kp":"%s","result":"correct","ts":NaN}' % KP,
                    '{"type":"attempt","kp":"%s","result":"correct","ts":Infinity}' % KP,
                    '{"type":"attempt","kp":"%s","result":"correct","quality":NaN}' % KP,
                    '{"type":"attempt","kp":"%s","result":"correct","hints_used":Infinity}' % KP,
                    '{"type":"attempt","kp":"%s","result":"correct","ts":1%s}' % (KP, "0" * 400)):
            with self.subTest(raw[:60]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = ledger.main(["--root", self.root, "record"], stdin=io.StringIO(raw))
                self.assertEqual(code, 1)
                self.assertFalse(json.loads(out.getvalue())["ok"])
        self.assertEqual(len(self.log_file().read_text().splitlines()), 1)
        self.ok("state")

    def test_non_finite_numbers_already_in_the_file_are_skipped(self):
        self.define()
        with self.log_file().open("a") as fh:
            fh.write('{"id":"n","ts":NaN,"type":"attempt","kp":"%s","result":"correct"}\n' % KP)
        data = self.ok("state")
        self.assertEqual(data["warnings"][0]["line"], 2)
        json.dumps(data, allow_nan=False)

    def test_millisecond_timestamps_are_refused(self):
        self.define()
        self.assertIn("future", self.err("record", event={"type": "attempt", "kp": KP, "result": "correct", "ts": 1.7e12}))

    def test_partial_last_line_does_not_swallow_the_next_event(self):
        self.define()
        with self.log_file().open("a") as fh:
            fh.write('{"id":"zzz","type":"attempt","kp":"x","res')
        self.attempt("correct", T0)
        data = self.ok("state")
        self.assertEqual(data["knowledge_points"][0]["attempts"], 1)
        self.assertEqual([w["line"] for w in data["warnings"]], [2])

    def test_learner_names_that_are_dots_are_refused(self):
        for name in ("..", ".", ".hidden"):
            with self.subTest(name):
                self.assertIn("learner name", self.err("--learner", name, "state"))

    def test_kb_is_stripped_and_unicode_names_are_normalised(self):
        self.ok("record", event={"type": "kp_define", "kb": f" {KB} ", "name": "X idea", "knowledge_type": "memory"})
        self.assertEqual(self.ok("state")["knowledge_points"][0]["kb"], KB)
        nfc, nfd = "caf\u00e9", "cafe\u0301"
        self.assertEqual(ledger.kp_id("k", nfc), ledger.kp_id("k", nfd))

    def test_null_error_type_and_null_answer_get_defaults(self):
        self.define()
        self.ok("record", event={"type": "attempt", "kp": KP, "result": "incorrect", "error_type": None,
                                 "learner_answer": None, "question_id": "q", "ts": T0})
        err = self.ok("errors")["errors"][0]
        self.assertEqual(err["error_type"], "metacognitive")
        self.assertNotIn("None", self.ok("export", "--format", "md")["markdown"])

    def test_mastery_counts_only_unaided_attempts_and_uses_partial_half(self):
        self.define()
        for i in range(3):
            self.attempt("correct", T0 + i * 3600, qid=f"h{i}", hints_used=2)
        kp = self.ok("state", now=T0 + DAY)["knowledge_points"][0]
        self.assertEqual((kp["mastery"], kp["unaided_attempts"], kp["attempts"]), (0.0, 0, 3))
        self.attempt("partial", T0 + 2 * DAY, qid="p", quality=0.9)  # explicit quality does not change mastery input
        kp = self.ok("state", now=T0 + 3 * DAY)["knowledge_points"][0]
        self.assertAlmostEqual(kp["mastery"], 0.5)  # capped at 0.5 with one unaided attempt, and the input was 0.5

    def test_errors_keep_the_question_text(self):
        self.define()
        self.attempt("incorrect", T0, qid="q9", question="What does Rubisco do?")
        self.assertEqual(self.ok("errors")["errors"][0]["question"], "What does Rubisco do?")

    def test_human_readable_dates_and_now_command(self):
        self.define()
        self.attempt("correct", T0)
        due = self.ok("due", now=T0 + 400 * DAY)
        self.assertRegex(due["queue"][0]["due_date"], r"^\d{4}-\d\d-\d\d \d\d:\d\d UTC$")
        self.assertIn("now_iso", due)
        state = self.ok("state", now=T0)
        self.assertIn("next_review_date", state["knowledge_points"][0]["schedule"])
        now = self.ok("now", now=T0)
        self.assertEqual((now["iso"], now["stamp"]), ("2023-11-14 22:13 UTC", "20231114-221320"))

    def test_event_that_breaks_replay_is_not_stored(self):
        self.define()
        before = self.log_file().read_bytes()
        with mock.patch.object(ledger, "build_state", side_effect=RuntimeError("boom")):
            code, data = self.cli("record", event={"type": "attempt", "kp": KP, "result": "correct"})
        self.assertEqual((code, data["ok"]), (1, False))
        self.assertEqual(before, self.log_file().read_bytes())


class AttemptOrigin(LedgerCase):
    def test_origin_is_optional_and_restricted(self):
        self.define()
        for origin in ("learn", "quiz", "review", "diagnostic", "exemption"):
            with self.subTest(origin=origin):
                self.attempt("correct", T0, qid=origin, origin=origin)
        self.assertEqual(self.ok("state")["knowledge_points"][0]["attempts"], 5)
        for bad in ("session", "diagnostic ", "", 5):
            with self.subTest(bad=bad):
                self.assertIn("origin", self.err("record", event={
                    "type": "attempt", "kp": KP, "result": "correct", "origin": bad}))
        self.assertEqual(len(self.log_file().read_text().splitlines()), 6)


class SharedEventLibrary(unittest.TestCase):
    def test_learner_log_is_the_single_implementation(self):
        self.assertIs(ledger.kp_id, learner_log.kp_id)
        self.assertIs(ledger.LedgerError, learner_log.LedgerError)
        self.assertIs(ledger.read_events, learner_log.read_events)

    def test_learner_log_imports_without_ledger_or_path(self):
        code = ("import sys; sys.path.insert(0, %r); import learner_log; "
                "print(learner_log.kp_id('bio', 'X'))" % str(ROOT / "scripts"))
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "bio::x")


if __name__ == "__main__":
    unittest.main()
