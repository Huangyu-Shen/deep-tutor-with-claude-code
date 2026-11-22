#!/usr/bin/env python3
"""Memory data layer for DeepTutor@Claude Code.

Per-learner files under <root>/.tutor/memory/<learner>/:
  trace/<surface>/<UTC date>.jsonl   L1 events, append-only
  L2/<surface>.md, L3/<slot>.md      Markdown documents with footnote citations
  snapshot/<surface>/state.json + changes.jsonl
  entities/chat/<id>.json            what `/tutor:learn` wrote (see chat-put)

Format and behavior follow DeepTutor's memory service (services/memory/, 1.6.15);
the pure parts live in memory_doc.py and memory_snap.py. This script never reads
or writes the learner ledger's events, mastery, gates, errors or review schedule.

Commands (JSON on stdout; failures give {"ok": false, "error": ...}, exit 1):
  emit        --surface S      one L1 event from stdin {"kind","payload"}; an append
                               failure is a "warnings" entry, not a failure
  trace       --surface S [--since ISO | --count | --latest] | --ids A,B
  overview                     one row per L2 surface and L3 slot
  read-l3                      the four L3 documents joined, or "尚无记忆"
  doc-get     --layer L2|L3 --key K [--format md|json]
  doc-put     --layer --key   whole-document overwrite, stdin {"markdown": "..."}
  ops         --layer --key   atomic batch of add/edit/delete, stdin {"ops": [...]}
  entry-delete --layer --key --id   delete one entry (no reason needed)
  preference                   stdin {"op":"add|edit","text":...,"target_id":...}
  chat-put    --call-id ID     write/replace the `chat` entity for one /tutor:learn call,
                               stdin {"question","kps","points":[{"text","source"}],"check"}
  snapshot refresh|preview --surface S [--allow-empty]
  snapshot changes --surface S [--limit N --offset N]
  recent      [--days N --limit N --surface S]   what the learner touched lately
  recent-queries [--days N --limit N]            what they asked a knowledge base lately
"""

from __future__ import annotations

import argparse
from collections import namedtuple
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learner_log as ll  # noqa: E402
import memory_doc as md  # noqa: E402
import memory_snap as ms  # noqa: E402

L3_SLOTS = ("recent", "profile", "scope", "preferences")
# Surfaces this plugin feeds in 0.5.0; the other four keep their names only.
ENABLED_SURFACES = ("chat", "quiz", "kb")
LAYERS = ("L2", "L3")
NO_MEMORY = "尚无记忆"
PREF_SECTION = "Preferences"
PREF_MAX = md.MAX_TEXT_LEN
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class MemoryError_(Exception):
    pass


class MemoryArgumentParser(argparse.ArgumentParser):
    """Usage errors are failures too: JSON on stdout, exit 1."""

    def error(self, message: str):
        raise MemoryError_(f"usage error: {message}")


# ------------------------------------------------------------------- storage


def check_learner(value: str) -> str:
    if not _NAME.fullmatch(value or "") or set(value) == {"."}:
        raise MemoryError_(
            "learner name may contain only letters, digits, _ . - and must not start with a dot")
    return value


def memory_dir(args) -> Path:
    return Path(args.root) / ".tutor" / "memory" / check_learner(args.learner)


def now_of(args) -> float:
    return args.now if args.now is not None else time.time()


