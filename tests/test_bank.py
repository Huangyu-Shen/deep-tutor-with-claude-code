"""Question bank storage and CLI contract."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bank  # noqa: E402


class BankTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.kb = "course"
        self.kp = "course::photosynthesis"

    def tearDown(self):
        self.tmp.cleanup()

    def question(self, question_id="20261109-q1", **overrides):
        item = {
            "question_id": question_id,
            "kp": self.kp,
            "knowledge_type": "concept",
            "format": "short_answer",
            "difficulty": "medium",
            "source_type": "quiz",
            "answer_status": "sourced",
            "prompt": "Explain the light reactions of photosynthesis in detail.",
            "answer": "Light energy drives electron transport and makes ATP and NADPH.",
            "explanation": "The light reactions happen in the thylakoid membrane.",
            "checklist": ["electron transport", "ATP and NADPH"],
            "source": {"source_id": "lecture.pdf", "display": "lecture.pdf", "page": 3,
                       "quote": "The light reactions produce ATP and NADPH."},
        }
        item.update(overrides)
        return item

    def reference(self, reference_id="ref-1", **overrides):
        item = {
            "reference_id": reference_id,
            "kp": self.kp,
            "format": "short_answer",
            "difficulty": "medium",
            "prompt": "Why do the light reactions produce ATP and NADPH?",
            "source": {"source_id": "lecture.pdf", "display": "lecture.pdf", "page": 3,
                       "quote": "The light reactions produce ATP and NADPH."},
        }
        item.update(overrides)
        return item

    def run_cli(self, *argv, payload=None):
        out = io.StringIO()
        stdin = io.StringIO(json.dumps(payload) if payload is not None else "")
        with contextlib.redirect_stdout(out):
            code = bank.main(["--root", self.root, *argv], stdin=stdin)
        return code, json.loads(out.getvalue())

    def add(self, *questions, allow_near_duplicate=False):
        return self.run_cli("add", "--kb", self.kb,
                            payload={"questions": list(questions),
                                     "allow_near_duplicate": allow_near_duplicate})

    def bank_file(self):
        return Path(self.root) / ".tutor" / "question_bank" / f"{self.kb}.json"

    def test_add_creates_versioned_file(self):
        code, out = self.add(self.question())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], ["20261109-q1"])
        data = json.loads(self.bank_file().read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["kb"], self.kb)
        stored = data["questions"]["20261109-q1"]
        self.assertEqual(stored["prompt"], self.question()["prompt"])
        self.assertTrue(stored["citation_ok"])
        self.assertIsNone(stored["invalidated_at"])
        self.assertTrue(stored["added_at"])

    def test_add_accepts_the_quiz_file_as_a_bare_list(self):
        code, out = self.run_cli("add", "--kb", self.kb, payload=[self.question()])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], ["20261109-q1"])

    def test_readding_same_payload_is_idempotent(self):
        self.add(self.question())
        code, out = self.add(self.question())
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], [])
        self.assertEqual(out["existing"], ["20261109-q1"])

    def test_same_id_different_content_is_rejected(self):
        self.add(self.question())
        variants = [
            self.question(prompt="A completely different question about leaves."),
            self.question(answer="A different key."),
            self.question(checklist=["something else"]),
        ]
        for variant in variants:
            code, out = self.add(variant)
            self.assertEqual(code, 1, out)
            self.assertIn("already exists with different content", out["error"])
        data = json.loads(self.bank_file().read_text(encoding="utf-8"))
        self.assertIn("light reactions", data["questions"]["20261109-q1"]["prompt"])

    def test_exact_duplicate_under_another_id_is_rejected_without_losing_the_batch(self):
        self.add(self.question())
        duplicate = self.question("20261109-q2",
                                  prompt="  EXPLAIN the light reactions of photosynthesis, in detail!  ")
        clean = self.question("20261109-q3", prompt="What does the Calvin cycle fix?")
        code, out = self.add(duplicate, clean)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], ["20261109-q3"])
        self.assertEqual(out["rejected"][0]["question_id"], "20261109-q2")
        self.assertEqual(out["rejected"][0]["matches"][0]["question_id"], "20261109-q1")
        stored = json.loads(self.bank_file().read_text())["questions"]
        self.assertIn("20261109-q3", stored)
        self.assertNotIn("20261109-q2", stored)

    def test_near_duplicate_is_rejected_unless_allowed(self):
        self.add(self.question())
        similar = self.question("20261109-q2",
                                prompt="Explain the light reactions of photosynthesis in details.")
        code, out = self.add(similar)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], [])
        self.assertEqual(out["near_duplicates"][0]["question_id"], "20261109-q2")
        self.assertEqual(out["near_duplicates"][0]["matches"][0]["question_id"], "20261109-q1")
        code, out = self.add(similar, allow_near_duplicate=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], ["20261109-q2"])

    def test_a_near_duplicate_does_not_block_clean_questions_in_the_same_batch(self):
        self.add(self.question())
        similar = self.question("20261109-q2",
                                prompt="Explain the light reactions of photosynthesis in details.")
        clean = self.question("20261109-q3", prompt="What does the Calvin cycle fix?")
        code, out = self.add(similar, clean)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], ["20261109-q3"])
        self.assertEqual([item["question_id"] for item in out["near_duplicates"]],
                         ["20261109-q2"])
        stored = json.loads(self.bank_file().read_text())["questions"]
        self.assertEqual(sorted(stored), ["20261109-q1", "20261109-q3"])

    def test_an_invalid_question_still_fails_the_whole_batch(self):
        good = self.question()
        bad = self.question("20261109-q2", prompt="", kp=self.kp)
        code, out = self.add(good, bad)
        self.assertEqual(code, 1, out)
        self.assertIn("prompt", out["error"])
        self.assertFalse(self.bank_file().exists())

    def test_list_filters_and_hides_answers(self):
        self.run_cli("add-reference", "--kb", self.kb,
                     payload={"references": [self.reference(format="fill_in_blank",
                                                            difficulty="hard")]})
        self.add(self.question())
        self.add(self.question("20261109-m1", format="fill_in_blank", difficulty="hard",
                               source_type="mimic", answer_status="unverified",
                               reference_id="ref-1",
                               prompt="The light reactions make ____ and NADPH.",
                               options=None))
        code, out = self.run_cli("list", "--kb", self.kb, "--format", "fill_in_blank")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["count"], 1)
        item = out["questions"][0]
        self.assertEqual(item["question_id"], "20261109-m1")
        for key in ("answer", "explanation", "checklist"):
            self.assertNotIn(key, item)
        self.assertEqual(item["answer_status"], "unverified")
        self.assertEqual(item["source"]["quote"], "The light reactions produce ATP and NADPH.")

        code, out = self.run_cli("list", "--kb", self.kb, "--source-type", "mimic")
        self.assertEqual(out["count"], 1)
        code, out = self.run_cli("list", "--kb", self.kb, "--answer-status", "sourced")
        self.assertEqual(out["count"], 1)
        code, out = self.run_cli("list", "--kb", self.kb, "--difficulty", "hard")
        self.assertEqual([q["question_id"] for q in out["questions"]], ["20261109-m1"])
        code, out = self.run_cli("list", "--kb", self.kb, "--kp", self.kp)
        self.assertEqual(out["count"], 2)
        code, out = self.run_cli("list", "--kb", self.kb, "--with-answers")
        self.assertEqual(code, 0, out)
        self.assertTrue(all("answer" in q for q in out["questions"]))

    def test_get_hides_answers_until_requested(self):
        self.add(self.question())
        code, out = self.run_cli("get", "--kb", self.kb, "--question-id", "20261109-q1")
        self.assertEqual(code, 0, out)
        self.assertNotIn("answer", out["question"])
        code, out = self.run_cli("get", "--kb", self.kb, "--question-id", "20261109-q1",
                                 "--with-answers")
        self.assertEqual(out["question"]["answer"], self.question()["answer"])

    def test_pick_skips_invalid_questions_and_honours_excludes(self):
        self.add(self.question(), self.question("20261109-q2",
                                                prompt="Why does the Calvin cycle need ATP?"))
        code, out = self.run_cli("invalidate", "--kb", self.kb, "--question-id", "20261109-q1")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("pick", "--kb", self.kb, "--limit", "5")
        self.assertEqual(code, 0, out)
        self.assertEqual([q["question_id"] for q in out["questions"]], ["20261109-q2"])
        self.run_cli("revalidate", "--kb", self.kb, "--question-id", "20261109-q1")
        code, out = self.run_cli("pick", "--kb", self.kb, "--limit", "5",
                                 "--exclude-question-id", "20261109-q2")
        self.assertEqual([q["question_id"] for q in out["questions"]], ["20261109-q1"])

    def test_pick_filters_and_hides_answers(self):
        self.add(
            self.question(),
            self.question("20261109-q2", kp="course::calvin_cycle", format="fill_in_blank",
                          difficulty="easy", prompt="The Calvin cycle fixes ____."),
            self.question("20261109-q3", kp="course::calvin_cycle", format="multiple_choice",
                          difficulty="hard", options=["A", "B", "C"],
                          prompt="Which product leaves the Calvin cycle?"),
        )
        code, out = self.run_cli("pick", "--kb", self.kb, "--kp", "course::calvin_cycle",
                                 "--limit", "5")
        self.assertEqual([q["question_id"] for q in out["questions"]],
                         ["20261109-q2", "20261109-q3"])
        code, out = self.run_cli("pick", "--kb", self.kb, "--difficulty", "hard")
        self.assertEqual([q["question_id"] for q in out["questions"]], ["20261109-q3"])
        code, out = self.run_cli("pick", "--kb", self.kb, "--format", "fill_in_blank")
        self.assertEqual([q["question_id"] for q in out["questions"]], ["20261109-q2"])
        code, out = self.run_cli("pick", "--kb", self.kb, "--source-type", "quiz")
        self.assertEqual(out["count"], 1)
        for item in out["questions"]:
            for key in ("answer", "explanation", "checklist"):
                self.assertNotIn(key, item)
        code, out = self.run_cli("pick", "--kb", self.kb, "--with-answers")
        self.assertIn("answer", out["questions"][0])

    def test_invalidate_keeps_the_first_timestamp_and_revalidate_clears_it(self):
        self.add(self.question())
        _, first = self.run_cli("invalidate", "--kb", self.kb, "--question-id", "20261109-q1")
        _, again = self.run_cli("invalidate", "--kb", self.kb, "--question-id", "20261109-q1")
        self.assertEqual(first["invalidated_at"], again["invalidated_at"])
        _, out = self.run_cli("revalidate", "--kb", self.kb, "--question-id", "20261109-q1")
        self.assertTrue(out["citation_ok"])
        self.assertIsNone(out["invalidated_at"])

    def test_unknown_schema_version_is_an_error_and_keeps_the_file(self):
        self.bank_file().parent.mkdir(parents=True)
        original = json.dumps({"schema_version": 99, "kb": self.kb,
                               "questions": {}, "references": {}})
        self.bank_file().write_text(original, encoding="utf-8")
        code, out = self.add(self.question())
        self.assertEqual(code, 1, out)
        self.assertIn("unsupported schema_version", out["error"])
        self.assertEqual(self.bank_file().read_text(encoding="utf-8"), original)

    def test_corrupt_json_is_an_error(self):
        self.bank_file().parent.mkdir(parents=True)
        self.bank_file().write_text("{not json", encoding="utf-8")
        code, out = self.run_cli("list", "--kb", self.kb)
        self.assertEqual(code, 1, out)
        self.assertIn("damaged", out["error"])
        self.assertEqual(self.bank_file().read_text(encoding="utf-8"), "{not json")

    def test_mimic_needs_an_existing_reference(self):
        mimic = self.question("20261109-m1", source_type="mimic", answer_status="unverified",
                              reference_id="missing", prompt="A mimicked question?")
        code, out = self.add(mimic)
        self.assertEqual(code, 1, out)
        self.assertIn("unknown reference", out["error"])
        self.run_cli("add-reference", "--kb", self.kb,
                     payload={"references": [self.reference()]})
        mimic["reference_id"] = "ref-1"
        code, out = self.add(mimic)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], ["20261109-m1"])

    def test_reference_must_share_the_question_knowledge_point(self):
        self.run_cli("add-reference", "--kb", self.kb, payload={"references": [self.reference()]})
        mimic = self.question("20261109-m1", source_type="mimic", answer_status="unverified",
                              reference_id="ref-1", kp="course::calvin_cycle",
                              prompt="A mimicked question?")
        code, out = self.add(mimic)
        self.assertEqual(code, 1, out)
        self.assertIn("belongs to", out["error"])

    def test_mimic_must_keep_the_reference_format_and_difficulty(self):
        self.run_cli("add-reference", "--kb", self.kb, payload={"references": [self.reference()]})
        for overrides in ({"format": "multiple_choice", "options": ["A", "B"]},
                          {"difficulty": "hard"}):
            mimic = self.question("20261109-m1", source_type="mimic", answer_status="unverified",
                                  reference_id="ref-1", prompt="A mimicked question?",
                                  **overrides)
            code, out = self.add(mimic)
            self.assertEqual(code, 1, out)
            self.assertIn("format and difficulty", out["error"])

    def test_kp_must_be_spelled_the_way_the_ledger_spells_it(self):
        code, out = self.add(self.question(kp="course::Photosynthesis"))
        self.assertEqual(code, 1, out)
        self.assertIn("course::photosynthesis", out["error"])

    def test_a_damaged_entry_is_rejected_at_read_time(self):
        self.bank_file().parent.mkdir(parents=True)
        self.bank_file().write_text(json.dumps({
            "schema_version": 1, "kb": self.kb,
            "questions": {"q1": {"question_id": "q1"}}, "references": {},
        }), encoding="utf-8")
        for argv in (("list", "--kb", self.kb), ("pick", "--kb", self.kb)):
            code, out = self.run_cli(*argv)
            self.assertEqual(code, 1, out)
            self.assertIn("damaged", out["error"])
        code, out = self.add(self.question())
        self.assertEqual(code, 1, out)
        self.assertIn("damaged", out["error"])

    def test_add_reference_is_idempotent(self):
        first = {"references": [self.reference(confirmed_at="2026-11-09T00:00:00Z")]}
        second = {"references": [self.reference(confirmed_at="2026-11-10T00:00:00Z")]}
        self.run_cli("add-reference", "--kb", self.kb, payload=first)
        code, out = self.run_cli("add-reference", "--kb", self.kb, payload=second)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], [])
        self.assertEqual(out["existing"], ["ref-1"])
        data = json.loads(self.bank_file().read_text(encoding="utf-8"))
        self.assertEqual(len(data["references"]), 1)
        self.assertEqual(data["references"]["ref-1"]["confirmed_at"], "2026-11-09T00:00:00Z")

    def test_ordinary_quiz_cannot_be_unverified(self):
        code, out = self.add(self.question(answer_status="unverified"))
        self.assertEqual(code, 1, out)
        self.assertIn("answer_status='sourced'", out["error"])

    def test_kp_must_belong_to_the_bank(self):
        code, out = self.add(self.question(kp="other::photosynthesis"))
        self.assertEqual(code, 1, out)
        self.assertIn("does not belong", out["error"])

    def test_missing_bank_reads_as_empty(self):
        code, out = self.run_cli("list", "--kb", self.kb)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["count"], 0)
        self.assertFalse(self.bank_file().exists())

    def test_multiple_choice_requires_options(self):
        code, out = self.add(self.question(format="multiple_choice"))
        self.assertEqual(code, 1, out)
        self.assertIn("options are required", out["error"])
        code, out = self.add(self.question(format="multiple_choice",
                                           options=["A", "B", "C"]))
        self.assertEqual(code, 0, out)

    def test_empty_batch_is_rejected(self):
        code, out = self.run_cli("add", "--kb", self.kb, payload={"questions": []})
        self.assertEqual(code, 1, out)
        self.assertIn("non-empty list", out["error"])

    def test_references_listing_filters(self):
        self.run_cli("add-reference", "--kb", self.kb, payload={"references": [
            self.reference(),
            self.reference("ref-2", format="multiple_choice", difficulty="hard"),
        ]})
        code, out = self.run_cli("references", "--kb", self.kb, "--difficulty", "hard")
        self.assertEqual(code, 0, out)
        self.assertEqual([r["reference_id"] for r in out["references"]], ["ref-2"])

    def test_short_answer_may_carry_only_a_checklist(self):
        code, out = self.add(self.question(answer=None))
        self.assertEqual(code, 0, out)
        _, got = self.run_cli("get", "--kb", self.kb, "--question-id", "20261109-q1",
                              "--with-answers")
        self.assertIsNone(got["question"]["answer"])
        self.assertEqual(got["question"]["checklist"], ["electron transport", "ATP and NADPH"])

    def test_short_answer_without_answer_or_checklist_is_rejected(self):
        code, out = self.add(self.question(answer=None, checklist=None))
        self.assertEqual(code, 1, out)
        self.assertIn("answer or a checklist", out["error"])

    def test_multiple_choice_requires_an_answer(self):
        code, out = self.add(self.question(format="multiple_choice", answer=None,
                                           options=["A", "B", "C"]))
        self.assertEqual(code, 1, out)
        self.assertIn("answer must be a non-empty string", out["error"])

    def test_prompts_differing_only_in_control_whitespace_are_duplicates(self):
        self.add(self.question(prompt="A B"))
        code, out = self.add(self.question("20261109-q2", prompt="A\nB"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["added"], [])
        self.assertEqual(out["rejected"][0]["reason"], "exact_duplicate")
        self.assertEqual(out["rejected"][0]["matches"][0]["question_id"], "20261109-q1")

    def test_schema_version_must_be_a_real_integer(self):
        self.bank_file().parent.mkdir(parents=True)
        for version in (True, 1.0):
            with self.subTest(version=version):
                original = json.dumps({"schema_version": version, "kb": self.kb,
                                       "questions": {}, "references": {}})
                self.bank_file().write_text(original, encoding="utf-8")
                code, out = self.run_cli("list", "--kb", self.kb)
                self.assertEqual(code, 1, out)
                self.assertIn("unsupported schema_version", out["error"])

    def test_allow_near_duplicate_must_be_a_boolean(self):
        code, out = self.run_cli("add", "--kb", self.kb,
                                 payload={"questions": [self.question()],
                                          "allow_near_duplicate": "false"})
        self.assertEqual(code, 1, out)
        self.assertIn("allow_near_duplicate", out["error"])
        self.assertFalse(self.bank_file().exists())

    def test_missing_or_non_boolean_citation_ok_is_damaged(self):
        self.add(self.question())
        original = json.loads(self.bank_file().read_text(encoding="utf-8"))
        for bad in ("yes", None, 0):
            with self.subTest(bad=bad):
                data = json.loads(json.dumps(original))
                data["questions"]["20261109-q1"]["citation_ok"] = bad
                self.bank_file().write_text(json.dumps(data), encoding="utf-8")
                code, out = self.run_cli("pick", "--kb", self.kb)
                self.assertEqual(code, 1, out)
                self.assertIn("citation_ok", out["error"])


if __name__ == "__main__":
    unittest.main()
