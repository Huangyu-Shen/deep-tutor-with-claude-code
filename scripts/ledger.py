"""Learner ledger: an append-only event log and the state replayed from it.

Event/record fields follow DeepTutor (https://github.com/HKUDS/DeepTutor,
Apache-2.0), deeptutor/learning/models.py: LearningEvidence, ErrorRecord,
KnowledgeType, ErrorType. `classify_error` is adapted from
deeptutor/learning/grading.py. Error records use DeepTutor's status names but a
stricter rule: DeepTutor graduates an error after one correct retry of the same
question; here it takes two consecutive correct answers (see NOTICE).

Mastery follows DeepTutor's policy (deeptutor/learning/service.py): each
attempt counts 1.0 (correct), 0.5 (partial) or 0.0 (incorrect), and only
unaided attempts (hints_used == 0) count, so hinted answers do not inflate it.
The attempt's `quality` is used by the review scheduler, not by mastery.
Not ported: DeepTutor's legacy value mappings for KnowledgeType/ErrorType.

Log: <root>/.tutor/learner/<learner>/events.jsonl, one JSON object per line.
State is never stored; it is recomputed by replaying the log in file order,
so fixing the scheduler later re-derives history correctly.

Commands (JSON on stdout; failures give {"ok": false, "error": ...}, exit 1):
  record  < event.json      append one event
  state                     per-knowledge-point mastery and schedule
  due                       review queue, highest forgetting risk first
  errors [--status S]       error records
  export [--format json|md] everything, for backup or reading
  now                       current time as epoch, ISO and a file-name stamp
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mastery import compute_mastery  # noqa: E402
import scheduler as sch  # noqa: E402

SCHEMA_VERSION = 1
KNOWLEDGE_TYPES = sch.KNOWLEDGE_TYPES
ERROR_TYPES = ("structural", "deviation", "application", "metacognitive")
RESULTS = ("correct", "partial", "incorrect")
ASSESSMENT_TYPES = ("quiz", "qualitative", "review")
ACTIVE_ERROR_STATUSES = ("active", "retrying")
GRADUATE_AFTER = 2  # consecutive correct retries needed to graduate an error
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
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
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


def replay(events: list[dict]) -> dict:
    """Derive knowledge points, schedule states and error records from the log."""
    kps: dict[str, dict] = {}
    errors: dict[tuple, dict] = {}
    for event in events:
        if event["type"] == "kp_define":
            entry = kps.setdefault(
                event["kp"],
                {"kp": event["kp"], "kb": event["kb"], "name": event["name"],
                 "knowledge_type": event["knowledge_type"], "sources": [], "attempts": []},
            )
            for src in event.get("sources", []):
                if src not in entry["sources"]:
                    entry["sources"].append(src)
            continue
        entry = kps.get(event["kp"])
        if entry is None:
            continue  # attempt for an undefined kp: reported by read_events/validate on write
        entry["attempts"].append(event)
        qid = event.get("question_id") or ""
        key = (event["kp"], qid)
        record = errors.get(key)
        if event["result"] == "incorrect":
            if record is None:
                errors[key] = record = {
                    "id": f"err_{len(errors) + 1}", "kp": event["kp"], "question_id": qid,
                    "error_type": event.get("error_type", "application"),
                    "question": event.get("question", ""),
                    "self_attribution": event.get("self_attribution", ""),
                    "ai_confirmation": event.get("ai_confirmation", ""),
                    "status": "active", "created_at": event["ts"], "retry_history": [],
                    "_streak": 0, "_first_event": event["id"],
                }
            else:
                record["retry_history"].append({"ts": event["ts"], "is_correct": False})
                record["_streak"] = 0
                record["status"] = "retrying"
                if event.get("error_type"):
                    record["error_type"] = event["error_type"]
                if event.get("ai_confirmation"):
                    record["ai_confirmation"] = event["ai_confirmation"]
        elif record is not None and record["status"] != "graduated":
            ok = event["result"] == "correct"
            record["retry_history"].append({"ts": event["ts"], "is_correct": ok})
            if ok:
                record["_streak"] += 1
                record["status"] = "graduated" if record["_streak"] >= GRADUATE_AFTER else "review"
            else:  # partial
                record["_streak"] = 0
                record["status"] = "retrying"
    return {"kps": kps, "errors": errors}


def schedule(kps: dict, errors: dict):
    """Run DeepTutor's scheduler over each kp's attempts, in log order."""
    scheduler = sch.SpacedRepetitionScheduler()
    states, types, evidence_log = {}, {}, []
    for kp, entry in kps.items():
        if not entry["attempts"]:
            continue
        evidence = [
            sch.LearningEvidence(
                knowledge_point_id=kp, timestamp=a["ts"], evidence_id=a["id"],
                question_id=a.get("question_id", ""), source=a.get("source", "deep_question"),
                assessment_type=a.get("assessment_type", "quiz"), result=a["result"],
                quality=a.get("quality"), hints_used=a.get("hints_used", 0),
                attempt_count=a.get("attempt_count", 1),
            )
            for a in entry["attempts"]
        ]
        states[kp] = scheduler.replay(entry["knowledge_type"], evidence)
        types[kp] = entry["knowledge_type"]
        evidence_log.extend(evidence)
    error_kps = {r["kp"] for r in errors.values() if r["status"] in ACTIVE_ERROR_STATUSES}
    return scheduler, states, types, error_kps, evidence_log


def public_error(record: dict) -> dict:
    return {k: v for k, v in record.items() if not k.startswith("_")}


def iso(ts: float) -> str:
    """UTC date-time for humans; epoch floats are hard to read (and to convert) for a model."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_state(events: list[dict], now: float) -> dict:
    derived = replay(events)
    kps, errors = derived["kps"], derived["errors"]
    scheduler, states, _types, error_kps, _log = schedule(kps, errors)
    out = []
    for kp, entry in kps.items():
        attempts = entry["attempts"]
        item = {
            "kp": kp, "kb": entry["kb"], "name": entry["name"],
            "knowledge_type": entry["knowledge_type"], "sources": entry["sources"],
            "attempts": len(attempts),
            "mastery": compute_mastery(mastery_scores(attempts)),
            "unaided_attempts": len(mastery_scores(attempts)),
            "open_errors": sum(
                1 for r in errors.values() if r["kp"] == kp and r["status"] in ACTIVE_ERROR_STATUSES
            ),
        }
        if kp in states:
            st = states[kp]
            item["schedule"] = {
                **asdict(st),
                "next_review_date": iso(st.next_review_at),
                "retrievability_now": scheduler.retrievability(st, now=now),
                "forgetting_risk_now": scheduler.forgetting_risk(st, error_kps, kp, now=now),
            }
        out.append(item)
    return {"now_iso": iso(now), "knowledge_points": out}


