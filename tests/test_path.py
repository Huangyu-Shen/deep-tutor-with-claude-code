"""Tests for scripts/path.py: definitions, validation and the path store."""

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kb  # noqa: E402
import learner_log as ll  # noqa: E402
from mastery import compute_mastery  # noqa: E402
import path  # noqa: E402

NOW = 1761400000


def outline(name="Midterm", modules=None):
    if modules is None:
        modules = [
            {"id": "m1", "title": "Foundations", "prerequisites": [], "knowledge_points": [
                {"name": "Photosynthesis", "knowledge_type": "concept",
                 "objective": "Explain the light reactions.",
                 "evidence": [{"source_id": "lecture", "page": 1}]},
                {"name": "Rubisco", "knowledge_type": "memory",
                 "objective": "State its role in carbon fixation.",
                 "prerequisites": ["bio::photosynthesis"],
                 "evidence": [{"source_id": "lecture", "page": 2}]},
            ]},
            {"id": "m2", "title": "Calvin cycle", "prerequisites": ["m1"], "knowledge_points": [
                {"name": "Calvin cycle", "knowledge_type": "concept",
                 "objective": "Trace carbon through the cycle.",
                 "evidence": [{"source_id": "lecture", "page": 3}]},
            ]},
        ]
    return {"name": name, "modules": modules}


def chain_outline(name="Chain"):
    return {"name": name, "modules": [
        {"id": "m1", "title": "Alpha", "prerequisites": [], "knowledge_points": [
            {"name": "Alpha", "knowledge_type": "memory", "objective": "Recall alpha.",
             "evidence": [{"source_id": "lecture", "page": 1}]}]},
        {"id": "m2", "title": "Beta", "prerequisites": ["m1"], "knowledge_points": [
            {"name": "Beta", "knowledge_type": "memory", "objective": "Recall beta.",
             "evidence": [{"source_id": "lecture", "page": 2}]}]},
        {"id": "m3", "title": "Gamma", "prerequisites": ["m2"], "knowledge_points": [
            {"name": "Gamma", "knowledge_type": "memory", "objective": "Recall gamma.",
             "evidence": [{"source_id": "lecture", "page": 3}]}]},
    ]}


def single_outline(name="Rubisco", knowledge_type="memory", page=2):
    return {"name": "Single", "modules": [
        {"id": "m1", "title": "Only", "prerequisites": [], "knowledge_points": [
            {"name": name, "knowledge_type": knowledge_type, "objective": f"Recall {name}.",
             "evidence": [{"source_id": "lecture", "page": page}]}]}]}


class PathCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.clock = NOW + 100
        self.make_kb()

    def tearDown(self):
        self._tmp.cleanup()

    def make_kb(self):
        directory = self.root / ".tutor" / "kb" / "bio"
        directory.mkdir(parents=True)
        conn = sqlite3.connect(directory / "index.sqlite")
        conn.executescript(kb.SCHEMA)
        conn.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                     ("lecture", "lecture.pdf", "/course/lecture.pdf", "abc", "pdf", "page", 4, 0.0))
        pages = [(1, "Photosynthesis converts light energy into chemical energy."),
                 (2, "Rubisco catalyses carbon fixation in the stroma."),
                 (3, "The Calvin cycle fixes carbon dioxide into sugar."),
                 (4, "")]
        for num, text in pages:
            conn.execute("INSERT INTO pages (source_id, num, origin, text, alt_text) VALUES (?,?,?,?,NULL)",
                         ("lecture", num, "text-layer" if text else "empty", text))
        conn.commit()
        conn.close()

    def set_page_text(self, num, text):
        conn = sqlite3.connect(self.root / ".tutor" / "kb" / "bio" / "index.sqlite")
        conn.execute("UPDATE pages SET text = ? WHERE source_id = 'lecture' AND num = ?", (text, num))
        conn.commit()
        conn.close()

    def add_attempt(self, kp, result="correct", ts=None, origin="learn", assessment_type=None, hints=None,
                    question_id=None):
        self.clock += 10
        raw = {"type": "attempt", "kp": kp, "result": result,
               "question_id": question_id or f"q{self.clock}"}
        if ts is not None:
            raw["ts"] = ts
        if origin is not None:
            raw["origin"] = origin
        if assessment_type is not None:
            raw["assessment_type"] = assessment_type
        if hints is not None:
            raw["hints_used"] = hints
        event = ll.prepare_event(raw, self.events(), ts if ts is not None else self.clock)
        log = ll.log_path(str(self.root), "default")
        ll.append_line(log, json.dumps(event))
        return event

    def pass_memory(self, kp):
        for _ in range(3):
            self.add_attempt(kp, "correct")

    def status(self, *extra):
        return self.cli("status", "--kb", "bio", *extra)

    def resolve(self, *extra):
        return self.cli("resolve", "--kb", "bio", *extra)

    def cli(self, *argv, stdin=None):
        payload = io.StringIO(json.dumps(stdin) if stdin is not None else "")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = path.main(["--root", str(self.root), "--now", str(NOW), *argv], stdin=payload)
        return code, json.loads(out.getvalue())

    def save(self, data, *extra):
        return self.cli("save", "--kb", "bio", *extra, stdin=data)

    def store_file(self):
        return self.root / ".tutor" / "path" / "bio" / "paths.json"

    def stored(self):
        return json.loads(self.store_file().read_text(encoding="utf-8"))

    def events(self):
        return ll.read_events(ll.log_path(str(self.root), "default"))[0]

    def assert_rejected(self, data, needle):
        code, result = self.save(data)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn(needle, result["error"])
        self.assertFalse(self.store_file().exists())
        self.assertEqual(self.events(), [])


