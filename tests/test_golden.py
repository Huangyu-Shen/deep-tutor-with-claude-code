"""Ported scheduler/mastery must reproduce vectors made by DeepTutor's original code."""

from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mastery  # noqa: E402
import scheduler as sch  # noqa: E402

VECTORS = json.loads((Path(__file__).parent / "golden" / "vectors.json").read_text())
TOL = 1e-9


def evidence(event: dict, t0: float) -> sch.LearningEvidence:
    spec = {k: v for k, v in event.items() if k not in {"offset_s", "kp"}}
    spec.setdefault("source", "deep_question")
    return sch.LearningEvidence(
        knowledge_point_id=event["kp"],
        evidence_id=f"e{int(event['offset_s'])}",
        timestamp=t0 + event["offset_s"],
        **spec,
    )


def assert_close(test: unittest.TestCase, actual, expected, path: str) -> None:
    if isinstance(expected, dict):
        test.assertEqual(set(actual), set(expected), f"{path}: keys differ")
        for key in expected:
            assert_close(test, actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        test.assertEqual(len(actual), len(expected), f"{path}: length differs")
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_close(test, a, e, f"{path}[{i}]")
    elif isinstance(expected, float) and actual is not None:
        scale = max(1.0, abs(expected))
        test.assertLessEqual(abs(actual - expected), TOL * scale, f"{path}: {actual} != {expected}")
    else:
        test.assertEqual(actual, expected, path)


class GoldenTests(unittest.TestCase):
    def test_provenance_recorded(self):
        meta = VECTORS["meta"]
        self.assertTrue(meta["deeptutor_version"])
        self.assertEqual(len(meta["source_files"]), 3)

    def test_mastery(self):
        for case in VECTORS["mastery"]:
            with self.subTest(case["correctness"]):
                self.assertAlmostEqual(mastery.compute_mastery(case["correctness"]), case["mastery"], delta=TOL)

    def test_replay_steps_final_and_probes(self):
        t0 = VECTORS["meta"]["t0"]
        for case in VECTORS["replay"]:
            with self.subTest(case["id"]):
                s = sch.SpacedRepetitionScheduler()
                events = [evidence(e, t0) for e in case["events"]]
                ktype = case["knowledge_type"]
                if events:
                    state = s.get_initial_state(
                        ktype, now=events[0].timestamp, desired_retention=case["desired_retention"]
                    )
                    for i, e in enumerate(events):
                        s.schedule_review(state, ktype, e, now=e.timestamp)
                        assert_close(self, asdict(state), case["steps"][i], f"step[{i}]")
                final = s.replay(ktype, events, now=case["initial_now"], desired_retention=case["desired_retention"])
                assert_close(self, asdict(final), case["final_state"], "final")
                for p in case["probes"]:
                    errs = set(p["error_kps"])
                    self.assertAlmostEqual(s.retrievability(final, now=p["now"]), p["retrievability"], delta=TOL)
                    self.assertAlmostEqual(s.forgetting_risk(final, errs, "kp", now=p["now"]), p["forgetting_risk"], delta=TOL)
                    latest = events[-1] if events else None
                    self.assertEqual(s.review_reason(final, errs, "kp", now=p["now"], latest_evidence=latest), p["reason"])

    def test_review_queue(self):
        t0 = VECTORS["meta"]["t0"]
        s = sch.SpacedRepetitionScheduler()
        for case in VECTORS["queue"]:
            with self.subTest(case["id"]):
                states, types, log = {}, {}, []
                for kp, plan in case["plans"].items():
                    events = [evidence(e, t0) for e in plan["events"]]
                    states[kp] = s.replay(plan["knowledge_type"], events, now=t0)
                    types[kp] = plan["knowledge_type"]
                    log.extend(events)
                log.sort(key=lambda e: e.timestamp)
                queue = s.build_review_queue(states, types, set(case["error_kps"]), log, now=case["now"])
                got = [
                    {
                        "id": t.id,
                        "knowledge_point_id": t.knowledge_point_id,
                        "knowledge_type": t.knowledge_type,
                        "due_at": t.due_at,
                        "priority": t.priority,
                        "forgetting_risk": t.forgetting_risk,
                        "reason": t.reason,
                        "evidence_source": t.evidence_source,
                        "evidence_id": t.evidence_id,
                    }
                    for t in queue
                ]
                assert_close(self, got, case["queue"], "queue")


if __name__ == "__main__":
    unittest.main()
