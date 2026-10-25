"""Shared learner event log: validation, replay input and durable appends.

`ledger.py` and `path.py` both read the same log, so the event contract lives
here once. This module is a pure library: it never imports `ledger.py` or
`path.py`, and it has no CLI. Event/record fields follow DeepTutor
(https://github.com/HKUDS/DeepTutor, Apache-2.0),
deeptutor/learning/models.py: LearningEvidence, ErrorRecord, KnowledgeType,
ErrorType.

Log: <root>/.tutor/learner/<learner>/events.jsonl, one JSON object per line.
Damaged lines are reported by `read_events`, never dropped silently.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
import unicodedata
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scheduler as sch  # noqa: E402

SCHEMA_VERSION = 1
KNOWLEDGE_TYPES = sch.KNOWLEDGE_TYPES
ERROR_TYPES = ("structural", "deviation", "application", "metacognitive")
RESULTS = ("correct", "partial", "incorrect")
ASSESSMENT_TYPES = ("quiz", "qualitative", "review")
ORIGINS = ("learn", "quiz", "review", "diagnostic", "exemption")
TEXT_FIELDS = ("question_id", "learner_answer", "feedback", "question", "source",
               "ai_confirmation", "self_attribution")
FUTURE_SLACK = 86400.0  # a recorded timestamp may be at most this far ahead of "now"


class LedgerError(Exception):
    pass


# ---------------------------------------------------------------- identifiers


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name).strip().lower())


def kp_id(kb: str, name: str) -> str:
    """Stable id from knowledge base + normalized knowledge-point name."""
    kb, norm = kb.strip(), normalize_name(name)
    if not kb or not norm:
        raise LedgerError("kb and name must be non-empty")
    if "::" in kb or "::" in norm:
        raise LedgerError("kb and name must not contain '::', the id separator")
    return f"{kb}::{norm}"


def classify_error(learner_answer: str):
    """Coarse default (adapted from DeepTutor): blank = metacognitive."""
    return "metacognitive" if not learner_answer.strip() else "application"


# ------------------------------------------------------------------- storage


def log_path(root: str, learner: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", learner):
        raise LedgerError("learner name may contain only letters, digits, _ . - and must not start with a dot")
    return Path(root) / ".tutor" / "learner" / learner / "events.jsonl"


def _reject_constant(name: str):
    raise ValueError(f"{name} is not a valid number")


def loads_strict(text: str):
    """json.loads that refuses NaN / Infinity."""
    return json.loads(text, parse_constant=_reject_constant)


def read_events(path: Path):
    """Return (events, warnings). Damaged lines are reported, never dropped silently."""
    events, warnings, seen, defined = [], [], set(), set()
    if not path.exists():
        return events, warnings
    with path.open("rb") as fh:  # bytes, so one bad encoding cannot take the log down
        for lineno, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                warnings.append({"line": lineno,
                                 "problem": f"skipped: not UTF-8 (byte {exc.start}: {exc.reason})"})
                continue
            if not line.strip():
                continue
            try:
                event = loads_strict(line)
                if not isinstance(event, dict):
                    raise ValueError("not an object")
                validate_event(event, defined)
            except Exception as exc:  # one bad line must never take the whole log down
                warnings.append({"line": lineno, "problem": f"skipped: {type(exc).__name__}: {exc}"})
                continue
            if event["id"] in seen:
                warnings.append({"line": lineno, "problem": f"skipped: duplicate id {event['id']}"})
                continue
            seen.add(event["id"])
            if event["type"] == "kp_define":
                defined.add(event["kp"])
            if event["type"] == "attempt" and event.get("origin") is not None \
                    and event["origin"] not in ORIGINS:
                warnings.append({"line": lineno,
                                 "problem": f"unknown origin {event['origin']!r}: it counts for mastery "
                                            "but not for gates"})
            events.append(event)
    return events, warnings


# ---------------------------------------------------------------- validation


def _num(event, key, lo=None, hi=None, integer=False):
    value = event.get(key)
    try:
        finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:  # an integer too large for a float
        finite = False
    if not finite:
        raise LedgerError(f"{key} must be a finite number")
    if integer and int(value) != value:
        raise LedgerError(f"{key} must be an integer")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise LedgerError(f"{key} out of range")


def validate_event(event: dict, defined: set | None) -> None:
    """Check one event. `defined` = known kp ids; None skips the cross-check."""
    for key in ("id", "ts", "type"):
        if key not in event:
            raise LedgerError(f"missing {key}")
    if not isinstance(event["id"], str) or not event["id"]:
        raise LedgerError("id must be a non-empty string")
    _num(event, "ts", lo=0)
    etype = event["type"]
    if etype == "kp_define":
        if event.get("knowledge_type") not in KNOWLEDGE_TYPES:
            raise LedgerError(f"knowledge_type must be one of {KNOWLEDGE_TYPES}")
        for key in ("kp", "kb", "name"):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise LedgerError(f"missing {key}")
        if event["kp"] != kp_id(event["kb"], event["name"]):
            raise LedgerError("kp does not match kb + name")
        sources = event.get("sources", [])
        if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
            raise LedgerError("sources must be a list of strings")
    elif etype == "attempt":
        if not isinstance(event.get("kp"), str) or not event["kp"]:
            raise LedgerError("missing kp")
        for key in TEXT_FIELDS:
            if key in event and not isinstance(event[key], str):
                raise LedgerError(f"{key} must be a string")
        if defined is not None and event["kp"] not in defined:
            raise LedgerError(f"unknown kp {event['kp']!r}: record a kp_define first")
        if event.get("result") not in RESULTS:
            raise LedgerError(f"result must be one of {RESULTS}")
        if event.get("assessment_type", "quiz") not in ASSESSMENT_TYPES:
            raise LedgerError(f"assessment_type must be one of {ASSESSMENT_TYPES}")
        # `origin` is validated on write (prepare_event), not on read: an older or
        # third-party log may carry an origin this version does not know, and reading
        # it must still report the attempt instead of dropping the line.
        if event.get("quality") is not None:
            _num(event, "quality", 0, 1)
        for key in ("hints_used", "attempt_count"):
            if key in event:
                _num(event, key, 0, integer=True)
        if event.get("error_type") is not None and event["error_type"] not in ERROR_TYPES:
            raise LedgerError(f"error_type must be one of {ERROR_TYPES}")
        if event["result"] != "incorrect" and event.get("error_type"):
            raise LedgerError("error_type only applies to incorrect attempts")
    else:
        raise LedgerError(f"unknown event type {etype!r}")


def prepare_event(raw: dict, existing: list, now: float) -> dict:
    """Fill defaults, validate against the existing log, return the event."""
    event = {k: v for k, v in raw.items() if v is not None}  # null means "not given"
    for key in ("kb", "name", "kp"):
        if isinstance(event.get(key), str):
            event[key] = event[key].strip()
    event.setdefault("id", uuid.uuid4().hex)
    event.setdefault("ts", now)
    event["v"] = SCHEMA_VERSION
    ts = event.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > now + FUTURE_SLACK:
        raise LedgerError("ts is in the future (are the seconds really milliseconds?)")
    if event.get("type") == "kp_define" and "kp" not in event:
        event["kp"] = kp_id(event.get("kb", ""), event.get("name", ""))
    if event.get("type") == "attempt" and event.get("origin") is not None \
            and event["origin"] not in ORIGINS:
        raise LedgerError(f"origin must be one of {ORIGINS}")
    if event.get("type") == "attempt" and event.get("result") == "incorrect":
        event.setdefault("error_type", classify_error(str(event.get("learner_answer", ""))))
    if any(e["id"] == event["id"] for e in existing):
        raise LedgerError(f"duplicate event id {event['id']!r}")
    defined = {e["kp"] for e in existing if e["type"] == "kp_define"}
    if event.get("type") == "kp_define":
        previous = [e for e in existing if e["type"] == "kp_define" and e["kp"] == event["kp"]]
        if previous and previous[-1]["knowledge_type"] != event.get("knowledge_type"):
            raise LedgerError("knowledge_type of an existing kp cannot change")
        defined.add(event["kp"])
    validate_event(event, defined)
    return event


# -------------------------------------------------------------------- replay


def _attempt_score(event: dict) -> float:
    return {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}[event["result"]]


def mastery_scores(attempts: list[dict]) -> list[float]:
    """Unaided attempts only, as in DeepTutor."""
    return [_attempt_score(a) for a in attempts if not a.get("hints_used")]


def append_line(path: Path, line: str) -> None:
    """Append one line durably; repair a missing final newline so records never merge."""
    prefix = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                prefix = "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