class Save(PathCase):
    def test_save_creates_store_and_defines_missing_knowledge_points(self):
        code, result = self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        self.assertEqual((code, result["ok"]), (0, True))
        self.assertTrue(result["created"])
        self.assertEqual(result["active_path_id"], result["path_id"])
        self.assertEqual([k["kp"] for k in result["kps_defined"]],
                         ["bio::photosynthesis", "bio::rubisco", "bio::calvin cycle"])
        stored = self.stored()
        self.assertEqual(stored["schema"], 1)
        self.assertEqual(stored["kb"], "bio")
        self.assertEqual(stored["active_path_id"], result["path_id"])
        self.assertEqual(stored["paths"][0]["modules"][0]["knowledge_points"][1]["prerequisites"],
                         ["bio::photosynthesis"])
        defines = [e for e in self.events() if e["type"] == "kp_define"]
        self.assertEqual([e["knowledge_type"] for e in defines], ["concept", "memory", "concept"])
        self.assertEqual(defines[0]["sources"], ["lecture.pdf page 1"])
        self.assertEqual(result["summary"], {"modules": 2, "knowledge_points": 3})

    def test_overwrite_by_id_is_idempotent_and_keeps_created_at(self):
        _code, first = self.save(outline("Midterm"), "--activate")
        data = outline("Midterm")
        data["id"] = first["path_id"]
        code, second = self.save(data)
        self.assertEqual((code, second["ok"]), (0, True))
        self.assertFalse(second["created"])
        self.assertEqual(second["path_id"], first["path_id"])
        self.assertEqual(second["kps_defined"], [])
        stored = self.stored()
        self.assertEqual(len(stored["paths"]), 1)
        self.assertEqual(stored["paths"][0]["created_at"], NOW)
        self.assertEqual(len([e for e in self.events() if e["type"] == "kp_define"]), 3)

    def test_second_path_keeps_its_own_definition_and_activation(self):
        _code, first = self.save(outline("Midterm"), "--activate")
        _code, second = self.save(outline("Finals"))
        self.assertNotEqual(first["path_id"], second["path_id"])
        self.assertEqual(second["kps_defined"], [])
        stored = self.stored()
        self.assertEqual([p["name"] for p in stored["paths"]], ["Midterm", "Finals"])
        self.assertEqual(stored["active_path_id"], first["path_id"])

    def test_evidence_is_resolved_to_display_and_unit(self):
        _code, first = self.save(outline())
        _code, shown = self.cli("show", "--kb", "bio", "--path", first["path_id"])
        evidence = shown["path"]["modules"][0]["knowledge_points"][0]["evidence"][0]
        self.assertEqual(evidence, {"source_id": "lecture", "page": 1,
                                    "display": "lecture.pdf", "unit": "page"})

    def test_save_and_validate_report_affected_modules(self):
        _code, saved = self.save(chain_outline())
        self.assertEqual(saved["affected_modules"], ["m1", "m2", "m3"])

        data = chain_outline()
        data["id"] = saved["path_id"]
        data["modules"][2]["knowledge_points"][0]["objective"] = "Recall gamma precisely."
        code, checked = self.cli("validate", "--kb", "bio", stdin=data)
        self.assertEqual((code, checked["valid"], checked["affected_modules"]), (0, True, ["m3"]))
        _code, edited = self.save(data)
        self.assertEqual(edited["affected_modules"], ["m3"])

        data["modules"][0]["knowledge_points"][0]["objective"] = "Recall alpha precisely."
        _code, upstream = self.save(data)
        self.assertEqual(upstream["affected_modules"], ["m1", "m2", "m3"])