def iso_of(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_line(path: Path, line: str) -> None:
    """Durable append; repairs a missing final newline so a torn line never swallows the next."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ll.append_line(path, line)


def check_surface(surface: str, enabled_only: bool = True) -> str:
    if surface not in md.SURFACES:
        raise MemoryError_(f"unknown surface {surface!r}; expected one of {', '.join(md.SURFACES)}")
    if enabled_only and surface not in ENABLED_SURFACES:
        raise MemoryError_(
            f"surface {surface!r} is not enabled in this plugin (enabled: {', '.join(ENABLED_SURFACES)})")
    return surface


def check_layer_key(layer: str, key: str) -> None:
    if layer not in LAYERS:
        raise MemoryError_(f"layer must be one of {', '.join(LAYERS)}")
    valid = md.SURFACES if layer == "L2" else L3_SLOTS
    if key not in valid:
        raise MemoryError_(f"{layer} key must be one of {', '.join(valid)} (got {key!r})")


def doc_path(args, layer: str, key: str) -> Path:
    check_layer_key(layer, key)
    return memory_dir(args) / layer / f"{key}.md"


def default_title(layer: str, key: str) -> str:
    if layer == "L2":
        return f"{key} memory"
    return {"recent": "Recent summary", "profile": "User profile",
            "scope": "Knowledge scope", "preferences": "Preferences"}[key]


def load_doc(path: Path, layer: str, key: str, for_write: bool = False) -> md.Document:
    """Missing file = empty document. A file with content but no title is damaged:
    an error, never an empty document that a later write would overwrite.

    ``for_write``: every edit rewrites the whole file from the parsed document, so lines the
    format does not understand (a hand-written note, an anchor-less bullet) would be erased.
    Refuse instead and point at ``doc-put``."""
    if not path.exists():
        return md.Document(title=default_title(layer, key))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MemoryError_(f"memory document {path} cannot be read: {exc}") from exc
    doc = md.parse(text)
    if text.strip() and not doc.title:
        raise MemoryError_(
            f"memory document {path} is damaged: it has no '# title' line. "
            "Fix it with doc-put (whole document) or remove the file.")
    if for_write:
        lost = md.unparsed_lines(text)
        if lost:
            raise MemoryError_(
                f"memory document {path} has {len(lost)} line(s) the memory format does not understand "
                f"(first: {lost[0][:60]!r}); editing it would erase them. Nothing was changed. "
                "Move them into the entries with doc-put (whole document), or remove them.")
    return doc


# -------------------------------------------------------------------- trace


def trace_dir(args, surface: str) -> Path:
    return memory_dir(args) / "trace" / surface


def trace_events(args, surface: str, since=None):
    """Events for one surface, oldest first; ``since`` is an ISO string (ts >= since).

    Damaged lines are skipped. Files dated before ``since`` are not opened.
    """
    base = trace_dir(args, surface)
    out = []
    if not base.is_dir():
        return out
    cutoff_date = since[:10] if since else ""
    for path in sorted(base.glob("*.jsonl")):
        if cutoff_date and path.stem < cutoff_date:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) \
                    or not isinstance(obj.get("ts"), str) or not isinstance(obj.get("kind"), str):
                continue
            if since and obj["ts"] < since:
                continue
            out.append(obj)
    return out


def write_event(args, surface: str, kind: str, payload: dict, now: float,
                session_id=None, turn_id=None):
    """Append one L1 event. Returns (event, warnings); never raises on a write failure."""
    event = {"id": md.new_trace_id(surface, now_ms=int(now * 1000)), "ts": iso_of(now),
             "surface": surface, "kind": kind, "payload": payload,
             "session_id": session_id, "turn_id": turn_id}
    day = event["ts"][:10]
    path = trace_dir(args, surface) / f"{day}.jsonl"
    warnings = []
    try:
        append_line(path, json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    except (OSError, ValueError) as exc:  # ValueError: text that cannot be encoded, e.g. a lone surrogate
        warnings.append({"problem": f"L1 event was not saved ({surface}/{kind}): {exc}"})
    return event, warnings


def read_stdin_json(stdin, what: str) -> dict:
    try:
        data = json.loads(stdin.read())
    except ValueError as exc:
        raise MemoryError_(f"stdin must be a JSON {what}: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryError_(f"stdin must be a JSON object ({what})")
    return data


def cmd_emit(args, stdin) -> dict:
    surface = check_surface(args.surface)
    data = read_stdin_json(stdin, "object {kind, payload}")
    kind = data.get("kind")
    payload = data.get("payload", {})
    if not isinstance(kind, str) or not kind.strip():
        raise MemoryError_("kind must be a non-empty string")
    if not isinstance(payload, dict):
        raise MemoryError_("payload must be an object")
    for name in ("session_id", "turn_id"):
        if data.get(name) is not None and not isinstance(data[name], str):
            raise MemoryError_(f"{name} must be a string")
    event, warnings = write_event(args, surface, kind.strip(), payload, now_of(args),
                                  data.get("session_id"), data.get("turn_id"))
    return {"event": event, "warnings": warnings}


def cmd_trace(args) -> dict:
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        found = []
        for tid in wanted:
            if ":" not in tid:
                continue
            surface = tid.split(":", 1)[0]
            if surface in md.SURFACES:
                found.extend(e for e in trace_events(args, surface) if e["id"] == tid)
        return {"events": found}
    surface = check_surface(args.surface or "", enabled_only=False)
    if args.latest:
        events = trace_events(args, surface)
        return {"surface": surface, "latest_ts": max((e["ts"] for e in events), default=None)}
    events = trace_events(args, surface, args.since)
    if args.count:
        return {"surface": surface, "count": len(events)}
    return {"surface": surface, "events": events}


# ------------------------------------------------------------------ L2 / L3


def entry_count_of(path: Path):
    """(entry count, problem). A document that cannot be read or has no title is reported, not counted as empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, f"cannot be read: {exc}"
    doc = md.parse(text)
    if text.strip() and not doc.title:
        return 0, "damaged: it has no '# title' line"
    return len(doc.all_entries()), None


def cmd_overview(args) -> dict:
    rows = []
    for layer, keys in (("L2", md.SURFACES), ("L3", L3_SLOTS)):
        for key in keys:
            path = doc_path(args, layer, key)
            if not path.exists():
                backlog = len(trace_events(args, key)) if layer == "L2" else 0
                rows.append({"layer": layer, "key": key, "exists": False, "updated_at": None,
                             "entry_count": 0, "backlog": backlog, "problem": None})
                continue
            mtime = path.stat().st_mtime
            backlog = len(trace_events(args, key, iso_of(mtime))) if layer == "L2" else 0
            count, problem = entry_count_of(path)
            rows.append({"layer": layer, "key": key, "exists": True, "updated_at": iso_of(mtime),
                         "entry_count": count, "backlog": backlog, "problem": problem})
    return {"learner": args.learner, "rows": rows}


def cmd_read_l3(args) -> dict:
    parts = []
    for slot in L3_SLOTS:
        path = doc_path(args, "L3", slot)
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8").strip()
        if body:
            parts.append(body)
    if not parts:
        return {"empty": True, "text": NO_MEMORY}
    return {"empty": False, "text": "\n\n---\n\n".join(parts) + "\n"}


def doc_json(doc: md.Document) -> dict:
    return {"title": doc.title,
            "sections": [{"name": name, "entries": [
                {"id": e.id, "text": e.text, "refs": list(e.refs)} for e in entries]}
                for name, entries in doc.sections]}


def cmd_doc_get(args) -> dict:
    path = doc_path(args, args.layer, args.key)
    doc = load_doc(path, args.layer, args.key)
    result = {"layer": args.layer, "key": args.key, "exists": path.exists(),
              "entry_count": len(doc.all_entries())}
    if args.format == "json":
        result["document"] = doc_json(doc)
    else:
        result["markdown"] = path.read_text(encoding="utf-8") if path.exists() else ""
    return result


def cmd_doc_put(args, stdin) -> dict:
    path = doc_path(args, args.layer, args.key)
    data = read_stdin_json(stdin, "object {markdown}")
    text = data.get("markdown")
    if not isinstance(text, str) or not text.strip():
        raise MemoryError_("markdown must be a non-empty string")
    doc = md.parse(text)
    if not doc.title:
        raise MemoryError_("the document needs a '# title' line; nothing was written")
    atomic_write(path, text if text.endswith("\n") else text + "\n")
    return {"layer": args.layer, "key": args.key, "entry_count": len(doc.all_entries())}


def ops_from_json(raw) -> list:
    if not isinstance(raw, list):
        raise MemoryError_("ops must be a list")
    ops = []
    for i, spec in enumerate(raw):
        where = f"ops[{i}]"
        if not isinstance(spec, dict):
            raise MemoryError_(f"{where}: must be an object")
        kind = spec.get("op")

        def need(name, types):
            value = spec.get(name)
            if not isinstance(value, types) or isinstance(value, bool):
                raise MemoryError_(f"{where}: {name} must be a {types.__name__ if isinstance(types, type) else 'list of strings'}")
            return value

        def refs(name):
            value = spec.get(name)
            if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
                raise MemoryError_(f"{where}: {name} must be a list of strings")
            return value

        if kind == "add":
            ops.append(md.AddOp(need("section", str), need("text", str), refs("refs")))
        elif kind == "edit":
            ops.append(md.EditOp(need("target_id", str), need("new_text", str), refs("new_refs")))
        elif kind == "delete":
            ops.append(md.DeleteOp(need("target_id", str), need("reason", str)))
        else:
            raise MemoryError_(f"{where}: op must be add, edit or delete (got {kind!r})")
    return ops


def cmd_ops(args, stdin) -> dict:
    path = doc_path(args, args.layer, args.key)
    data = read_stdin_json(stdin, "object {ops}")
    ops = ops_from_json(data.get("ops"))
    doc = load_doc(path, args.layer, args.key, for_write=True)
    report = md.apply(doc, ops)
    if not report.accepted:
        raise MemoryError_(f"batch rejected, nothing written: {report.reason}")
    if ops:
        atomic_write(path, md.serialize(doc))
    return {"layer": args.layer, "key": args.key, "entry_count": len(doc.all_entries()),
            "results": [{"op": r.op.op, "entry_id": r.entry_id, "detail": r.detail}
                        for r in report.results]}


def cmd_entry_delete(args) -> dict:
    path = doc_path(args, args.layer, args.key)
    if not path.exists():
        raise MemoryError_(f"{args.layer}/{args.key} does not exist; nothing to delete")
    doc = load_doc(path, args.layer, args.key, for_write=True)
    if not doc.remove(args.id):
        raise MemoryError_(f"entry {args.id} not found in {args.layer}/{args.key}; nothing changed")
    atomic_write(path, md.serialize(doc))
    return {"layer": args.layer, "key": args.key, "deleted": args.id,
            "entry_count": len(doc.all_entries())}


def norm_pref(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def cmd_preference(args, stdin) -> dict:
    data = read_stdin_json(stdin, "object {op, text, target_id?}")
    op = data.get("op")
    text = data.get("text")
    target_id = data.get("target_id")
    reason = data.get("reason")
    if op not in ("add", "edit"):
        raise MemoryError_("op must be 'add' or 'edit'")
    if not isinstance(text, str) or not text.strip():
        raise MemoryError_("text is required and must be non-empty")
    text = " ".join(text.split())  # one line: a preference never needs a line break
    if len(text) > PREF_MAX:
        raise MemoryError_(f"text must be at most {PREF_MAX} characters (got {len(text)})")
    if reason is not None and not isinstance(reason, str):
        raise MemoryError_("reason must be a string")

    path = doc_path(args, "L3", "preferences")
    doc = load_doc(path, "L3", "preferences", for_write=True)
    duplicate = None
    if op == "add":
        target = norm_pref(text)
        for entry in doc.all_entries():
            if entry.section == PREF_SECTION and norm_pref(entry.text) == target:
                duplicate = entry
                break
    else:
        if not isinstance(target_id, str) or not target_id.strip():
            raise MemoryError_("edit requires target_id")
        if doc.find(target_id.strip()) is None:
            raise MemoryError_(f"edit: target_id {target_id} not found in the preferences")
        target_id = target_id.strip()

    event, warnings = write_event(
        args, "chat", "preference_stated",
        {"op": op, "text": text, "target_id": target_id, "reason": reason}, now_of(args))
    if duplicate is not None:
        return {"status": "duplicate", "entry_id": duplicate.id, "event_id": event["id"],
                "warnings": warnings}
    if op == "add":
        report = md.apply(doc, [md.AddOp(PREF_SECTION, text, [event["id"]])])
    else:
        report = md.apply(doc, [md.EditOp(target_id, text, [event["id"]])])
    if not report.accepted:
        raise MemoryError_(f"preference rejected: {report.reason}")
    atomic_write(path, md.serialize(doc))
    entry_id = report.results[0].entry_id or target_id
    return {"status": "added" if op == "add" else "edited", "entry_id": entry_id,
            "event_id": event["id"], "warnings": warnings}


# ---------------------------------------------------------- chat entities

CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_POINTS = 8
MAX_SOURCE = 200
CHECK_RESULTS = ("correct", "partial", "incorrect")


def entities_dir(args) -> Path:
    return memory_dir(args) / "entities" / "chat"


def render_chat(question: str, kps: list, points: list, check) -> str:
    parts = ["**Question**: " + question]
    if kps:
        parts.append("**Knowledge points**: " + ", ".join(kps))
    if points:
        parts.append("**Key points**:\n" + "\n".join(f"- {p['text']} ({p['source']})" for p in points))
    if check:
        parts.append(f"**Check**: {check['kp']}: {check['result']}")
    return "\n\n".join(parts)


def parse_chat_payload(data: dict) -> dict:
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise MemoryError_("question must be a non-empty string (the learner's own words)")
    kps = data.get("kps", [])
    if not isinstance(kps, list) or not all(isinstance(k, str) and k.strip() for k in kps):
        raise MemoryError_("kps must be a list of non-empty strings")
    points = data.get("points", [])
    if not isinstance(points, list):
        raise MemoryError_("points must be a list")
    if len(points) > MAX_POINTS:
        raise MemoryError_(f"at most {MAX_POINTS} points (got {len(points)})")
    clean = []
    for i, point in enumerate(points):
        if not isinstance(point, dict) or not isinstance(point.get("text"), str) \
                or not point["text"].strip() or not isinstance(point.get("source"), str) \
                or not point["source"].strip():
            raise MemoryError_(f"points[{i}] needs a non-empty text and source")
        if len(point["text"]) > md.MAX_TEXT_LEN:
            raise MemoryError_(f"points[{i}].text is over {md.MAX_TEXT_LEN} characters")
        if len(point["source"]) > MAX_SOURCE:
            raise MemoryError_(f"points[{i}].source is over {MAX_SOURCE} characters")
        clean.append({"text": point["text"].strip(), "source": point["source"].strip()})
    check = data.get("check")
    if check is not None:
        if not isinstance(check, dict) or not isinstance(check.get("kp"), str) \
                or not check["kp"].strip() or check.get("result") not in CHECK_RESULTS:
            raise MemoryError_(f"check must be {{kp, result}} with result in {', '.join(CHECK_RESULTS)}")
        check = {"kp": check["kp"].strip(), "result": check["result"]}
    return {"question": question, "kps": [k.strip() for k in kps], "points": clean, "check": check}


def cmd_chat_put(args, stdin) -> dict:
    if not CALL_ID.fullmatch(args.call_id or ""):
        raise MemoryError_("call id may contain only letters, digits, _ and - (1-64 characters)")
    payload = parse_chat_payload(read_stdin_json(stdin, "object {question, kps, points, check}"))
    entity_id = "learn-" + args.call_id
    path = entities_dir(args) / f"{entity_id}.json"
    content = render_chat(payload["question"], payload["kps"], payload["points"], payload["check"])
    metadata = {"call_id": args.call_id, "question": payload["question"], "kps": payload["kps"],
                "points": payload["points"], "check": payload["check"]}
    existing = None
    if path.exists():
        existing = read_chat_entity(path)
        if existing["content"] == content and existing["metadata"] == metadata:
            return {"entity_id": entity_id, "created": False, "updated": False}
    entity = {"id": entity_id, "label": " ".join(payload["question"].split())[:80] or entity_id,
              "ts": iso_of(now_of(args)), "content": content, "metadata": metadata,
              "fingerprint": ms.fingerprint(payload["question"], content)}
    atomic_write(path, json.dumps(entity, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"entity_id": entity_id, "created": existing is None, "updated": existing is not None}


def read_chat_entity(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MemoryError_(f"chat entity {path} is damaged: {exc}") from exc
    keys = ("id", "label", "ts", "content", "fingerprint")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in keys) \
            or not isinstance(data.get("metadata"), dict) or not data["id"]:
        raise MemoryError_(f"chat entity {path} is damaged: missing or mistyped fields")
    return data


# ------------------------------------------------------- snapshot adapters


class Adapted:
    """Entities plus how trustworthy the read was."""

    def __init__(self, entities: list, warnings: list = None):
        self.entities = entities
        self.warnings = warnings or []  # non-empty = degraded read


def read_chat_entities(args) -> Adapted:
    base = entities_dir(args)
    out = []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            data = read_chat_entity(path)
            out.append(ms.Entity(id=data["id"], label=data["label"], ts=data["ts"],
                                 content=data["content"], metadata=data["metadata"],
                                 fingerprint=data["fingerprint"]))
    return Adapted(out)


def load_bank_questions(args, kb: str) -> dict:
    path = Path(args.root) / ".tutor" / "question_bank" / f"{kb}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        questions = data["questions"]
        if not isinstance(questions, dict):
            raise ValueError("questions is not an object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MemoryError_(f"question bank {path} cannot be read: {exc}") from exc
    return questions


def read_quiz_entities(args) -> Adapted:
    events, warnings = ll.read_events(ll.log_path(args.root, args.learner))
    skipped = [w for w in warnings if str(w.get("problem", "")).startswith("skipped")]
    banks = {}
    out = []
    for event in events:
        if event.get("type") != "attempt" or event.get("assessment_type", "quiz") not in ("quiz", "review"):
            continue
        kp = event["kp"]
        kb = kp.split("::", 1)[0] if "::" in kp else ""
        if kb and not _NAME.fullmatch(kb):  # a ledger-derived name must never build a path outside the bank folder
            skipped.append({"problem": f"ledger: knowledge-base name {kb!r} in {kp!r} is not a valid name; its question bank was not read"})
            kb = ""
        if kb and kb not in banks:
            banks[kb] = load_bank_questions(args, kb)
        qid = event.get("question_id") or ""
        bank_q = banks.get(kb, {}).get(qid) if qid else None
        bank_q = bank_q if isinstance(bank_q, dict) else {}
        question = str(bank_q.get("prompt") or event.get("question") or "").strip()
        answer = str(event.get("learner_answer") or "").strip()
        correct = str(bank_q.get("answer") or "").strip()
        explanation = str(bank_q.get("explanation") or "").strip()
        is_correct = event.get("result") == "correct"
        content = "\n\n".join(part for part in (
            f"**Question**: {question}" if question else "",
            f"**Learner answer**: {answer}" if answer else "",
            f"**Correct answer**: {correct}" if correct else "",
            f"**Explanation**: {explanation}" if explanation else "") if part)
        out.append(ms.Entity(
            id=event["id"], label=question[:80] or qid or kp, ts=iso_of(event["ts"]), content=content,
            metadata={"kp": kp, "question_id": qid, "difficulty": bank_q.get("difficulty"),
                      "result": event.get("result"), "is_correct": is_correct,
                      "assessment_type": event.get("assessment_type", "quiz")},
            fingerprint=ms.fingerprint(question, answer, correct, is_correct)))
    return Adapted(out, [w if w["problem"].startswith("ledger:") else {"problem": "ledger: " + w["problem"]}
                         for w in skipped])


def read_kb_entities(args) -> Adapted:
    base = Path(args.root) / ".tutor" / "kb"
    out = []
    if base.is_dir():
        for kb_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            db = kb_dir / "index.sqlite"
            if not db.exists():
                continue
            try:
                conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
                try:
                    rows = conn.execute(
                        "SELECT display, sha256, ingested_at FROM sources ORDER BY source_id").fetchall()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                raise MemoryError_(f"knowledge base {kb_dir.name!r} has an unreadable index: {exc}") from exc
            earliest = min((r[2] for r in rows), default=None)
            names = [str(r[0]) for r in rows][:20]
            content = f"Knowledge base: {kb_dir.name}\nSources: {len(rows)}"
            if names:
                content += "\n" + "\n".join(f"- {n}" for n in names)
            out.append(ms.Entity(
                id=kb_dir.name, label=kb_dir.name, ts=iso_of(earliest) if earliest is not None else "",
                content=content, metadata={"source_count": len(rows)},
                fingerprint=ms.fingerprint(sorted(str(r[1]) for r in rows))))
    return Adapted(out)


ADAPTERS = {"chat": read_chat_entities, "quiz": read_quiz_entities, "kb": read_kb_entities}


# ---------------------------------------------------------------- snapshot


def snapshot_dir(args, surface: str) -> Path:
    return memory_dir(args) / "snapshot" / surface


def load_state(args, surface: str) -> dict:
    """Missing = empty. Damaged = error: a refresh must not diff against a guess."""
    path = snapshot_dir(args, surface) / "state.json"
    if not path.exists():
        return {"fingerprints": {}, "labels": {}, "last_refresh": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fps, labels = data["fingerprints"], data["labels"]
        if not isinstance(fps, dict) or not isinstance(labels, dict) \
                or not all(isinstance(v, str) for v in fps.values()):
            raise ValueError("fingerprints/labels have the wrong shape")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MemoryError_(f"snapshot state {path} is damaged: {exc}") from exc
    return {"fingerprints": fps, "labels": labels, "last_refresh": data.get("last_refresh")}


def read_changes_file(args, surface: str) -> list:
    path = snapshot_dir(args, surface) / "changes.jsonl"
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def plan_refresh(args, surface: str, allow_empty: bool):
    """Read, guard and diff. Returns (changes, new_state, prev_state, warnings, adapted).

    Raises MemoryError_ for anything that must not be written."""
    try:
        adapted = ADAPTERS[surface](args)
    except MemoryError_:
        raise
    except Exception as exc:
        raise MemoryError_(f"{surface}: reading the current entities failed, nothing was changed: "
                           f"{type(exc).__name__}: {exc}") from exc
    prev = load_state(args, surface)
    curr_fp = {e.id: e.fingerprint for e in adapted.entities}
    labels = {e.id: e.label for e in adapted.entities}
    if not adapted.entities and prev["fingerprints"] and not allow_empty:
        raise MemoryError_(
            f"{surface}: the read returned no entities but the saved state has "
            f"{len(prev['fingerprints'])}. Refusing to record them all as removed; "
            "if that really happened, run again with --allow-empty. Nothing was changed.")
    changes = ms.diff_snapshots(prev["fingerprints"], curr_fp, label_map=labels,
                                prev_label_map=prev["labels"], now=iso_of(now_of(args)))
    warnings = list(adapted.warnings)
    new_fp, new_labels = dict(curr_fp), dict(labels)
    if adapted.warnings:  # degraded read: do not turn missing evidence into deletions
        kept = [c for c in changes if c.kind == "removed"]
        changes = [c for c in changes if c.kind != "removed"]
        for c in kept:
            new_fp[c.entity_id] = prev["fingerprints"][c.entity_id]
            new_labels[c.entity_id] = prev["labels"].get(c.entity_id, c.entity_id)
        if kept:
            warnings.append({"problem": f"{len(kept)} entities not seen in this degraded read were not "
                                        "recorded as removed"})
    return changes, {"fingerprints": new_fp, "labels": new_labels}, prev, warnings, adapted


def summarize(changes) -> dict:
    return {"added": sum(c.kind == "added" for c in changes),
            "removed": sum(c.kind == "removed" for c in changes),
            "modified": sum(c.kind == "modified" for c in changes),
            "changes": [c.to_dict() for c in changes]}


def cmd_snapshot(args) -> dict:
    surface = check_surface(args.surface)
    if args.action == "changes":
        limit = max(1, min(args.limit, 1000))
        rows = list(reversed(read_changes_file(args, surface)))
        return {"surface": surface, "changes": rows[max(0, args.offset):max(0, args.offset) + limit]}
    changes, new_state, prev, warnings, _ = plan_refresh(args, surface, args.allow_empty)
    result = summarize(changes)
    result.update({"surface": surface, "warnings": warnings})
    if args.action == "preview":
        result["written"] = False
        return result
    dirty = bool(changes) or new_state["labels"] != prev["labels"] \
        or new_state["fingerprints"] != prev["fingerprints"]
    if dirty:
        directory = snapshot_dir(args, surface)
        if changes:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "changes.jsonl").open("a", encoding="utf-8") as fh:
                for change in changes:
                    fh.write(json.dumps(change.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
        atomic_write(directory / "state.json", json.dumps(
            {"fingerprints": new_state["fingerprints"], "labels": new_state["labels"],
             "last_refresh": iso_of(now_of(args))}, ensure_ascii=False, indent=2) + "\n")
    result["written"] = dirty
    return result


# ------------------------------------------------------------------ recall

TraceEvent = namedtuple("TraceEvent", "kind payload ts")


def cmd_recent(args) -> dict:
    now = datetime.fromtimestamp(now_of(args), tz=timezone.utc)
    stamps, warnings = {}, []
    wanted = ms.resolve_surfaces([args.surface] if args.surface else None)
    for surface in ("chat", "quiz"):  # kb timestamps are first-index times: not "recent" (upstream recall.py:152)
        if surface not in wanted:
            continue
        try:
            entities = ADAPTERS[surface](args).entities
        except Exception as exc:
            warnings.append({"problem": f"{surface}: {type(exc).__name__}: {exc}"})
            continue
        stamps[surface] = entities  # shape_recent reads only .label and .ts
    hits = ms.shape_recent(stamps, now=now, days=args.days, limit=args.limit,
                           surfaces=[args.surface] if args.surface else None)
    return {"hits": [h.to_dict() for h in hits], "warnings": warnings}


def cmd_recent_queries(args) -> dict:
    now = datetime.fromtimestamp(now_of(args), tz=timezone.utc)
    cutoff = ms.cutoff(args.days, now)
    events = [TraceEvent(e["kind"], e.get("payload"), e["ts"])
              for e in trace_events(args, "kb", cutoff.isoformat() if cutoff else None)]
    hits = ms.shape_queries({"kb": events}, now=now, days=args.days, limit=args.limit)
    return {"hits": [h.to_dict() for h in hits]}


# --------------------------------------------------------------------- CLI


def parser() -> argparse.ArgumentParser:
    p = MemoryArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project root containing .tutor/")
    p.add_argument("--learner", default="default")
    p.add_argument("--now", type=float, default=None, help="epoch seconds (testing)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("emit")
    sp.add_argument("--surface", required=True)
    sp = sub.add_parser("trace")
    sp.add_argument("--surface")
    sp.add_argument("--since")
    sp.add_argument("--ids")
    sp.add_argument("--count", action="store_true")
    sp.add_argument("--latest", action="store_true")
    sub.add_parser("overview")
    sub.add_parser("read-l3")
    sp = sub.add_parser("doc-get")
    sp.add_argument("--layer", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--format", choices=("md", "json"), default="md")
    for name in ("doc-put", "ops"):
        sp = sub.add_parser(name)
        sp.add_argument("--layer", required=True)
        sp.add_argument("--key", required=True)
    sp = sub.add_parser("entry-delete")
    sp.add_argument("--layer", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--id", required=True)
    sub.add_parser("preference")
    sp = sub.add_parser("chat-put")
    sp.add_argument("--call-id", required=True)
    sp = sub.add_parser("snapshot")
    sp.add_argument("action", choices=("refresh", "preview", "changes"))
    sp.add_argument("--surface", required=True)
    sp.add_argument("--allow-empty", action="store_true")
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--offset", type=int, default=0)
    sp = sub.add_parser("recent")
    sp.add_argument("--days", type=int, default=ms.DEFAULT_DAYS)
    sp.add_argument("--limit", type=int, default=ms.DEFAULT_LIMIT)
    sp.add_argument("--surface")
    sp = sub.add_parser("recent-queries")
    sp.add_argument("--days", type=int, default=ms.DEFAULT_DAYS)
    sp.add_argument("--limit", type=int, default=ms.DEFAULT_LIMIT)
    return p


def emit(obj: dict) -> None:
    try:
        print(json.dumps(obj, ensure_ascii=False, allow_nan=False))
    except UnicodeEncodeError:
        print(json.dumps(obj, ensure_ascii=True, allow_nan=False))


def run(args, stdin) -> dict:
    with_stdin = {"emit": cmd_emit, "doc-put": cmd_doc_put, "ops": cmd_ops,
                  "preference": cmd_preference, "chat-put": cmd_chat_put}
    plain = {"trace": cmd_trace, "overview": cmd_overview, "read-l3": cmd_read_l3,
             "doc-get": cmd_doc_get, "entry-delete": cmd_entry_delete, "snapshot": cmd_snapshot,
             "recent": cmd_recent, "recent-queries": cmd_recent_queries}
    if args.command in with_stdin:
        return with_stdin[args.command](args, stdin)
    return plain[args.command](args)


def main(argv=None, stdin=None) -> int:
    stdin = stdin or sys.stdin
    try:
        args = parser().parse_args(argv)
        result = run(args, stdin)
    except MemoryError_ as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # keep the JSON contract even for unexpected failures
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    result["ok"] = True
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