def build_due(events: list[dict], now: float, limit: int | None, kb: str | None) -> dict:
    derived = replay(events)
    scheduler, states, types, error_kps, log = schedule(derived["kps"], derived["errors"])
    if kb:
        states = {k: v for k, v in states.items() if derived["kps"][k]["kb"] == kb}
    queue = scheduler.build_review_queue(states, types, error_kps, log, now=now)
    due = [t for t in queue if t.due_at <= now]
    shown = due if limit is None else due[:limit]
    return {
        "now": now, "now_iso": iso(now), "due_count": len(due), "tracked_count": len(queue),
        "queue": [
            {"kp": t.knowledge_point_id, "name": derived["kps"][t.knowledge_point_id]["name"],
             "knowledge_type": t.knowledge_type, "due_at": t.due_at, "due_date": iso(t.due_at),
             "priority": t.priority,
             "forgetting_risk": t.forgetting_risk, "reason": t.reason,
             "sources": derived["kps"][t.knowledge_point_id]["sources"]}
            for t in shown
        ],
    }


# ----------------------------------------------------------------------- CLI


def to_markdown(events: list[dict], now: float) -> str:
    state = build_state(events, now)
    errors = [public_error(r) for r in replay(events)["errors"].values()]
    lines = ["# Learning ledger export", ""]
    for kp in state["knowledge_points"]:
        lines.append(f"- **{kp['name']}** ({kp['knowledge_type']}, {kp['kb']}): "
                     f"mastery {kp['mastery']:.2f}, {kp['attempts']} attempts, "
                     f"{kp['open_errors']} open errors")
    lines += ["", "## Errors", ""]
    for e in errors:
        lines.append(f"- {e['kp']} [{e['error_type']}] status={e['status']} "
                     f"question={e['question_id'] or '-'}")
    return "\n".join(lines) + "\n"


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