class Rejections(PathCase):
    def test_knowledge_point_names_cannot_contain_the_id_separator(self):
        with self.assertRaises(ll.LedgerError):
            ll.kp_id("bio", "chapter::section")

    def test_unknown_page_is_rejected_with_the_knowledge_point(self):
        data = outline()
        data["modules"][0]["knowledge_points"][0]["evidence"] = [{"source_id": "lecture", "page": 99}]
        self.assert_rejected(data, "'bio::photosynthesis'")

    def test_empty_page_is_not_evidence(self):
        data = outline()
        data["modules"][0]["knowledge_points"][0]["evidence"] = [{"source_id": "lecture", "page": 4}]
        self.assert_rejected(data, "has no text")

    def test_unknown_source_is_rejected(self):
        data = outline()
        data["modules"][0]["knowledge_points"][0]["evidence"] = [{"source_id": "ghost", "page": 1}]
        self.assert_rejected(data, "unknown source 'ghost'")

    def test_missing_evidence_is_rejected(self):
        data = outline()
        data["modules"][0]["knowledge_points"][0]["evidence"] = []
        self.assert_rejected(data, "has no evidence")

    def test_duplicate_and_unknown_ids_are_rejected(self):
        data = outline()
        data["modules"][1]["id"] = "m1"
        code, result = self.save(data)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("duplicate module id 'm1'", result["error"])

        data = outline()
        data["modules"][1]["knowledge_points"][0]["name"] = "Rubisco"
        code, result = self.save(data)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("duplicate knowledge point 'bio::rubisco'", result["error"])

    def test_deleted_knowledge_point_still_used_as_prerequisite_is_rejected(self):
        data = outline()
        data["modules"][0]["knowledge_points"][1]["prerequisites"] = ["bio::deleted point"]
        self.assert_rejected(data, "'bio::deleted point'")

    def test_module_prerequisites_unknown_self_and_cycle(self):
        cases = [
            (["m9"], "unknown prerequisite 'm9'"),
            (["m1"], "lists itself as a prerequisite"),
        ]
        for prereqs, needle in cases:
            with self.subTest(prereqs=prereqs):
                data = outline()
                data["modules"][0]["prerequisites"] = prereqs
                self.assert_rejected(data, needle)
        data = outline()
        data["modules"][0]["prerequisites"] = ["m2"]
        self.assert_rejected(data, "cycle")

    def test_knowledge_point_prerequisites_stay_inside_their_module(self):
        data = outline()
        data["modules"][0]["knowledge_points"][1]["prerequisites"] = ["bio::calvin cycle"]
        self.assert_rejected(data, "cross-module")

    def test_knowledge_point_prerequisite_cycle(self):
        data = outline()
        data["modules"][0]["knowledge_points"][0]["prerequisites"] = ["bio::rubisco"]
        self.assert_rejected(data, "cycle")

    def test_type_conflict_with_existing_definition_requires_a_new_name(self):
        self.save(outline("Midterm"))
        data = {
            "name": "Finals",
            "modules": [{"id": "m1", "title": "Review", "knowledge_points": [
                {"name": "Rubisco", "knowledge_type": "concept",
                 "objective": "Explain Rubisco kinetics.",
                 "evidence": [{"source_id": "lecture", "page": 2}]}]}],
        }
        code, result = self.save(data)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("already defined as 'memory'", result["error"])
        self.assertEqual(len(self.stored()["paths"]), 1)

    def test_path_name_is_unique_ignoring_case_and_space(self):
        self.save(outline("Midterm"))
        code, result = self.save(outline(" midterm "))
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("already used by path", result["error"])

    def test_missing_knowledge_base_is_rejected(self):
        code, result = self.cli("save", "--kb", "missing", stdin=outline())
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("does not exist", result["error"])

    def test_validate_reports_every_error_without_writing(self):
        data = outline()
        data["modules"][0]["knowledge_points"][0]["evidence"] = []
        data["modules"][0]["knowledge_points"][1]["evidence"] = [{"source_id": "lecture", "page": 99}]
        code, result = self.cli("validate", "--kb", "bio", stdin=data)
        self.assertEqual((code, result["ok"]), (0, True))
        self.assertFalse(result["valid"])
        self.assertEqual(len(result["errors"]), 2)
        self.assertFalse(self.store_file().exists())
        self.assertEqual(self.events(), [])


