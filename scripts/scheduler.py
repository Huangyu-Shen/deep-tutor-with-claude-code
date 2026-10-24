"""Spaced-repetition scheduler (exponential-forgetting baseline).

Adapted from DeepTutor (https://github.com/HKUDS/DeepTutor, Apache-2.0),
deeptutor/learning/scheduler.py. Modified: pydantic models replaced by
dataclasses; LearningProgress replaced by explicit arguments (states, types,
error knowledge points, evidence list); ``set_desired_retention``,
``schedule_next``, ``get_due_tasks``, the ``RetentionScheduler`` protocol, the
``BaselineRetentionScheduler`` alias and the LEARNING_DEBUG seconds-mode switch
omitted. The numeric logic, constants and
branch order are kept as in the original so results can be checked against it
(tests/golden).

Known limit inherited from DeepTutor: this is not a calibrated FSRS fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import time

# Knowledge types and their per-type tables (original: KnowledgeType enum).
MEMORY, CONCEPT, PROCEDURE, DESIGN = "memory", "concept", "procedure", "design"
KNOWLEDGE_TYPES = (MEMORY, CONCEPT, PROCEDURE, DESIGN)

INTERVAL_SEQUENCES: dict[str, list[int]] = {
    MEMORY: [0, 1, 3, 7, 14, 30, 60],
    CONCEPT: [3, 7, 14, 30],
    PROCEDURE: [3, 7, 14],
    DESIGN: [14, 28],
}

_TYPE_PRIORITY: dict[str, int] = {MEMORY: 2, CONCEPT: 3, PROCEDURE: 4, DESIGN: 5}
_TYPE_DIFFICULTY: dict[str, float] = {MEMORY: 0.3, CONCEPT: 0.4, PROCEDURE: 0.5, DESIGN: 0.6}

DEFAULT_DESIRED_RETENTION = 0.9
MIN_DESIRED_RETENTION = 0.7
MAX_DESIRED_RETENTION = 0.99
_MIN_STABILITY_DAYS = 0.5
_FAIL_QUALITY = 0.5
_EPS = 1e-6
_SAME_SESSION_DAYS = 0.25
_EVIDENCE_SOURCE_LABELS = {
    "deep_question": "Question Bank",
    "immersive_reading": "Reading",
    "book": "Book",
    "partner_chat": "Study Partner",
    "import": "Imported",
}


@dataclass
class LearningEvidence:
    knowledge_point_id: str
    timestamp: float = field(default_factory=time.time)
    evidence_id: str = ""
    question_id: str = ""
    source: str = "mastery_path"
    assessment_type: str = "quiz"  # quiz | qualitative | review
    result: str = "incorrect"  # correct | incorrect | partial
    quality: float | None = None
    hints_used: int = 0
    attempt_count: int = 1


@dataclass
class RepetitionState:
    next_review_at: float
    interval_index: int = 0
    consecutive_correct: int = 0
    consecutive_wrong: int = 0
    difficulty: float = 0.3
    stability: float = 0.0
    retrievability: float = 1.0
    desired_retention: float = 0.9
    review_count: int = 0
    lapse_count: int = 0
    last_review_at: float | None = None
    last_scheduled_at: float | None = None
    scheduled_after_failure: bool = False


@dataclass
class ReviewTask:
    id: str
    knowledge_point_id: str
    knowledge_type: str
    due_at: float
    priority: int
    state: RepetitionState
    forgetting_risk: float = 0.0
    reason: str = ""
    evidence_source: str = ""
    evidence_id: str = ""


def validate_desired_retention(value: float) -> float:
    desired = float(value)
    if not math.isfinite(desired) or not MIN_DESIRED_RETENTION <= desired <= MAX_DESIRED_RETENTION:
        raise ValueError(
            f"desired_retention must be between {MIN_DESIRED_RETENTION} and {MAX_DESIRED_RETENTION}"
        )
    return desired


def review_sort_key(task: ReviewTask, *, now: float) -> tuple[float, float, int]:
    """Higher forgetting risk, then more overdue, then lower type/error priority."""
    overdue = max(0.0, now - task.due_at)
    return (-task.forgetting_risk, -overdue, task.priority)


class SpacedRepetitionScheduler:
    def __init__(self, *, desired_retention: float = DEFAULT_DESIRED_RETENTION) -> None:
        # Intervals are days. (The original's seconds-based debug switch, read
        # from the LEARNING_DEBUG environment variable, is dropped here so an
        # inherited environment variable cannot silently change schedules.)
        self.desired_retention = validate_desired_retention(desired_retention)

    @staticmethod
    def _seconds_per_unit() -> float:
        return 86400.0

    @staticmethod
    def _stability_from_interval(interval_days: float, desired_retention: float) -> float:
        retention = min(max(desired_retention, _EPS), 1.0 - _EPS)
        if interval_days <= 0:
            return _MIN_STABILITY_DAYS
        return max(_MIN_STABILITY_DAYS, interval_days / -math.log(retention))

    @staticmethod
    def _interval_from_stability(stability: float, desired_retention: float) -> float:
        retention = min(max(desired_retention, _EPS), 1.0 - _EPS)
        return max(_EPS, max(stability, _EPS) * -math.log(retention))

    def _scheduled_interval_days(self, state: RepetitionState, knowledge_type: str) -> float:
        if state.review_count == 0 and INTERVAL_SEQUENCES[knowledge_type][0] == 0:
            return 0.0
        interval_days = self._interval_from_stability(state.stability, state.desired_retention)
        if state.scheduled_after_failure:
            interval_days = min(
                interval_days,
                max(interval_days * 0.5, _MIN_STABILITY_DAYS * 0.2),
            )
        return interval_days

    def hydrate(self, state: RepetitionState, knowledge_type: str) -> RepetitionState:
        if state.stability > 0:
            if state.difficulty <= 0:
                state.difficulty = _TYPE_DIFFICULTY.get(knowledge_type, 0.3)
            return state
        intervals = INTERVAL_SEQUENCES[knowledge_type]
        max_index = len(intervals) - 1
        state.interval_index = max(0, min(state.interval_index, max_index))
        state.desired_retention = state.desired_retention or DEFAULT_DESIRED_RETENTION
        state.stability = self._stability_from_interval(
            float(intervals[state.interval_index]), state.desired_retention
        )
        state.difficulty = _TYPE_DIFFICULTY.get(knowledge_type, 0.3)
        if state.retrievability <= 0:
            state.retrievability = 1.0
        return state

    def get_initial_state(
        self,
        knowledge_type: str,
        *,
        now: float | None = None,
        desired_retention: float | None = None,
    ) -> RepetitionState:
        intervals = INTERVAL_SEQUENCES[knowledge_type]
        first_interval = float(intervals[0])
        moment = time.time() if now is None else now
        desired = (
            self.desired_retention
            if desired_retention is None
            else validate_desired_retention(desired_retention)
        )
        stability = self._stability_from_interval(first_interval, DEFAULT_DESIRED_RETENTION)
        initial_interval = (
            first_interval
            if first_interval == 0
            else self._interval_from_stability(stability, desired)
        )
        return RepetitionState(
            interval_index=0,
            consecutive_correct=0,
            consecutive_wrong=0,
            next_review_at=moment + initial_interval * self._seconds_per_unit(),
            difficulty=_TYPE_DIFFICULTY[knowledge_type],
            stability=stability,
            retrievability=1.0,
            desired_retention=desired,
            review_count=0,
            lapse_count=0,
            last_review_at=None,
            last_scheduled_at=moment,
            scheduled_after_failure=False,
        )

    def schedule_review(
        self,
        state: RepetitionState,
        knowledge_type: str,
        evidence: LearningEvidence,
        *,
        now: float | None = None,
    ) -> RepetitionState:
        self.hydrate(state, knowledge_type)
        event_moment = evidence.timestamp if now is None else now
        moment = (
            max(event_moment, state.last_review_at)
            if state.last_review_at is not None
            else event_moment
        )
        quality = _resolved_quality(evidence)
        intervals = INTERVAL_SEQUENCES[knowledge_type]
        max_index = len(intervals) - 1

        previous_stability = max(state.stability, _EPS)
        previous_retrievability = self.retrievability(state, now=moment)
        elapsed_days = (
            max(0.0, moment - state.last_review_at) / self._seconds_per_unit()
            if state.last_review_at is not None
            else 0.0
        )
        same_session_days = min(
            _SAME_SESSION_DAYS,
            self._interval_from_stability(previous_stability, DEFAULT_DESIRED_RETENTION) * 0.5,
        )
        if state.last_review_at is not None and elapsed_days < same_session_days:
            if quality >= _FAIL_QUALITY:
                state.review_count += 1
                state.last_review_at = moment
                state.retrievability = 1.0
                state.consecutive_wrong = 0
                return state
            if state.scheduled_after_failure and (
                evidence.result == "partial" or evidence.assessment_type == "qualitative"
            ):
                state.review_count += 1
                state.last_review_at = moment
                state.retrievability = max(quality, 0.2)
                return state
        spacing_ratio = min(elapsed_days / previous_stability, 4.0)

        outcome_error = (1.0 - quality) - state.difficulty
        surprise = previous_retrievability - quality
        state.difficulty = float(
            min(0.95, max(0.05, state.difficulty + 0.12 * outcome_error + 0.08 * surprise))
        )

        if quality < _FAIL_QUALITY:
            state.consecutive_wrong += 1
            state.consecutive_correct = 0
            state.lapse_count += 1
            retention_factor = 0.55 - 0.25 * previous_retrievability
            difficulty_factor = 1.0 - 0.25 * state.difficulty
            state.stability = max(
                _MIN_STABILITY_DAYS * 0.1,
                previous_stability * retention_factor * difficulty_factor,
            )
            state.retrievability = max(quality, 0.2)
            if state.consecutive_wrong >= 2:
                state.consecutive_wrong = 0
        else:
            state.consecutive_wrong = 0
            state.consecutive_correct += 1
            retrieval_effort = 1.0 + (1.0 - previous_retrievability) * 1.5
            spacing_bonus = 1.0 + spacing_ratio * 0.15
            learnability = 1.35 - 0.6 * state.difficulty
            quality_strength = max(0.0, (quality - _FAIL_QUALITY) / (1.0 - _FAIL_QUALITY))
            growth = 1.0 + quality_strength * learnability * retrieval_effort * spacing_bonus
            if quality >= 0.8 and state.consecutive_correct >= 2:
                growth *= 1.15
            state.stability = max(_MIN_STABILITY_DAYS, previous_stability * growth)
            state.retrievability = 1.0
            if state.consecutive_correct >= 2:
                state.consecutive_correct = 0

        state.review_count += 1
        state.last_review_at = moment
        state.last_scheduled_at = moment
        state.scheduled_after_failure = quality < _FAIL_QUALITY
        interval_days = self._scheduled_interval_days(state, knowledge_type)
        state.next_review_at = moment + interval_days * self._seconds_per_unit()
        state.interval_index = _snap_interval_index(intervals, interval_days, max_index)
        return state

    def retrievability(self, state: RepetitionState, *, now: float | None = None) -> float:
        moment = time.time() if now is None else now
        stability = max(state.stability, _EPS)
        anchor = state.last_review_at if state.last_review_at is not None else state.next_review_at
        elapsed_days = max(0.0, (moment - anchor) / self._seconds_per_unit())
        return float(min(1.0, max(0.0, math.exp(-elapsed_days / stability))))

    def forgetting_risk(
        self,
        state: RepetitionState,
        error_kp_ids: set[str],
        kp_id: str,
        *,
        now: float | None = None,
    ) -> float:
        moment = time.time() if now is None else now
        recall = self.retrievability(state, now=moment)
        risk = 1.0 - recall
        if moment > state.next_review_at:
            overdue_days = (moment - state.next_review_at) / self._seconds_per_unit()
            risk += min(overdue_days / 7.0, 0.25)
        if kp_id in error_kp_ids:
            risk += 0.2
        if state.lapse_count:
            risk += min(0.1 * state.lapse_count, 0.2)
        return float(min(1.0, max(0.0, risk)))

    def review_reason(
        self,
        state: RepetitionState,
        error_kp_ids: set[str],
        kp_id: str,
        *,
        now: float | None = None,
        latest_evidence: LearningEvidence | None = None,
    ) -> str:
        moment = time.time() if now is None else now
        unit = self._seconds_per_unit()
        recall = self.retrievability(state, now=moment)
        parts: list[str] = []
        delta = moment - state.next_review_at
        if delta >= unit:
            parts.append(f"due {_format_span(delta / unit)} ago")
        elif delta >= 0:
            parts.append("due now")
        else:
            parts.append(f"due in {_format_span(-delta / unit)}")
        desired = state.desired_retention or DEFAULT_DESIRED_RETENTION
        if recall + 1e-9 < desired:
            parts.append(f"retrievability {recall:.0%} below {desired:.0%} target")
        else:
            parts.append(f"retrievability {recall:.0%}")
        failures = state.consecutive_wrong + (1 if kp_id in error_kp_ids else 0)
        if state.lapse_count:
            parts.append(f"{state.lapse_count} lapse{'s' if state.lapse_count != 1 else ''}")
        elif failures:
            parts.append("recent failure")
        if latest_evidence is not None:
            source = _EVIDENCE_SOURCE_LABELS.get(latest_evidence.source)
            if source is not None:
                parts.append(f"latest {source} assessment: {latest_evidence.result}")
        return "; ".join(parts) + "."

    def build_review_queue(
        self,
        states: dict[str, RepetitionState],
        knowledge_types: dict[str, str],
        error_kp_ids: set[str],
        evidence_log: list[LearningEvidence],
        *,
        now: float | None = None,
    ) -> list[ReviewTask]:
        moment = time.time() if now is None else now
        latest_evidence: dict[str, LearningEvidence] = {}
        for event in evidence_log:
            latest_evidence[event.knowledge_point_id] = event
        tasks: list[ReviewTask] = []
        for kp_id, state in states.items():
            kp_type = knowledge_types.get(kp_id, MEMORY)
            self.hydrate(state, kp_type)
            priority = 1 if kp_id in error_kp_ids else _TYPE_PRIORITY[kp_type]
            risk = self.forgetting_risk(state, error_kp_ids, kp_id, now=moment)
            evidence = latest_evidence.get(kp_id)
            tasks.append(
                ReviewTask(
                    id=f"review_{kp_id}",
                    knowledge_point_id=kp_id,
                    knowledge_type=kp_type,
                    due_at=state.next_review_at,
                    priority=priority,
                    state=state,
                    forgetting_risk=round(risk, 4),
                    reason=self.review_reason(
                        state, error_kp_ids, kp_id, now=moment, latest_evidence=evidence
                    ),
                    evidence_source=evidence.source if evidence is not None else "",
                    evidence_id=evidence.evidence_id if evidence is not None else "",
                )
            )
        tasks.sort(key=lambda t: review_sort_key(t, now=moment))
        return tasks

    def replay(
        self,
        knowledge_type: str,
        evidence: list[LearningEvidence],
        *,
        now: float | None = None,
        desired_retention: float | None = None,
        initial_state: RepetitionState | None = None,
    ) -> RepetitionState:
        if initial_state is not None:
            if desired_retention is not None:
                raise ValueError("initial_state already contains desired_retention")
            state = replace(initial_state)
            for event in evidence:
                self.schedule_review(state, knowledge_type, event, now=event.timestamp)
            return state
        if not evidence:
            return self.get_initial_state(
                knowledge_type, now=now, desired_retention=desired_retention
            )
        state = self.get_initial_state(
            knowledge_type,
            now=evidence[0].timestamp,
            desired_retention=desired_retention,
        )
        for event in evidence:
            self.schedule_review(state, knowledge_type, event, now=event.timestamp)
        return state


def _resolved_quality(evidence: LearningEvidence) -> float:
    if evidence.quality is not None:
        quality = float(evidence.quality)
    elif evidence.result == "correct":
        quality = 1.0
    elif evidence.result == "partial":
        quality = 0.4
    else:
        quality = 0.0
    if evidence.quality is None:
        quality -= min(0.25, evidence.hints_used * 0.08)
        quality -= min(0.2, max(0, evidence.attempt_count - 1) * 0.05)
    return max(0.0, min(1.0, quality))


def _snap_interval_index(intervals: list[int], interval_days: float, max_index: int) -> int:
    nearest = min(range(len(intervals)), key=lambda i: abs(float(intervals[i]) - interval_days))
    return max(0, min(nearest, max_index))


def _format_span(days: float) -> str:
    if days < 1.0 / 24.0:
        return "moments"
    if days < 1:
        hours = max(1, int(round(days * 24)))
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if days < 1.5:
        return "1 day"
    return f"{int(round(days))} days"
