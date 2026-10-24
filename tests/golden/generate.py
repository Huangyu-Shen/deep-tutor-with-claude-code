"""Generate golden vectors by running DeepTutor's ORIGINAL code.

Development-time only. Needs a DeepTutor source checkout and pydantic:

    python3 -I tests/golden/generate.py /path/to/DeepTutor-main

Writes tests/golden/vectors.json. The ported scripts must reproduce every
value in it (tests/test_golden.py). The file records the DeepTutor version and
SHA-256 of the original files used, so the provenance is checkable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

DT_ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
if DT_ROOT is None or not (DT_ROOT / "deeptutor").is_dir():
    raise SystemExit("usage: generate.py /path/to/DeepTutor-main")
sys.path.insert(0, str(DT_ROOT))

from deeptutor.__version__ import __version__ as DT_VERSION  # noqa: E402
from deeptutor.learning.mastery import compute_mastery  # noqa: E402
from deeptutor.learning.models import (  # noqa: E402
    ErrorRecord,
    KnowledgeType,
    LearningEvidence,
    LearningProgress,
)
from deeptutor.learning.scheduler import SpacedRepetitionScheduler  # noqa: E402

T0 = 1_700_000_000.0
DAY = 86400.0
HOUR = 3600.0
MIN = 60.0

KP_TYPES = ["memory", "concept", "procedure", "design"]


def ev(day_offset: float, result: str, **kw) -> dict:
    return {"offset_s": day_offset * DAY, "result": result, **kw}


# name -> list of event specs (offset from T0 in seconds)
SCENARIOS: dict[str, list[dict]] = {
    "empty": [],
    "all_correct_spaced": [ev(0, "correct"), ev(1, "correct"), ev(4, "correct"), ev(11, "correct"), ev(26, "correct")],
    "all_incorrect": [ev(0, "incorrect"), ev(1, "incorrect"), ev(2, "incorrect"), ev(3, "incorrect")],
    "lapse_then_recover": [ev(0, "correct"), ev(2, "correct"), ev(9, "incorrect"), ev(10, "correct"), ev(14, "correct"), ev(25, "correct")],
    "same_session_correct": [ev(0, "correct"), {"offset_s": 10 * MIN, "result": "correct"}, {"offset_s": 20 * MIN, "result": "correct"}, ev(3, "correct")],
    "same_session_fail_then_partial": [ev(0, "correct"), ev(5, "incorrect"), {"offset_s": 5 * DAY + 30 * MIN, "result": "partial"}, {"offset_s": 5 * DAY + 40 * MIN, "result": "partial", "assessment_type": "qualitative"}, ev(8, "correct")],
    "long_gap_then_correct": [ev(0, "correct"), ev(120, "correct")],
    "long_gap_then_incorrect": [ev(0, "correct"), ev(2, "correct"), ev(200, "incorrect")],
    "hints_and_attempts": [ev(0, "correct", hints_used=2), ev(3, "correct", attempt_count=4), ev(8, "correct", hints_used=5, attempt_count=9), ev(20, "partial", hints_used=1)],
    "explicit_quality": [ev(0, "correct", quality=0.95), ev(2, "incorrect", quality=0.45), ev(3, "correct", quality=0.5), ev(6, "partial", quality=0.7), ev(9, "correct", quality=1.0)],
    "out_of_order_timestamps": [ev(5, "correct"), ev(2, "correct"), ev(9, "incorrect"), ev(7, "correct")],
    "alternating": [ev(i * 2, "correct" if i % 2 == 0 else "incorrect") for i in range(10)],
    # --- branches the first version of the vectors missed (found in review) ---
    "same_session_fail_after_success": [ev(0, "correct"), ev(3, "correct"), ev(3 + 10 / 1440, "incorrect")],
    "same_session_fail_inside_failure_episode": [
        ev(0, "correct"), ev(5, "incorrect"), ev(5 + 20 / 1440, "incorrect"),
        ev(5 + 30 / 1440, "incorrect", assessment_type="qualitative", quality=0.3),
        ev(5 + 40 / 1440, "partial", quality=0.1), ev(9, "correct")],
    "same_session_qualitative_incorrect_only": [
        ev(0, "correct"), ev(5, "incorrect"), ev(5 + 15 / 1440, "incorrect", assessment_type="qualitative"),
        ev(8, "correct")],
    "same_session_window_5h_after_long_interval": [
        ev(0, "correct"), ev(1, "correct"), ev(4, "correct"), ev(11, "correct"), ev(26, "correct"),
        ev(26 + 5 / 24, "correct"), ev(26 + 5.5 / 24, "incorrect")],
    "same_session_window_6h30_after_long_interval": [
        ev(0, "correct"), ev(1, "correct"), ev(4, "correct"), ev(11, "correct"), ev(26, "correct"),
        ev(26 + 6.5 / 24, "correct"), ev(26 + 7.2 / 24, "correct")],
    "same_session_window_90min_fresh_item": [ev(0, "correct"), ev(90 / 1440, "correct"), ev(2, "correct")],
    "same_session_partial_after_success": [ev(0, "correct"), ev(3, "correct"),
                                           ev(3 + 10 / 1440, "partial", quality=0.3),
                                           ev(3 + 20 / 1440, "incorrect", assessment_type="qualitative", quality=0.2)],
    "quality_boundaries": [
        ev(0, "correct", quality=0.8), ev(2, "correct", quality=0.8), ev(5, "correct", quality=0.79),
        ev(9, "correct", quality=0.81), ev(14, "correct", quality=0.5), ev(20, "incorrect", quality=0.49),
        ev(21, "correct", quality=0.5), ev(30, "correct", quality=0.8), ev(45, "correct", quality=0.8)],
    "same_session_quality_exactly_half": [ev(0, "correct"), ev(2, "correct"), ev(2 + 5 / 1440, "correct", quality=0.5),
                                          ev(2 + 8 / 1440, "incorrect", quality=0.49)],
}

RETENTIONS = [None, 0.8, 0.95]
PROBE_OFFSETS_DAYS = [0.0, 0.5, 1.0, 3.0, 10.0, 40.0, 365.0]


def to_dt_evidence(kp: str, spec: dict) -> LearningEvidence:
    kw = {k: v for k, v in spec.items() if k not in {"offset_s"}}
    return LearningEvidence(
        knowledge_point_id=kp,
        evidence_id=f"e{int(spec['offset_s'])}",
        timestamp=T0 + spec["offset_s"],
        source=kw.pop("source", "deep_question"),
        **kw,
    )


def state_dict(state) -> dict:
    return json.loads(state.model_dump_json())


def build_replay_cases() -> list[dict]:
    cases = []
    for ktype in KP_TYPES:
        for name, specs in SCENARIOS.items():
            for retention in RETENTIONS:
                sched = SpacedRepetitionScheduler()
                evidence = [to_dt_evidence("kp", s) for s in specs]
                steps = []
                if evidence:
                    state = sched.get_initial_state(
                        KnowledgeType(ktype),
                        now=evidence[0].timestamp,
                        desired_retention=retention,
                    )
                    for e in evidence:
                        sched.schedule_review(state, KnowledgeType(ktype), e, now=e.timestamp)
                        steps.append(state_dict(state))
                final = sched.replay(
                    KnowledgeType(ktype),
                    evidence,
                    now=T0,
                    desired_retention=retention,
                )
                probes = []
                err_sets = [[], ["kp"]]
                for off in PROBE_OFFSETS_DAYS:
                    now = T0 + (specs[-1]["offset_s"] if specs else 0) + off * DAY
                    for errs in err_sets:
                        progress = LearningProgress(
                            book_id="b",
                            error_records=[
                                ErrorRecord(
                                    id="r",
                                    question_id="q",
                                    knowledge_point_id=k,
                                    module_id="m",
                                    error_type="application",
                                    status="active",
                                )
                                for k in errs
                            ],
                        )
                        probes.append(
                            {
                                "now": now,
                                "error_kps": errs,
                                "retrievability": sched.retrievability(final, now=now),
                                "forgetting_risk": sched.forgetting_risk(final, progress, "kp", now=now),
                                "reason": sched.review_reason(
                                    final, progress, "kp", now=now,
                                    latest_evidence=evidence[-1] if evidence else None,
                                ),
                            }
                        )
                cases.append(
                    {
                        "id": f"{ktype}/{name}/ret={retention}",
                        "knowledge_type": ktype,
                        "desired_retention": retention,
                        "events": [{"kp": "kp", **s} for s in specs],
                        "initial_now": T0,
                        "steps": steps,
                        "final_state": state_dict(final),
                        "probes": probes,
                    }
                )
    return cases


def build_queue_cases() -> list[dict]:
    """Several knowledge points at once: ordering, priority, reasons."""
    sched = SpacedRepetitionScheduler()
    plans = {
        "a_mem": ("memory", SCENARIOS["all_correct_spaced"]),
        "b_con": ("concept", SCENARIOS["lapse_then_recover"]),
        "c_pro": ("procedure", SCENARIOS["all_incorrect"]),
        "d_des": ("design", SCENARIOS["long_gap_then_correct"]),
        "e_mem2": ("memory", SCENARIOS["alternating"]),
        "f_con2": ("concept", SCENARIOS["hints_and_attempts"]),
    }
    cases = []
    for errs in ([], ["c_pro"], ["a_mem", "d_des"]):
        for now_off in (5.0, 30.0, 90.0, 400.0):
            states, types, log = {}, {}, []
            for kp, (ktype, specs) in plans.items():
                evidence = [to_dt_evidence(kp, s) for s in specs]
                states[kp] = sched.replay(KnowledgeType(ktype), evidence, now=T0)
                types[kp] = KnowledgeType(ktype)
                log.extend(evidence)
            now = T0 + now_off * DAY
            progress = LearningProgress(
                book_id="b",
                knowledge_types=types,
                repetition_states=states,
                learning_evidence=sorted(log, key=lambda e: e.timestamp),
                error_records=[
                    ErrorRecord(
                        id=f"r{k}", question_id="q", knowledge_point_id=k, module_id="m",
                        error_type="application", status="active",
                    )
                    for k in errs
                ],
            )
            queue = sched.build_review_queue(progress, now=now)
            cases.append(
                {
                    "id": f"queue/errs={errs}/now+{now_off}d",
                    "plans": {kp: {"knowledge_type": t, "events": [{"kp": kp, **s} for s in specs]} for kp, (t, specs) in plans.items()},
                    "error_kps": errs,
                    "now": now,
                    "queue": [
                        {
                            "id": t.id,
                            "knowledge_point_id": t.knowledge_point_id,
                            "knowledge_type": t.knowledge_type.value,
                            "due_at": t.due_at,
                            "priority": t.priority,
                            "forgetting_risk": t.forgetting_risk,
                            "reason": t.reason,
                            "evidence_source": t.evidence_source,
                            "evidence_id": t.evidence_id,
                        }
                        for t in queue
                    ],
                }
            )
    return cases


def build_mastery_cases() -> list[dict]:
    inputs = [
        [], [True], [False], [1.0], [0.5], [True, True], [True, False], [False, True],
        [True, True, True], [True, True, True, True, True], [False] * 5,
        [False, False, True, True, True], [True, True, True, True, True, False],
        [True, False, True, False, True, False, True], [0.2, 0.4, 0.6, 0.8, 1.0],
        [1.5, -0.3, 0.5], [True] * 12, [0.9, 0.1],
    ]
    return [{"correctness": c, "mastery": compute_mastery(c)} for c in inputs]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    out = {
        "meta": {
            "deeptutor_version": DT_VERSION,
            "source_files": {
                p: sha256(DT_ROOT / p)
                for p in (
                    "deeptutor/learning/mastery.py",
                    "deeptutor/learning/scheduler.py",
                    "deeptutor/learning/models.py",
                )
            },
            "t0": T0,
        },
        "mastery": build_mastery_cases(),
        "replay": build_replay_cases(),
        "queue": build_queue_cases(),
    }
    target = Path(__file__).with_name("vectors.json")
    target.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {target} ({len(out['replay'])} replay, {len(out['queue'])} queue, {len(out['mastery'])} mastery cases)")


if __name__ == "__main__":
    main()