class StoreSafety(PathCase):
    def test_corrupt_store_is_an_error_and_is_never_overwritten(self):
        self.store_file().parent.mkdir(parents=True)
        self.store_file().write_text("{not json", encoding="utf-8")
        before = self.store_file().read_bytes()
        code, result = self.cli("list", "--kb", "bio")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("unreadable", result["error"])
        code, result = self.save(outline())
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertEqual(before, self.store_file().read_bytes())

    def test_dangling_active_pointer_is_an_error(self):
        self.store_file().parent.mkdir(parents=True)
        self.store_file().write_text(json.dumps({"schema": 1, "kb": "bio",
                                                 "active_path_id": "p_missing", "paths": []}),
                                     encoding="utf-8")
        code, result = self.cli("list", "--kb", "bio")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("p_missing", result["error"])

    def test_deactivate_is_the_repair_for_a_dangling_pointer(self):
        self.store_file().parent.mkdir(parents=True)
        self.store_file().write_text(json.dumps({"schema": 1, "kb": "bio",
                                                 "active_path_id": "p_missing", "paths": []}),
                                     encoding="utf-8")
        code, result = self.cli("status", "--kb", "bio")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("deactivate", result["error"])

        code, repaired = self.cli("deactivate", "--kb", "bio")
        self.assertEqual((code, repaired["active_path_id"]), (0, None))
        code, listed = self.cli("list", "--kb", "bio")
        self.assertEqual((code, listed["active_path_id"]), (0, None))

    def test_half_damaged_store_is_rejected_without_writing(self):
        _code, saved = self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        good = self.stored()
        damaged = [
            ("active_path_id", 7),
            ("created_at", "soon"),
        ]
        for key, value in damaged:
            data = json.loads(json.dumps(good))
            if key == "active_path_id":
                data[key] = value
            else:
                data["paths"][0][key] = value
            self.store_file().write_text(json.dumps(data), encoding="utf-8")
            before = self.store_file().read_bytes()
            code, result = self.cli("list", "--kb", "bio")
            self.assertEqual((code, result["ok"]), (1, False), key)
            self.assertIn(key, result["error"])
            self.assertEqual(before, self.store_file().read_bytes())

        data = json.loads(json.dumps(good))
        data["paths"][0]["modules"][0]["knowledge_points"][0]["evidence"] = ["lecture page 1"]
        self.store_file().write_text(json.dumps(data), encoding="utf-8")
        code, result = self.cli("list", "--kb", "bio")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("evidence entry", result["error"])

    def test_unknown_kb_is_rejected_and_creates_no_store(self):
        for command in (["status", "--kb", "ghost"], ["resolve", "--kb", "ghost", "--module", "m1"],
                        ["deactivate", "--kb", "ghost"], ["list", "--kb", "ghost"]):
            code, result = self.cli(*command)
            self.assertEqual((code, result["ok"]), (1, False), command)
            self.assertIn("does not exist", result["error"], command)
        self.assertFalse((self.root / ".tutor" / "path" / "ghost").exists())

    def test_usage_errors_are_json_failures(self):
        code, result = self.cli("status")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("usage error", result["error"])
        code, result = self.cli("status", "--kb", "bio", "--path", "p1", "--all")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("usage error", result["error"])

    def test_invalid_learner_is_rejected(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        code, result = self.cli("status", "--kb", "bio", "--learner", "bad/name")
        self.assertEqual((code, result["ok"]), (1, False))
        code, result = self.cli("resolve", "--kb", "bio", "--module", "m1", "--learner", "bad/name")
        self.assertEqual((code, result["ok"]), (1, False))

    def test_status_and_resolve_are_read_only(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        self.add_attempt("bio::photosynthesis", "correct", assessment_type="qualitative")
        store_before = self.store_file().read_bytes()
        log_before = ll.log_path(str(self.root), "default").read_bytes()
        for argv in (["status", "--kb", "bio"], ["status", "--kb", "bio", "--all"],
                     ["resolve", "--kb", "bio", "--module", "m1"],
                     ["resolve", "--kb", "bio", "--kp", "Rubisco"]):
            code, _result = self.cli(*argv)
            self.assertEqual(code, 0, argv)
        self.assertEqual(store_before, self.store_file().read_bytes())
        self.assertEqual(log_before, ll.log_path(str(self.root), "default").read_bytes())

    def test_a_non_utf8_log_line_is_reported_and_skipped(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        log = ll.log_path(str(self.root), "default")
        with log.open("ab") as fh:
            fh.write(b"\xff\xfe not utf8\n")
        code, data = self.status()
        self.assertEqual((code, data["ok"]), (0, True))
        self.assertTrue(any("not UTF-8" in w for w in data["warnings"]))

    def test_an_unknown_origin_is_read_but_never_opens_a_gate(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        log = ll.log_path(str(self.root), "default")
        for index in range(3):
            raw = {"id": f"future{index}", "v": 1, "ts": self.clock + index, "type": "attempt",
                   "kp": "bio::rubisco", "result": "correct", "origin": "future-origin"}
            ll.append_line(log, json.dumps(raw))
        _code, data = self.status()
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertEqual(kp["mastery"], 1.0)
        self.assertEqual(kp["gate"]["unaided"], 0)
        self.assertEqual(data["modules"][0]["status"], "available")
        self.assertTrue(any("origin" in w for w in data.get("warnings", [])), data.get("warnings"))


class Management(PathCase):
    def test_list_show_activate_deactivate_rename_and_delete(self):
        _code, first = self.cli("save", "--kb", "bio", "--activate", stdin=outline("Midterm"))
        _code, second = self.save(outline("Finals"))
        code, listed = self.cli("list", "--kb", "bio")
        self.assertEqual((code, listed["ok"]), (0, True))
        self.assertEqual(listed["active_path_id"], first["path_id"])
        self.assertEqual([(p["name"], p["module_count"], p["knowledge_point_count"])
                          for p in listed["paths"]], [("Midterm", 2, 3), ("Finals", 2, 3)])

        code, shown = self.cli("show", "--kb", "bio")
        self.assertEqual((code, shown["path"]["id"], shown["active"]), (0, first["path_id"], True))

        code, activated = self.cli("activate", "--kb", "bio", "--path", second["path_id"])
        self.assertEqual((code, activated["active_path_id"]), (0, second["path_id"]))
        code, deactivated = self.cli("deactivate", "--kb", "bio")
        self.assertEqual((code, deactivated["active_path_id"]), (0, None))
        code, shown = self.cli("show", "--kb", "bio")
        self.assertEqual((code, shown["ok"]), (1, False))
        self.assertIn("no active path", shown["error"])

        code, renamed = self.cli("rename", "--kb", "bio", "--path", first["path_id"], "--name", "Midterm 2")
        self.assertEqual((code, renamed), (0, {"kb": "bio", "path_id": first["path_id"],
                                               "name": "Midterm 2", "ok": True}))
        code, deleted = self.cli("delete", "--kb", "bio", "--path", second["path_id"])
        self.assertEqual((code, deleted["deleted"]), (0, second["path_id"]))
        self.assertEqual([p["name"] for p in self.stored()["paths"]], ["Midterm 2"])

    def test_delete_active_path_clears_the_pointer(self):
        _code, first = self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        _code, deleted = self.cli("delete", "--kb", "bio", "--path", first["path_id"])
        self.assertIsNone(deleted["active_path_id"])
        self.assertEqual(self.stored()["paths"], [])

    def test_activate_unknown_path_is_rejected(self):
        self.save(outline())
        code, result = self.cli("activate", "--kb", "bio", "--path", "p_nope")
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertIn("p_nope", result["error"])


class Status(PathCase):
    def test_four_states_next_action_and_module_unlock(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        code, data = self.status()
        self.assertEqual((code, data["ok"], data["learner"]), (0, True, "default"))
        self.assertEqual({m["id"]: m["status"] for m in data["modules"]}, {"m1": "available", "m2": "locked"})
        self.assertEqual(data["modules"][1]["locked_by"], ["m1"])
        self.assertEqual(data["progress"], {"modules": 2, "mastered": 0})
        self.assertEqual(data["next_action"], {"module": "m1", "kp": "bio::photosynthesis",
                                               "action": "learn", "exemption_available": True})
        self.assertFalse(data["path_complete"])

        self.add_attempt("bio::photosynthesis", "correct", assessment_type="qualitative")
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "in_progress")
        self.assertEqual(data["next_action"]["kp"], "bio::rubisco")
        self.assertEqual(data["next_action"]["action"], "learn")

        last = None
        for _ in range(3):
            last = self.add_attempt("bio::rubisco", "correct")
        _code, data = self.status()
        self.assertEqual([m["status"] for m in data["modules"]], ["mastered", "available"])
        self.assertEqual(data["modules"][0]["mastered_at"], last["ts"])
        self.assertEqual(data["modules"][0]["missing"], [])
        self.assertEqual(data["next_action"], {"module": "m2", "kp": "bio::calvin cycle",
                                               "action": "learn", "exemption_available": True})

        last = self.add_attempt("bio::calvin cycle", "correct", assessment_type="qualitative")
        _code, data = self.status()
        self.assertEqual([m["status"] for m in data["modules"]], ["mastered", "mastered"])
        self.assertEqual(data["modules"][1]["mastered_at"], last["ts"])
        self.assertTrue(data["path_complete"])
        self.assertEqual(data["progress"], {"modules": 2, "mastered": 2})
        self.assertIsNone(data["next_action"])

    def test_numeric_gate_needs_three_unaided_correct(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.add_attempt("bio::rubisco", "correct")
        self.add_attempt("bio::rubisco", "correct")
        _code, data = self.status()
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertEqual(data["modules"][0]["status"], "in_progress")
        self.assertEqual(kp["mastery"], 0.8)
        self.assertFalse(kp["pass"])
        self.assertEqual(kp["gate"], {"kind": "numeric", "passed": False, "mastery": 0.8,
                                      "unaided": 2, "recent": ["correct", "correct"],
                                      "needed": {"mastery": 0.9, "unaided": 3}})
        self.assertEqual([g["code"] for g in kp["gaps"]], ["mastery_below", "unaided_below"])

        self.add_attempt("bio::rubisco", "correct")
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "mastered")
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertTrue(kp["pass"])
        self.assertEqual(kp["gaps"], [])

    def test_recent_failure_is_reported_before_a_module_can_complete(self):
        modules = [
            {"id": "m1", "title": "Mixed", "prerequisites": [], "knowledge_points": [
                {"name": "Rubisco", "knowledge_type": "memory", "objective": "Recall Rubisco.",
                 "evidence": [{"source_id": "lecture", "page": 2}]},
                {"name": "Photosynthesis", "knowledge_type": "concept",
                 "objective": "Explain photosynthesis.",
                 "evidence": [{"source_id": "lecture", "page": 1}]},
            ]},
        ]
        self.cli("save", "--kb", "bio", "--activate", stdin=outline(modules=modules))
        self.pass_memory("bio::rubisco")
        self.add_attempt("bio::rubisco", "incorrect")
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "in_progress")
        kp = data["modules"][0]["knowledge_points"][0]
        gaps = {g["code"]: g for g in kp["gaps"]}
        self.assertIn("recent_failure", gaps)
        self.assertEqual(gaps["recent_failure"]["value"], ["correct", "correct", "incorrect"])
        self.assertFalse(kp["pass"])
        self.assertIn("qualitative_missing",
                      [g["code"] for g in data["modules"][0]["knowledge_points"][1]["gaps"]])

    def test_hinted_attempts_do_not_satisfy_the_numeric_gate(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        for _ in range(3):
            self.add_attempt("bio::rubisco", "correct", hints=1)
        _code, data = self.status()
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertEqual(data["modules"][0]["status"], "in_progress")
        self.assertIsNone(kp["mastery"])
        self.assertEqual(kp["gate"]["unaided"], 0)
        self.assertFalse(kp["pass"])
        self.assertEqual([g["code"] for g in kp["gaps"]], ["mastery_below", "unaided_below"])

    def test_diagnostic_attempts_display_mastery_but_never_open_gates(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        for _ in range(2):
            self.add_attempt("bio::rubisco", "correct", origin="diagnostic")
        _code, data = self.status()
        module = data["modules"][0]
        kp = module["knowledge_points"][0]
        self.assertEqual(module["status"], "available")
        self.assertEqual(kp["mastery"], 0.8)
        self.assertEqual(kp["gate"]["unaided"], 0)
        self.assertEqual(kp["unaided_attempts"], 0)
        self.assertIsNone(kp["last_unaided"])
        self.assertFalse(kp["pass"])
        self.assertEqual(data["next_action"]["action"], "learn")

        self.add_attempt("bio::rubisco", "correct", origin="diagnostic")
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "available")
        self.assertEqual(data["modules"][0]["knowledge_points"][0]["mastery"], 1.0)
        self.assertFalse(data["modules"][0]["knowledge_points"][0]["pass"])

    def test_a_re_recorded_question_counts_once(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.add_attempt("bio::rubisco", "correct", question_id="same")
        self.add_attempt("bio::rubisco", "correct", question_id="same")
        kp = self.status()[1]["modules"][0]["knowledge_points"][0]
        self.assertEqual(kp["unaided_attempts"], 1)
        self.assertEqual(kp["gate"]["unaided"], 1)
        self.assertFalse(kp["gate"]["passed"])
        self.assertEqual([g["code"] for g in kp["gaps"]], ["mastery_below", "unaided_below"])

        self.add_attempt("bio::rubisco", "incorrect", question_id="same")
        kp = self.status()[1]["modules"][0]["knowledge_points"][0]
        self.assertEqual(kp["unaided_attempts"], 1)
        self.assertEqual(kp["gate"]["recent"], ["incorrect"])

    def test_concept_gate_needs_a_correct_qualitative_attempt(self):
        self.cli("save", "--kb", "bio", "--activate",
                 stdin=single_outline("Photosynthesis", "concept", page=1))
        for _ in range(5):
            self.add_attempt("bio::photosynthesis", "correct")
        _code, data = self.status()
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertEqual(data["modules"][0]["status"], "in_progress")
        self.assertEqual(kp["gate"]["kind"], "qualitative")
        self.assertIsNone(kp["qualitative"])
        self.assertIn("qualitative_missing", [g["code"] for g in kp["gaps"]])
        self.assertFalse(kp["pass"])

        self.add_attempt("bio::photosynthesis", "partial", assessment_type="qualitative")
        _code, data = self.status()
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertEqual(kp["qualitative"], "partial")
        self.assertIn("qualitative_not_correct", [g["code"] for g in kp["gaps"]])

        self.add_attempt("bio::photosynthesis", "correct", assessment_type="qualitative")
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "mastered")
        self.assertTrue(data["modules"][0]["knowledge_points"][0]["pass"])

    def test_review_attempts_count_toward_gates_but_do_not_unlock_locked_modules(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=chain_outline())
        for _ in range(3):
            self.add_attempt("bio::beta", "correct", origin="review")
        _code, data = self.status()
        self.assertEqual([m["status"] for m in data["modules"]], ["available", "locked", "locked"])
        self.assertTrue(data["modules"][1]["knowledge_points"][0]["pass"])
        self.assertEqual(data["modules"][1]["missing"], [])
        self.assertEqual(data["next_action"]["kp"], "bio::alpha")

        last = None
        for _ in range(3):
            last = self.add_attempt("bio::alpha", "correct")
        _code, data = self.status()
        # Review earned beta's gate while beta was locked; once its prerequisite was
        # learned, beta becomes mastered and the state machine unlocks gamma.
        self.assertEqual([m["status"] for m in data["modules"]], ["mastered", "mastered", "available"])
        self.assertEqual(data["modules"][1]["mastered_at"], last["ts"])

    def test_mastered_module_does_not_regress_after_a_later_failure(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.pass_memory("bio::rubisco")
        _code, before = self.status()
        self.add_attempt("bio::rubisco", "incorrect")
        _code, after = self.status()
        module = after["modules"][0]
        self.assertEqual(module["status"], "mastered")
        self.assertEqual(module["mastered_at"], before["modules"][0]["mastered_at"])
        self.assertFalse(module["knowledge_points"][0]["pass"])
        self.assertIn("recent_failure", [g["code"] for g in module["knowledge_points"][0]["gaps"]])
        self.assertEqual(module["missing"], [])
        self.assertEqual(module["regressed"], ["bio::rubisco"])
        self.assertIsNotNone(module["mastered_at_iso"])
        self.assertTrue(after["path_complete"])

    def test_legacy_attempts_without_origin_still_count(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        for _ in range(3):
            self.add_attempt("bio::rubisco", "correct", origin=None)
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "mastered")

    def test_status_without_active_path_returns_nulls_and_keeps_legacy_behavior(self):
        self.save(outline())
        code, data = self.status()
        self.assertEqual((code, data["ok"]), (0, True))
        self.assertIsNone(data["active_path_id"])
        self.assertIsNone(data["path"])
        self.assertEqual(data["modules"], [])
        self.assertIsNone(data["next_action"])
        self.assertFalse(data["path_complete"])
        code, resolved = self.resolve("--module", "m1")
        self.assertEqual((code, resolved["allowed"], resolved["reason"]), (0, True, "no_active_path"))

    def test_progress_is_isolated_per_learner(self):
        code, saved = self.cli("--learner", "other", "save", "--kb", "bio", "--activate",
                               stdin=single_outline())
        self.assertEqual((code, saved["ok"]), (0, True))
        log = ll.log_path(str(self.root), "other")
        for index in range(3):
            event = ll.prepare_event({"type": "attempt", "kp": "bio::rubisco", "result": "correct",
                                      "question_id": f"o{index}", "origin": "learn"},
                                     ll.read_events(log)[0], self.clock)
            self.clock += 10
            ll.append_line(log, json.dumps(event))
        _code, mine = self.status()
        _code, theirs = self.cli("status", "--kb", "bio", "--learner", "other")
        self.assertEqual((mine["learner"], mine["modules"][0]["knowledge_points"][0]["pass"]),
                         ("default", False))
        self.assertEqual((theirs["learner"], theirs["modules"][0]["knowledge_points"][0]["pass"]),
                         ("other", True))
        self.assertEqual(theirs["modules"][0]["status"], "mastered")

    def test_a_hinted_retry_does_not_erase_unaided_evidence(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.add_attempt("bio::rubisco", "correct", question_id="q1")
        self.add_attempt("bio::rubisco", "correct", question_id="q2")
        self.add_attempt("bio::rubisco", "incorrect", hints=1, question_id="q1")
        kp = self.status()[1]["modules"][0]["knowledge_points"][0]
        self.assertEqual(kp["unaided_attempts"], 2)
        self.assertEqual(kp["gate"]["unaided"], 2)
        self.assertEqual(kp["gate"]["recent"], ["correct", "correct"])
        self.assertEqual(kp["gate"]["mastery"], 0.8)

    def test_a_hinted_feynman_retelling_cannot_pass_a_concept_gate(self):
        self.cli("save", "--kb", "bio", "--activate",
                 stdin=single_outline("Photosynthesis", "concept", page=1))
        self.add_attempt("bio::photosynthesis", "correct", assessment_type="qualitative", hints=1)
        kp = self.status()[1]["modules"][0]["knowledge_points"][0]
        self.assertIsNone(kp["qualitative"])
        self.assertIn("qualitative_missing", [g["code"] for g in kp["gaps"]])
        self.assertFalse(kp["pass"])

    def test_a_qualitative_failure_blocks_a_numeric_gate(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.add_attempt("bio::rubisco", "incorrect", assessment_type="qualitative")
        self.pass_memory("bio::rubisco")
        _code, data = self.status()
        kp = data["modules"][0]["knowledge_points"][0]
        self.assertEqual(data["modules"][0]["status"], "in_progress")
        self.assertFalse(kp["pass"])
        gaps = {g["code"]: g for g in kp["gaps"]}
        self.assertEqual(gaps["qualitative_failure"]["value"], ["incorrect"])
        self.assertNotIn("recent_failure", gaps)

    def test_attempts_without_a_question_id_do_not_open_a_gate(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        log = ll.log_path(str(self.root), "default")
        for _ in range(3):
            self.clock += 10
            event = ll.prepare_event({"type": "attempt", "kp": "bio::rubisco", "result": "correct",
                                      "origin": "learn"}, self.events(), self.clock)
            ll.append_line(log, json.dumps(event))
        _code, data = self.status()
        module = data["modules"][0]
        kp = module["knowledge_points"][0]
        self.assertEqual(module["status"], "in_progress")
        self.assertEqual(kp["mastery"], 1.0)
        self.assertEqual(kp["unaided_attempts"], 0)
        self.assertEqual(kp["gate"]["unaided"], 0)
        self.assertFalse(kp["pass"])

    def test_a_failed_exemption_leaves_the_module_available(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        for _ in range(2):
            self.add_attempt("bio::rubisco", "incorrect", origin="exemption")
        _code, data = self.status()
        self.assertEqual(data["modules"][0]["status"], "available")
        self.assertFalse(data["modules"][0]["knowledge_points"][0]["pass"])
        self.assertTrue(data["next_action"]["exemption_available"])
        self.assertNotIn("qualitative", [g["code"] for g in data["modules"][0]["knowledge_points"][0]["gaps"]])

    def test_status_mastery_matches_the_ledger(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.add_attempt("bio::rubisco", "correct", question_id="q1")
        self.add_attempt("bio::rubisco", "correct", hints=1, question_id="q2")
        self.add_attempt("bio::rubisco", "incorrect", origin="diagnostic", question_id="q3")
        attempts = [e for e in self.events()
                    if e.get("type") == "attempt" and e.get("kp") == "bio::rubisco"]
        expected = round(compute_mastery(ll.mastery_scores(attempts)), 4)
        kp = self.status()[1]["modules"][0]["knowledge_points"][0]
        self.assertEqual(kp["mastery"], expected)

    def test_status_all_reports_every_path(self):
        _code, first = self.cli("save", "--kb", "bio", "--activate", stdin=outline("Midterm"))
        _code, second = self.save(outline("Finals"))
        code, data = self.cli("status", "--kb", "bio", "--all")
        self.assertEqual((code, data["active_path_id"]), (0, first["path_id"]))
        self.assertEqual([(p["path"]["name"], p["active"]) for p in data["paths"]],
                         [("Midterm", True), ("Finals", False)])
        for item in data["paths"]:
            self.assertEqual([m["id"] for m in item["modules"]], ["m1", "m2"])
            self.assertEqual(len(item["progress"]), 2)
            self.assertEqual(second["path_id"], data["paths"][1]["path"]["id"])

    def test_status_is_deterministic_and_replays_in_file_order(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=single_outline())
        self.add_attempt("bio::rubisco", "correct", ts=NOW + 300)
        self.add_attempt("bio::rubisco", "correct", ts=NOW + 100)
        self.add_attempt("bio::rubisco", "correct", ts=NOW + 200)
        _code, first = self.status()
        _code, second = self.status()
        self.assertEqual(first, second)
        self.assertEqual(first["modules"][0]["mastered_at"], NOW + 200)


class Invalidation(PathCase):
    def test_invalid_evidence_propagates_downstream_and_recovers(self):
        _code, saved = self.cli("save", "--kb", "bio", "--activate", stdin=chain_outline())
        for kp in ("bio::alpha", "bio::beta", "bio::gamma"):
            self.pass_memory(kp)
        _code, data = self.status()
        self.assertTrue(data["path_complete"])
        events_before = len(self.events())

        self.set_page_text(1, "")
        _code, data = self.status()
        self.assertEqual(data["progress"], {"modules": 3, "mastered": 0})
        self.assertEqual([m["status"] for m in data["modules"]], ["in_progress", "locked", "locked"])
        self.assertEqual(data["modules"][0]["locked_by"], [])
        self.assertEqual(data["modules"][1]["locked_by"], ["m1"])
        expected = [{"module": "m1", "kp": "bio::alpha"}]
        self.assertEqual(data["modules"][0]["invalidated_by"], expected)
        self.assertEqual(data["modules"][1]["invalidated_by"], expected)
        self.assertEqual(data["modules"][2]["invalidated_by"], expected)
        self.assertEqual(data["invalidated_by"], expected)
        self.assertIn("evidence_invalid",
                      [g["code"] for g in data["modules"][0]["knowledge_points"][0]["gaps"]])
        self.assertFalse(data["modules"][0]["knowledge_points"][0]["pass"])
        self.assertIsNone(data["next_action"])
        self.assertTrue(data["blocked_by_invalid_evidence"])
        self.assertFalse(data["path_complete"])

        self.set_page_text(1, "Photosynthesis converts light energy into chemical energy.")
        _code, data = self.status()
        self.assertTrue(data["path_complete"])
        self.assertEqual(data["invalidated_by"], [])
        self.assertEqual(len(self.events()), events_before)

    def test_editing_a_mastered_module_recomputes_and_relocks_downstream(self):
        _code, saved = self.cli("save", "--kb", "bio", "--activate", stdin=chain_outline())
        for kp in ("bio::alpha", "bio::beta", "bio::gamma"):
            self.pass_memory(kp)
        _code, data = self.status()
        self.assertTrue(data["path_complete"])

        edited = chain_outline()
        edited["id"] = saved["path_id"]
        edited["modules"][0]["knowledge_points"].append(
            {"name": "Delta", "knowledge_type": "memory", "objective": "Recall delta.",
             "evidence": [{"source_id": "lecture", "page": 2}]})
        code, resaved = self.save(edited)
        self.assertEqual((code, resaved["created"], resaved["path_id"]), (0, False, saved["path_id"]))
        _code, data = self.status()
        self.assertEqual([m["status"] for m in data["modules"]], ["in_progress", "locked", "locked"])
        self.assertEqual(data["next_action"]["kp"], "bio::delta")
        self.assertEqual(data["next_action"]["action"], "learn")

        self.pass_memory("bio::delta")
        _code, data = self.status()
        self.assertEqual([m["status"] for m in data["modules"]],
                         ["mastered", "mastered", "mastered"])
        self.assertTrue(data["path_complete"])


class Resolve(PathCase):
    def test_resolve_locked_mastered_and_out_of_path(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=outline())
        code, locked = self.resolve("--module", "m2")
        self.assertEqual((code, locked["allowed"], locked["reason"]), (0, False, "locked"))
        self.assertEqual(locked["locked_by"], ["m1"])
        self.assertEqual(locked["next_action"]["kp"], "bio::photosynthesis")

        code, allowed = self.resolve("--kp", "Rubisco")
        self.assertEqual((code, allowed["allowed"], allowed["reason"]), (0, True, "available"))
        self.assertEqual((allowed["module"], allowed["kp"]), ("m1", "bio::rubisco"))
        code, blocked = self.resolve("--kp", "Calvin cycle")
        self.assertEqual((code, blocked["allowed"], blocked["reason"]), (0, False, "locked"))

        code, outside = self.resolve("--kp", "Astronomy")
        self.assertEqual((code, outside["allowed"], outside["reason"]), (0, False, "out_of_path"))
        code, unknown = self.resolve("--module", "m9")
        self.assertEqual((code, unknown["allowed"], unknown["reason"]), (0, False, "unknown_module"))

        self.add_attempt("bio::photosynthesis", "correct", assessment_type="qualitative")
        self.pass_memory("bio::rubisco")
        code, done = self.resolve("--module", "m1")
        self.assertEqual((code, done["allowed"], done["reason"]), (0, False, "mastered"))
        self.assertEqual(done["hint"], "review remains available")
        code, next_module = self.resolve("--module", "m2")
        self.assertEqual((code, next_module["allowed"], next_module["reason"]), (0, True, "available"))

    def test_resolve_rejects_invalid_evidence(self):
        self.cli("save", "--kb", "bio", "--activate", stdin=chain_outline())
        for kp in ("bio::alpha", "bio::beta", "bio::gamma"):
            self.pass_memory(kp)
        self.set_page_text(1, "")
        code, module = self.resolve("--module", "m1")
        self.assertEqual((code, module["allowed"], module["reason"]), (0, False, "evidence_invalid"))
        self.assertEqual(module["invalidated_by"], [{"module": "m1", "kp": "bio::alpha"}])
        code, kp = self.resolve("--kp", "Alpha")
        self.assertEqual((code, kp["allowed"], kp["reason"]), (0, False, "evidence_invalid"))
        self.assertEqual(kp["kp"], "bio::alpha")

    def test_resolve_rejects_an_already_passed_point_inside_an_open_module(self):
        modules = [
            {"id": "m1", "title": "Mixed", "prerequisites": [], "knowledge_points": [
                {"name": "Photosynthesis", "knowledge_type": "concept",
                 "objective": "Explain photosynthesis.", "evidence": [{"source_id": "lecture", "page": 1}]},
                {"name": "Rubisco", "knowledge_type": "memory",
                 "objective": "Recall Rubisco.", "evidence": [{"source_id": "lecture", "page": 2}]},
            ]},
        ]
        self.cli("save", "--kb", "bio", "--activate", stdin=outline(modules=modules))
        self.add_attempt("bio::photosynthesis", "correct", assessment_type="qualitative")
        code, result = self.resolve("--kp", "Photosynthesis")
        self.assertEqual((code, result["allowed"], result["reason"]), (0, False, "already_passed"))
        self.assertEqual(result["module_status"], "in_progress")
        code, other = self.resolve("--kp", "Rubisco")
        self.assertEqual((code, other["allowed"]), (0, True))


if __name__ == "__main__":
    unittest.main()
