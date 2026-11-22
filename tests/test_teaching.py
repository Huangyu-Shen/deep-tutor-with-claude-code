"""Teaching profile storage and CLI contract (scripts/teaching.py)."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ledger  # noqa: E402
import teaching  # noqa: E402


class TeachingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.kb = "bio"
        self.kp = "bio::calvin cycle"

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------- helpers

    def run_cli(self, *argv, payload=None, learner=None):
        out = io.StringIO()
        stdin = io.StringIO(json.dumps(payload) if payload is not None else "")
        base = ["--root", self.root]
        if learner:
            base += ["--learner", learner]
        with contextlib.redirect_stdout(out):
            code = teaching.main([*base, *argv], stdin=stdin)
        return code, json.loads(out.getvalue())

    def define_kp(self, name="Calvin cycle", kb=None, learner="default"):
        out = io.StringIO()
        event = {"type": "kp_define", "kb": kb or self.kb, "name": name,
                 "knowledge_type": "concept", "sources": []}
        with contextlib.redirect_stdout(out):
            code = ledger.main(["--root", self.root, "--learner", learner, "record"],
                               stdin=io.StringIO(json.dumps(event)))
        self.assertEqual(code, 0, out.getvalue())

    def pref(self, key="examples", text="多用例子讲", evidence="多用例子讲"):
        return {"kind": "preference", "op": "upsert", "key": key,
                "text": text, "evidence": evidence}

    def obs(self, key="calvin-analogy", kp=None, strategy="类比", outcome="not_helped",
            evidence="类比我没懂"):
        return {"kind": "observation", "op": "upsert", "key": key, "kp": kp or self.kp,
                "strategy": strategy, "outcome": outcome, "evidence": evidence}

    def remove(self, kind, key, evidence):
        return {"kind": kind, "op": "remove", "key": key, "evidence": evidence}

    def update(self, message, *updates, learner=None, now=None):
        argv = ["update", "--kb", self.kb]
        if now is not None:
            argv = ["--now", str(now), *argv]
        return self.run_cli(*argv, payload={"learner_message": message,
                                            "updates": list(updates)}, learner=learner)

    def store_path(self, learner="default", kb=None):
        return Path(self.root) / ".tutor" / "teaching" / (kb or self.kb) / f"{learner}.json"

    def read_store(self, learner="default"):
        return json.loads(self.store_path(learner).read_text(encoding="utf-8"))

    def write_store(self, data, learner="default"):
        path = self.store_path(learner)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def store(self, **overrides):
        data = {"schema_version": 1, "kb": self.kb, "learner": "default",
                "preferences": {}, "observations": {}}
        data.update(overrides)
        return data

    def stored_obs(self, kp, updated_at, strategy="类比", outcome="not_helped"):
        return {"kp": kp, "strategy": strategy, "outcome": outcome,
                "evidence": "原话", "updated_at": updated_at}

    # ------------------------------------------------------ reading (U1)

    def test_missing_store_reads_as_empty_and_creates_nothing(self):
        for argv in (["show", "--kb", self.kb], ["show", "--kb", self.kb, "--kp", self.kp],
                     ["list", "--kb", self.kb]):
            code, out = self.run_cli(*argv)
            self.assertEqual(code, 0, out)
            self.assertTrue(out["ok"])
        code, out = self.run_cli("list", "--kb", self.kb)
        self.assertEqual(out["preferences"], {})
        self.assertEqual(out["observations"], {})
        self.assertFalse((Path(self.root) / ".tutor").exists())

    def test_show_returns_preferences_and_latest_three_observations_for_kp(self):
        other = "bio::light reactions"
        self.write_store(self.store(
            preferences={"a": {"text": "t1", "evidence": "e1", "updated_at": 1},
                         "b": {"text": "t2", "evidence": "e2", "updated_at": 2}},
            observations={"o1": self.stored_obs(self.kp, 10), "o2": self.stored_obs(self.kp, 40),
                          "o3": self.stored_obs(self.kp, 20), "o4": self.stored_obs(self.kp, 30),
                          "x": self.stored_obs(other, 50)}))
        code, out = self.run_cli("show", "--kb", self.kb, "--kp", self.kp)
        self.assertEqual(code, 0, out)
        self.assertEqual(set(out["teaching_preferences"]), {"a", "b"})
        self.assertEqual([o["key"] for o in out["teaching_observations"]], ["o2", "o4", "o3"])
        self.assertTrue(all(o["kp"] == self.kp for o in out["teaching_observations"]))

    def test_show_without_kp_returns_only_preferences(self):
        self.write_store(self.store(
            preferences={"a": {"text": "t1", "evidence": "e1", "updated_at": 1}},
            observations={"o1": self.stored_obs(self.kp, 10)}))
        code, out = self.run_cli("show", "--kb", self.kb)
        self.assertEqual(code, 0, out)
        self.assertEqual(set(out["teaching_preferences"]), {"a"})
        self.assertEqual(out["teaching_observations"], [])

    def test_equal_timestamps_sort_by_key(self):
        self.write_store(self.store(observations={
            "zeta": self.stored_obs(self.kp, 5), "alpha": self.stored_obs(self.kp, 5)}))
        _, out = self.run_cli("show", "--kb", self.kb, "--kp", self.kp)
        self.assertEqual([o["key"] for o in out["teaching_observations"]], ["alpha", "zeta"])

    def test_list_returns_every_entry_with_its_key(self):
        self.write_store(self.store(
            preferences={"a": {"text": "t1", "evidence": "e1", "updated_at": 1}},
            observations={"o1": self.stored_obs(self.kp, 10)}))
        code, out = self.run_cli("list", "--kb", self.kb)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["preferences"]["a"]["evidence"], "e1")
        self.assertEqual(out["observations"]["o1"]["strategy"], "类比")
        self.assertEqual(out["preference_count"], 1)
        self.assertEqual(out["observation_count"], 1)

    def test_damaged_stores_are_errors_not_empty(self):
        good_pref = {"text": "t", "evidence": "e", "updated_at": 1}
        cases = {
            "invalid json": "{not json",
            "not an object": "[]",
            "bool version": self.store(schema_version=True),
            "float version": self.store(schema_version=1.0),
            "future version": self.store(schema_version=2),
            "wrong kb": self.store(kb="chem"),
            "wrong learner": self.store(learner="someone"),
            "preferences not object": self.store(preferences=[]),
            "preference missing text": self.store(preferences={"a": {"evidence": "e", "updated_at": 1}}),
            "preference bad key": self.store(preferences={"bad key": good_pref}),
            "observation bad outcome": self.store(observations={
                "o": dict(self.stored_obs(self.kp, 1), outcome="unknown")}),
            "observation missing strategy": self.store(observations={
                "o": {"kp": self.kp, "outcome": "helped", "evidence": "e", "updated_at": 1}}),
            "bad timestamp": self.store(preferences={"a": dict(good_pref, updated_at="now")}),
            "preference empty evidence": self.store(preferences={"a": dict(good_pref, evidence="")}),
            "observation bad key": self.store(observations={
                "bad key": self.stored_obs(self.kp, 1)}),
            "observation empty kp": self.store(observations={
                "o": dict(self.stored_obs(self.kp, 1), kp="")}),
            "observation empty evidence": self.store(observations={
                "o": dict(self.stored_obs(self.kp, 1), evidence="")}),
        }
        for label, data in cases.items():
            with self.subTest(label):
                path = self.store_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data if isinstance(data, str) else json.dumps(data),
                                encoding="utf-8")
                for argv in (["show", "--kb", self.kb], ["list", "--kb", self.kb]):
                    code, out = self.run_cli(*argv)
                    self.assertEqual(code, 1, out)
                    self.assertFalse(out["ok"])

    def test_stores_are_isolated_by_kb_and_learner(self):
        self.define_kp()
        code, _ = self.update("多用例子讲", self.pref())
        self.assertEqual(code, 0)
        _, other_learner = self.run_cli("list", "--kb", self.kb, learner="alice")
        self.assertEqual(other_learner["preferences"], {})
        _, other_kb = self.run_cli("list", "--kb", "chem")
        self.assertEqual(other_kb["preferences"], {})
        _, same = self.run_cli("list", "--kb", self.kb)
        self.assertEqual(set(same["preferences"]), {"examples"})

    def test_bad_names_are_rejected(self):
        for kb in (".hidden", "a/b", "", ".."):
            code, out = self.run_cli("list", "--kb", kb)
            self.assertEqual(code, 1, (kb, out))
        for learner in (".hidden", "a/b", "../x"):
            code, out = self.run_cli("list", "--kb", self.kb, learner=learner)
            self.assertEqual(code, 1, (learner, out))

    def test_show_rejects_a_kp_that_is_not_canonical_or_not_in_this_kb(self):
        for kp in ("bio::Calvin cycle", "chem::calvin cycle", "calvin cycle"):
            with self.subTest(kp):
                code, out = self.run_cli("show", "--kb", self.kb, "--kp", kp)
                self.assertEqual(code, 1, out)
                self.assertFalse(out["ok"])

    def test_usage_error_is_json_with_exit_1(self):
        code, out = self.run_cli("show")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])

    # ---------------------------------------------------- recording (U2)

    def test_records_a_preference_with_quote(self):
        code, out = self.update("能不能多用例子讲", self.pref(), now=100)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["recorded"], [{"kind": "preference", "key": "examples"}])
        entry = self.read_store()["preferences"]["examples"]
        self.assertEqual(entry["evidence"], "多用例子讲")
        self.assertEqual(entry["updated_at"], 100)

    def test_records_an_observation_on_a_registered_kp(self):
        self.define_kp()
        code, out = self.update("你这个类比我没懂", self.obs(), now=100)
        self.assertEqual(code, 0, out)
        _, shown = self.run_cli("show", "--kb", self.kb, "--kp", self.kp)
        self.assertEqual(shown["teaching_observations"][0]["strategy"], "类比")
        self.assertEqual(shown["teaching_observations"][0]["outcome"], "not_helped")

    def test_missing_or_empty_evidence_rejects_the_batch(self):
        for evidence in (None, ""):
            item = self.pref()
            if evidence is None:
                del item["evidence"]
            else:
                item["evidence"] = evidence
            code, out = self.update("多用例子讲", item)
            self.assertEqual(code, 1, out)
        remove = self.remove("preference", "examples", "")
        self.define_kp()
        self.update("多用例子讲", self.pref())
        code, out = self.update("忘掉", remove)
        self.assertEqual(code, 1, out)

    def test_empty_or_missing_updates_are_rejected(self):
        code, out = self.run_cli("update", "--kb", self.kb,
                                 payload={"learner_message": "hi", "updates": []})
        self.assertEqual(code, 1, out)
        code, out = self.run_cli("update", "--kb", self.kb, payload={"learner_message": "hi"})
        self.assertEqual(code, 1, out)
        self.assertFalse(self.store_path().exists())

    def test_evidence_must_be_a_verbatim_substring(self):
        cases = ["多用 例子讲", "多用例子,讲", "请多举例"]
        for evidence in cases:
            with self.subTest(evidence):
                code, out = self.update("能不能多用例子讲", self.pref(evidence=evidence))
                self.assertEqual(code, 1, out)
        self.assertFalse(self.store_path().exists())

    def test_one_bad_item_rejects_the_whole_batch(self):
        self.define_kp()
        code, out = self.update("多用例子讲,类比我没懂", self.pref(),
                                self.obs(evidence="不在消息里"))
        self.assertEqual(code, 1, out)
        self.assertFalse(self.store_path().exists())

    def test_observation_needs_a_registered_kp(self):
        code, out = self.update("类比我没懂", self.obs())
        self.assertEqual(code, 1, out)
        self.assertIn("register", out["error"].lower())
        self.assertFalse(self.store_path().exists())
        self.define_kp()
        code, out = self.update("类比我没懂", self.obs())
        self.assertEqual(code, 0, out)

    def test_observation_kp_must_be_canonically_spelled_and_in_this_kb(self):
        self.define_kp()
        for kp in ("bio::Calvin cycle", "bio::calvin  cycle", "chem::calvin cycle",
                   "calvin cycle"):
            with self.subTest(kp):
                code, out = self.update("类比我没懂", self.obs(kp=kp))
                self.assertEqual(code, 1, out)

    def test_a_kp_registered_in_another_kb_is_refused_by_the_prefix_check(self):
        self.define_kp(name="Calvin cycle", kb="chem")
        code, out = self.update("类比我没懂", self.obs(kp="chem::calvin cycle"))
        self.assertEqual(code, 1, out)
        self.assertIn("does not belong", out["error"])

    def test_field_rules(self):
        self.define_kp()
        bad_items = [
            dict(self.obs(), strategy=""),
            {k: v for k, v in self.obs().items() if k != "strategy"},
            dict(self.obs(), outcome="unknown"),
            dict(self.obs(), text="free text"),
            dict(self.pref(), kp=self.kp),
            dict(self.pref(), key="bad key"),
            dict(self.pref(), key="x" * 65),
            dict(self.pref(), text="x" * 401, evidence="多用例子讲"),
            dict(self.obs(), strategy="x" * 301),
            dict(self.pref(evidence="e" * 301)),
            dict(self.pref(), kind="note"),
            dict(self.pref(), op="merge"),
            dict(self.remove("preference", "examples", "e"), text="no"),
        ]
        for item in bad_items:
            with self.subTest(item):
                code, out = self.update("多用例子讲 类比我没懂 e", item)
                self.assertEqual(code, 1, out)
        self.assertFalse(self.store_path().exists())

    def test_preference_cap_is_six_and_updating_an_existing_key_is_allowed(self):
        message = " ".join(f"要求{i}" for i in range(8))
        for i in range(6):
            code, out = self.update(message, self.pref(key=f"k{i}", text=f"要求{i}",
                                                       evidence=f"要求{i}"))
            self.assertEqual(code, 0, out)
        code, out = self.update(message, self.pref(key="k6", text="要求6", evidence="要求6"))
        self.assertEqual(code, 1, out)
        self.assertIn("remove", out["error"].lower())
        self.assertEqual(len(self.read_store()["preferences"]), 6)
        code, out = self.update(message, self.pref(key="k0", text="要求7", evidence="要求7"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_store()["preferences"]["k0"]["text"], "要求7")

    def test_observation_cap_is_twelve(self):
        self.define_kp()
        message = " ".join(f"没懂{i}" for i in range(14))
        for i in range(12):
            code, out = self.update(message, self.obs(key=f"o{i}", evidence=f"没懂{i}"))
            self.assertEqual(code, 0, out)
        code, out = self.update(message, self.obs(key="o12", evidence="没懂12"))
        self.assertEqual(code, 1, out)
        self.assertEqual(len(self.read_store()["observations"]), 12)

    def test_cap_check_counts_the_whole_batch(self):
        message = " ".join(f"要求{i}" for i in range(8))
        batch = [self.pref(key=f"k{i}", text=f"要求{i}", evidence=f"要求{i}") for i in range(7)]
        code, out = self.update(message, *batch)
        self.assertEqual(code, 1, out)
        self.assertFalse(self.store_path().exists())

    def test_same_key_twice_in_one_batch_is_rejected_but_kinds_may_share_a_key(self):
        self.define_kp()
        message = "多用例子讲 类比我没懂 要求二"
        code, out = self.update(message, self.pref(key="same"), self.pref(key="same", text="x",
                                                                         evidence="要求二"))
        self.assertEqual(code, 1, out)
        code, out = self.update(message, self.pref(key="same"), self.obs(key="same"))
        self.assertEqual(code, 0, out)

    def test_identical_upsert_is_a_no_op_and_keeps_the_file_bytes(self):
        self.define_kp()
        self.update("多用例子讲", self.pref(), now=100)
        before = self.store_path().read_bytes()
        code, out = self.update("多用例子讲", self.pref(), now=999)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["unchanged"], [{"kind": "preference", "key": "examples"}])
        self.assertEqual(out["recorded"], [])
        self.assertEqual(self.store_path().read_bytes(), before)

    def test_changed_content_refreshes_updated_at(self):
        self.update("多用例子讲 简短些", self.pref(), now=100)
        code, out = self.update("多用例子讲 简短些",
                                self.pref(text="简短些", evidence="简短些"), now=200)
        self.assertEqual(code, 0, out)
        entry = self.read_store()["preferences"]["examples"]
        self.assertEqual((entry["text"], entry["updated_at"]), ("简短些", 200))

    def test_an_observation_key_is_bound_to_its_first_kp(self):
        self.define_kp()
        self.define_kp(name="Light reactions")
        self.update("类比我没懂", self.obs(key="k1"), now=1)
        before = self.store_path().read_bytes()
        code, out = self.update("类比我没懂", self.obs(key="k1", kp="bio::light reactions"))
        self.assertEqual(code, 1, out)
        self.assertIn("bio::calvin cycle", out["error"])
        self.assertIn("bio::light reactions", out["error"])
        self.assertEqual(self.store_path().read_bytes(), before)
        code, out = self.update("类比我没懂", self.obs(key="k1", outcome="helped"), now=2)
        self.assertEqual(code, 0, out)

    def test_remove_needs_a_quote_and_an_existing_key(self):
        self.update("多用例子讲", self.pref())
        code, out = self.update("忘掉多用例子那条", self.remove("preference", "examples", "不在消息里"))
        self.assertEqual(code, 1, out)
        code, out = self.update("忘掉多用例子那条", self.remove("preference", "nope", "忘掉多用例子那条"))
        self.assertEqual(code, 1, out)
        self.assertIn("examples", self.read_store()["preferences"])
        code, out = self.update("忘掉多用例子那条", self.remove("preference", "examples", "忘掉多用例子那条"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["removed"], [{"kind": "preference", "key": "examples"}])
        _, shown = self.run_cli("show", "--kb", self.kb)
        self.assertEqual(shown["teaching_preferences"], {})

    def test_at_the_cap_one_batch_can_remove_an_entry_and_add_another(self):
        message = " ".join(f"要求{i}" for i in range(8))
        for i in range(6):
            self.update(message, self.pref(key=f"k{i}", text=f"要求{i}", evidence=f"要求{i}"))
        batch = [self.remove("preference", "k0", "要求0"),
                 self.pref(key="k6", text="要求6", evidence="要求6")]
        code, out = self.update(message, *batch)
        self.assertEqual(code, 0, out)
        keys = set(self.read_store()["preferences"])
        self.assertEqual(keys, {"k1", "k2", "k3", "k4", "k5", "k6"})

    def test_an_observation_can_be_removed(self):
        self.define_kp()
        self.update("类比我没懂", self.obs())
        code, out = self.update("忘掉类比那条", self.remove("observation", "calvin-analogy", "忘掉类比那条"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["removed"], [{"kind": "observation", "key": "calvin-analogy"}])
        self.assertEqual(self.read_store()["observations"], {})

    def test_whitespace_around_evidence_is_trimmed_before_the_quote_check(self):
        code, out = self.update("能不能多用例子讲", self.pref(evidence="  多用例子讲  "))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_store()["preferences"]["examples"]["evidence"], "多用例子讲")

    def test_resubmitting_the_same_batch_is_idempotent(self):
        self.define_kp()
        message = "多用例子讲,类比我没懂"
        self.update(message, self.pref(), self.obs(), now=1)
        before = self.store_path().read_bytes()
        code, out = self.update(message, self.pref(), self.obs(), now=2)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out["unchanged"]), 2)
        self.assertEqual(self.store_path().read_bytes(), before)

    def test_failed_update_leaves_no_temp_files(self):
        self.update("多用例子讲", self.pref())
        self.update("不在", self.pref(key="other", evidence="不存在"))
        leftovers = [p.name for p in self.store_path().parent.iterdir()]
        self.assertEqual(leftovers, ["default.json"])

    def test_recording_never_touches_the_ledger(self):
        self.define_kp()
        ledger_file = Path(self.root) / ".tutor" / "learner" / "default" / "events.jsonl"
        before = ledger_file.read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ledger.main(["--root", self.root, "state"])
        state_before = out.getvalue()
        self.update("多用例子讲,类比我没懂", self.pref(), self.obs())
        self.assertEqual(ledger_file.read_bytes(), before)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ledger.main(["--root", self.root, "state"])
        self.assertEqual(out.getvalue(), state_before)

    def test_damaged_store_blocks_update_instead_of_being_overwritten(self):
        path = self.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        code, out = self.update("多用例子讲", self.pref())
        self.assertEqual(code, 1, out)
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