def run(args, stdin) -> dict:
    path = log_path(args.root, args.learner)
    now = args.now if args.now is not None else time.time()
    if args.command == "now":
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        return {"ok": True, "now": now, "iso": iso(now), "stamp": stamp}
    events, warnings = read_events(path)
    if args.command == "record":
        try:
            raw = loads_strict(stdin.read())
        except ValueError as exc:
            raise LedgerError(f"stdin is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise LedgerError("event must be a JSON object")
        event = prepare_event(raw, events, now)
        # Replay with the new event BEFORE writing, so an event that breaks replay is never stored.
        state_after = build_state(events + [event], now)
        result = {"recorded": event}
        if event["type"] == "attempt":
            item = next(k for k in state_after["knowledge_points"] if k["kp"] == event["kp"])
            result["kp_summary"] = {k: item[k] for k in ("kp", "mastery", "unaided_attempts", "attempts", "open_errors")}
            result["kp_summary"]["next_review_at"] = item["schedule"]["next_review_at"]
            result["kp_summary"]["next_review_date"] = item["schedule"]["next_review_date"]
        path.parent.mkdir(parents=True, exist_ok=True)
        append_line(path, json.dumps(event, ensure_ascii=False, sort_keys=True))
    elif args.command == "state":
        result = build_state(events, now)
    elif args.command == "due":
        result = build_due(events, now, args.limit, args.kb)
    elif args.command == "errors":
        recs = [public_error(r) for r in replay(events)["errors"].values()]
        if args.status:
            recs = [r for r in recs if r["status"] == args.status]
        result = {"errors": recs}
    elif args.command == "export":
        if args.format == "md":
            result = {"markdown": to_markdown(events, now)}
        else:
            derived = replay(events)
            result = {"events": events, "state": build_state(events, now),
                      "errors": [public_error(r) for r in derived["errors"].values()]}
    else:  # pragma: no cover - argparse guards this
        raise LedgerError("unknown command")
    result["ok"] = True
    if warnings:
        result["warnings"] = warnings
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project root containing .tutor/")
    p.add_argument("--learner", default="default")
    p.add_argument("--now", type=float, default=None, help="epoch seconds (testing)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("record")
    sub.add_parser("now")
    sub.add_parser("state")
    due = sub.add_parser("due")
    due.add_argument("--limit", type=int, default=None)
    due.add_argument("--kb", default=None)
    err = sub.add_parser("errors")
    err.add_argument("--status", choices=("active", "retrying", "review", "graduated"))
    exp = sub.add_parser("export")
    exp.add_argument("--format", choices=("json", "md"), default="json")
    return p


def emit(obj: dict) -> None:
    try:
        print(json.dumps(obj, ensure_ascii=False, allow_nan=False))
    except UnicodeEncodeError:  # e.g. a lone surrogate in free text
        print(json.dumps(obj, ensure_ascii=True, allow_nan=False))


def main(argv=None, stdin=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(args, stdin or sys.stdin)
    except LedgerError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # keep the JSON contract even for unexpected failures
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
