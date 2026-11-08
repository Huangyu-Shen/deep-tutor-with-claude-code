#!/usr/bin/env python3
"""Question bank for DeepTutor@Claude Code.

Every question that has been asked belongs to exactly one knowledge base. The
bank is configuration-like data, not evidence: it lives in
<root>/.tutor/question_bank/<kb>.json, while answers stay in the learner's
append-only ledger (see learner_log.py).

Questions keep the answer key that was fixed before asking, so later reviews
can retest the same question instead of writing a new one. A question copied
from a bank page keeps its page and quote; a question whose citation no longer
verifies is marked `citation_ok: false` and is skipped by `pick` until the
citation is restored.

Commands (JSON on stdout; failures give {"ok": false, "error": ...}, exit 1):
  add                  < questions.json   archive a batch of questions
  add-reference        < references.json  archive confirmed reference questions
  list --kb K          [filters]          browse questions, never the answer key
  get --kb K --question-id Q              one question, answer key only with
                                          --with-answers
  pick --kb K          [filters]          reusable questions for quiz/review
  invalidate --kb K --question-id Q       mark the citation as invalid
  revalidate --kb K --question-id Q       mark the citation as valid again
  references --kb K    [filters]          confirmed reference questions
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learner_log as ll  # noqa: E402

SCHEMA_VERSION = 1
FORMATS = ("multiple_choice", "fill_in_blank", "short_answer")
DIFFICULTIES = ("easy", "medium", "hard")
SOURCE_TYPES = ("quiz", "mimic")
ANSWER_STATUSES = ll.ANSWER_STATUSES
NEAR_DUPLICATE_THRESHOLD = 0.85
ANSWER_FIELDS = ("answer", "explanation", "checklist")
_KB_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class BankError(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class BankArgumentParser(argparse.ArgumentParser):
    """Usage errors are failures too: JSON on stdout, exit 1."""

    def error(self, message: str):
        raise BankError(f"usage error: {message}")


# ------------------------------------------------------------------- storage


def check_kb(name: str) -> str:
    if not _KB_NAME.fullmatch(name or "") or set(name) == {"."}:
        raise BankError("kb name may contain only letters, digits, _ . - and must not start with a dot")
    return name


def bank_path(root: str, kb: str) -> Path:
    return Path(root) / ".tutor" / "question_bank" / f"{check_kb(kb)}.json"


def empty_bank(kb: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "kb": check_kb(kb),
            "questions": {}, "references": {}}


def load_bank(root: str, kb: str) -> dict:
    """Load one bank. A missing file is empty; damage is an error, never empty."""
    path = bank_path(root, kb)
    if not path.exists():
        return empty_bank(kb)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BankError(f"question bank {path} is damaged: {exc}") from exc
    if not isinstance(data, dict):
        raise BankError(f"question bank {path} is damaged: root must be an object")
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise BankError(f"question bank {path} uses unsupported schema_version {version!r}")
    if data.get("kb") != check_kb(kb):
        raise BankError(f"question bank {path} is not for {kb!r}")
    for key in ("questions", "references"):
        if not isinstance(data.get(key), dict):
            raise BankError(f"question bank {path} is damaged: {key} must be an object")
    _check_stored(data, path)
    return data


QUESTION_KEYS = ("question_id", "kp", "knowledge_type", "format", "difficulty",
                 "source_type", "answer_status", "prompt")
REFERENCE_KEYS = ("reference_id", "kp", "format", "difficulty", "prompt")


def _check_stored(bank: dict, path: Path) -> None:
    """Reject a damaged entry at read time instead of returning a half question."""
    for qid, question in bank["questions"].items():
        if not isinstance(question, dict) or any(
                not isinstance(question.get(key), str) or not question[key].strip()
                for key in QUESTION_KEYS):
            raise BankError(f"question bank {path} is damaged: question {qid!r} is incomplete")
        if question.get("question_id") != qid:
            raise BankError(f"question bank {path} is damaged: question key {qid!r} does not match its id")
        if not isinstance(question.get("source"), dict):
            raise BankError(f"question bank {path} is damaged: question {qid!r} has no source object")
        if question.get("options") is not None and not isinstance(question["options"], list):
            raise BankError(f"question bank {path} is damaged: question {qid!r} has invalid options")
        if not isinstance(question.get("citation_ok"), bool):
            raise BankError(f"question bank {path} is damaged: question {qid!r} has an invalid citation_ok")
        for key in ("invalidated_at", "added_at"):
            if question.get(key) is not None and not isinstance(question[key], str):
                raise BankError(f"question bank {path} is damaged: question {qid!r} has an invalid {key}")
    for rid, reference in bank["references"].items():
        if not isinstance(reference, dict) or any(
                not isinstance(reference.get(key), str) or not reference[key].strip()
                for key in REFERENCE_KEYS):
            raise BankError(f"question bank {path} is damaged: reference {rid!r} is incomplete")
        if reference.get("reference_id") != rid:
            raise BankError(f"question bank {path} is damaged: reference key {rid!r} does not match its id")
        if not isinstance(reference.get("source"), dict):
            raise BankError(f"question bank {path} is damaged: reference {rid!r} has no source object")


def _write_temp(store: dict, root: str, kb: str) -> Path:
    target = bank_path(root, kb)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def write_bank(store: dict, root: str, kb: str) -> None:
    tmp = _write_temp(store, root, kb)
    try:
        os.replace(tmp, bank_path(root, kb))
    finally:
        if tmp.exists():
            tmp.unlink()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# -------------------------------------------------------------- validation


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BankError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _source(value) -> dict:
    if not isinstance(value, dict):
        raise BankError("source must be an object with source_id, page and quote")
    source = {
        "source_id": _text(value.get("source_id"), "source.source_id"),
        "page": value.get("page"),
        "quote": _text(value.get("quote"), "source.quote"),
    }
    if not isinstance(source["page"], int) or isinstance(source["page"], bool) or source["page"] < 1:
        raise BankError("source.page must be a positive integer")
    display = value.get("display")
    source["display"] = _text(display, "source.display") if display is not None else source["source_id"]
    return source


def _options(value, fmt: str) -> list[str] | None:
    if value is None:
        if fmt == "multiple_choice":
            raise BankError("options are required for multiple_choice")
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise BankError("options must be a list of non-empty strings")
    options = [x.strip() for x in value]
    if fmt == "multiple_choice" and len(options) < 2:
        raise BankError("multiple_choice needs at least two options")
    return options


def check_kp(kb: str, kp: str) -> str:
    """Require a knowledge point id from this base, spelled the way the ledger spells it."""
    prefix = kb + "::"
    if not kp.startswith(prefix):
        raise BankError(f"kp {kp!r} does not belong to {kb!r}")
    try:
        canonical = ll.kp_id(kb, kp[len(prefix):])
    except ll.LedgerError as exc:
        raise BankError(str(exc)) from exc
    if canonical != kp:
        raise BankError(f"kp {kp!r} is not normalized; use {canonical!r}")
    return kp


def validate_question(raw, references: dict, kb: str) -> dict:
    if not isinstance(raw, dict):
        raise BankError("each question must be an object")
    q = {
        "question_id": _text(raw.get("question_id"), "question_id"),
        "kp": _text(raw.get("kp"), "kp"),
        "knowledge_type": raw.get("knowledge_type"),
        "format": raw.get("format"),
        "difficulty": raw.get("difficulty"),
        "source_type": raw.get("source_type"),
        "answer_status": raw.get("answer_status"),
        "prompt": _text(raw.get("prompt"), "prompt"),
        "source": _source(raw.get("source")),
    }
    if "::" not in q["kp"]:
        raise BankError(f"kp {q['kp']!r} must be a knowledge point id (<kb>::<name>)")
    check_kp(kb, q["kp"])
    if q["knowledge_type"] not in ll.KNOWLEDGE_TYPES:
        raise BankError(f"knowledge_type must be one of {ll.KNOWLEDGE_TYPES}")
    if q["format"] not in FORMATS:
        raise BankError(f"format must be one of {FORMATS}")
    if q["difficulty"] not in DIFFICULTIES:
        raise BankError(f"difficulty must be one of {DIFFICULTIES}")
    if q["source_type"] not in SOURCE_TYPES:
        raise BankError(f"source_type must be one of {SOURCE_TYPES}")
    if q["answer_status"] not in ANSWER_STATUSES:
        raise BankError(f"answer_status must be one of {ANSWER_STATUSES}")
    if q["source_type"] == "quiz" and q["answer_status"] != "sourced":
        raise BankError("an ordinary quiz question must be answer_status='sourced'")
    q["options"] = _options(raw.get("options"), q["format"])
    q["answer"] = _optional_text(raw.get("answer"), "answer")
    q["explanation"] = _optional_text(raw.get("explanation"), "explanation")
    checklist = raw.get("checklist")
    if checklist is not None:
        if not isinstance(checklist, list) or not all(isinstance(x, str) and x.strip() for x in checklist):
            raise BankError("checklist must be a list of non-empty strings")
        checklist = [x.strip() for x in checklist] or None
    q["checklist"] = checklist
    if q["format"] == "short_answer":
        if not q["answer"] and not q["checklist"]:
            raise BankError("a short_answer question needs an answer or a checklist")
    elif not q["answer"]:
        raise BankError("answer must be a non-empty string")
    reference_id = raw.get("reference_id")
    if q["source_type"] == "mimic":
        reference_id = _text(reference_id, "reference_id")
        if reference_id not in references:
            raise BankError(f"mimic question refers to unknown reference {reference_id!r}")
        reference = references[reference_id]
        if reference["kp"] != q["kp"]:
            raise BankError(f"reference {reference_id!r} belongs to {reference['kp']!r}, not {q['kp']!r}")
        if reference["format"] != q["format"] or reference["difficulty"] != q["difficulty"]:
            raise BankError("a mimic question must keep the reference's format and difficulty")
    elif reference_id is not None:
        raise BankError("reference_id only applies to source_type='mimic'")
    q["reference_id"] = reference_id
    return q


def _payload(question: dict) -> dict:
    """Fields fixed when the question was asked; server-managed state excluded."""
    fields = ("question_id", "kp", "knowledge_type", "format", "difficulty", "source_type",
              "answer_status", "prompt", "options", "answer", "explanation", "checklist",
              "source", "reference_id")
    return {key: question.get(key) for key in fields}


def normalize_prompt(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in normalized
                   if not ch.isspace() and unicodedata.category(ch)[0] != "P")


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(left: str, right: str) -> float:
    a, b = _bigrams(normalize_prompt(left)), _bigrams(normalize_prompt(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_duplicates(question: dict, questions: dict) -> tuple[str | None, list[dict]]:
    """Return (exact id, near duplicates) against the bank and the current batch."""
    prompt = normalize_prompt(question["prompt"])
    exact, near = None, []
    for other in questions.values():
        if normalize_prompt(other.get("prompt") or "") == prompt:
            exact = exact or other["question_id"]
            continue
        score = similarity(question["prompt"], other.get("prompt") or "")
        if score >= NEAR_DUPLICATE_THRESHOLD:
            near.append({"question_id": other["question_id"], "similarity": round(score, 4)})
    near.sort(key=lambda item: (-item["similarity"], item["question_id"]))
    return exact, near


# ------------------------------------------------------------------ commands


def cmd_add(args, stdin, root: str) -> dict:
    payload = _read_json(stdin, "questions", allow_list=True)
    if isinstance(payload, list):
        payload = {"questions": payload}
    allow_near = payload.get("allow_near_duplicate", False)
    if not isinstance(allow_near, bool):
        raise BankError("allow_near_duplicate must be true or false")
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise BankError("questions must be a non-empty list")
    bank = load_bank(root, args.kb)
    questions = bank["questions"]
    batch: dict[str, dict] = {}
    added, existing, near_duplicates, rejected = [], [], [], []
    for raw in raw_questions:
        question = validate_question(raw, bank["references"], args.kb)
        exact, near = find_duplicates(question, {**questions, **batch})
        if exact:
            previous = {**questions, **batch}[exact]
            if exact == question["question_id"] and _payload(previous) == _payload(question):
                existing.append(exact)
                continue
            if exact == question["question_id"]:
                raise BankError(f"question {question['question_id']!r} already exists with different content")
            rejected.append({"question_id": question["question_id"],
                             "reason": "exact_duplicate",
                             "matches": [{"question_id": exact, "similarity": 1.0}]})
            continue
        if near and not allow_near:
            near_duplicates.append({"question_id": question["question_id"], "matches": near})
            continue
        previous = questions.get(question["question_id"])
        if previous is not None:
            if _payload(previous) == _payload(question):
                existing.append(question["question_id"])
                continue
            raise BankError(f"question {question['question_id']!r} already exists with different content")
        if question["question_id"] in batch:
            raise BankError(f"duplicate question id {question['question_id']!r} in one batch")
        question = dict(question, citation_ok=True, invalidated_at=None, added_at=now_iso())
        batch[question["question_id"]] = question
        added.append(question["question_id"])
    if batch:
        questions.update(batch)
        write_bank(bank, root, args.kb)
    return {"kb": args.kb, "added": added, "existing": existing,
            "near_duplicates": near_duplicates, "rejected": rejected,
            "questions": [_public(q) for q in batch.values()]}


def _without_confirmation(reference: dict) -> dict:
    return {key: value for key, value in reference.items() if key != "confirmed_at"}


def cmd_add_reference(args, stdin, root: str) -> dict:
    payload = _read_json(stdin, "references")
    raw_references = payload.get("references")
    if not isinstance(raw_references, list) or not raw_references:
        raise BankError("references must be a non-empty list")
    bank = load_bank(root, args.kb)
    added, existing = [], []
    for raw in raw_references:
        if not isinstance(raw, dict):
            raise BankError("each reference must be an object")
        reference = {
            "reference_id": _text(raw.get("reference_id"), "reference_id"),
            "kp": _text(raw.get("kp"), "kp"),
            "format": raw.get("format"),
            "difficulty": raw.get("difficulty"),
            "prompt": _text(raw.get("prompt"), "prompt"),
            "source": _source(raw.get("source")),
            "confirmed_at": _optional_text(raw.get("confirmed_at"), "confirmed_at") or now_iso(),
        }
        if not reference["kp"].startswith(args.kb + "::"):
            raise BankError(f"kp {reference['kp']!r} does not belong to {args.kb!r}")
        check_kp(args.kb, reference["kp"])
        if reference["format"] not in FORMATS:
            raise BankError(f"format must be one of {FORMATS}")
        if reference["difficulty"] not in DIFFICULTIES:
            raise BankError(f"difficulty must be one of {DIFFICULTIES}")
        previous = bank["references"].get(reference["reference_id"])
        if previous is not None:
            if _without_confirmation(previous) != _without_confirmation(reference):
                raise BankError(f"reference {reference['reference_id']!r} already exists with different content")
            existing.append(reference["reference_id"])
            continue
        bank["references"][reference["reference_id"]] = reference
        added.append(reference["reference_id"])
    write_bank(bank, root, args.kb)
    return {"kb": args.kb, "added": added, "existing": existing,
            "references": [bank["references"][rid] for rid in added]}


def _read_json(stdin, label: str, allow_list: bool = False):
    try:
        payload = json.loads(stdin.read())
    except (ValueError, OSError) as exc:
        raise BankError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, (dict, list)) or (isinstance(payload, list) and not allow_list):
        raise BankError(f"{label} payload must be an object")
    return payload


def _public(question: dict, with_answers: bool = False) -> dict:
    item = {key: value for key, value in question.items() if key not in ANSWER_FIELDS}
    if with_answers:
        for key in ANSWER_FIELDS:
            item[key] = question.get(key)
    return item


def _filtered(questions: dict, args) -> list[dict]:
    items = []
    for question in questions.values():
        if args.kp and question["kp"] != args.kp:
            continue
        if args.format and question["format"] != args.format:
            continue
        if args.difficulty and question["difficulty"] != args.difficulty:
            continue
        if args.source_type and question["source_type"] != args.source_type:
            continue
        if args.answer_status and question["answer_status"] != args.answer_status:
            continue
        items.append(question)
    return sorted(items, key=lambda q: (q.get("added_at", ""), q["question_id"]))


def _add_filters(parser) -> None:
    parser.add_argument("--kp")
    parser.add_argument("--format", choices=FORMATS)
    parser.add_argument("--difficulty", choices=DIFFICULTIES)
    parser.add_argument("--source-type", choices=SOURCE_TYPES)
    parser.add_argument("--answer-status", choices=ANSWER_STATUSES)


def cmd_list(args) -> dict:
    bank = load_bank(args.root, args.kb)
    items = _filtered(bank["questions"], args)
    return {"kb": args.kb, "count": len(items),
            "questions": [_public(q, args.with_answers) for q in items]}


def cmd_get(args) -> dict:
    bank = load_bank(args.root, args.kb)
    question = bank["questions"].get(args.question_id)
    if question is None:
        raise BankError(f"unknown question {args.question_id!r}")
    return {"kb": args.kb, "question": _public(question, args.with_answers)}


def cmd_pick(args) -> dict:
    bank = load_bank(args.root, args.kb)
    excluded = set(args.exclude_question_id or [])
    items = [q for q in _filtered(bank["questions"], args)
             if q.get("citation_ok") is True and q["question_id"] not in excluded]
    limit = args.limit if args.limit is not None else 1
    if limit < 0:
        raise BankError("limit must be non-negative")
    return {"kb": args.kb, "count": len(items[:limit]),
            "questions": [_public(q, args.with_answers) for q in items[:limit]]}


def _set_citation(args, root: str, ok: bool) -> dict:
    bank = load_bank(root, args.kb)
    question = bank["questions"].get(args.question_id)
    if question is None:
        raise BankError(f"unknown question {args.question_id!r}")
    question["citation_ok"] = ok
    question["invalidated_at"] = None if ok else (question.get("invalidated_at") or now_iso())
    write_bank(bank, root, args.kb)
    return {"kb": args.kb, "question_id": args.question_id,
            "citation_ok": question["citation_ok"], "invalidated_at": question["invalidated_at"]}


def cmd_invalidate(args) -> dict:
    return _set_citation(args, args.root, False)


def cmd_revalidate(args) -> dict:
    return _set_citation(args, args.root, True)


def cmd_references(args) -> dict:
    bank = load_bank(args.root, args.kb)
    items = []
    for reference in bank["references"].values():
        if args.kp and reference["kp"] != args.kp:
            continue
        if args.format and reference["format"] != args.format:
            continue
        if args.difficulty and reference["difficulty"] != args.difficulty:
            continue
        items.append(reference)
    items.sort(key=lambda r: (r.get("confirmed_at", ""), r["reference_id"]))
    return {"kb": args.kb, "count": len(items), "references": items}


# --------------------------------------------------------------------- CLI


def parser() -> argparse.ArgumentParser:
    p = BankArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project root containing .tutor/")
    sub = p.add_subparsers(dest="command", required=True)

    def with_kb(sp):
        sp.add_argument("--kb", required=True)
        return sp

    with_kb(sub.add_parser("add"))
    with_kb(sub.add_parser("add-reference"))

    listing = with_kb(sub.add_parser("list"))
    _add_filters(listing)
    listing.add_argument("--with-answers", action="store_true")

    get = with_kb(sub.add_parser("get"))
    get.add_argument("--question-id", required=True)
    get.add_argument("--with-answers", action="store_true")

    pick = with_kb(sub.add_parser("pick"))
    _add_filters(pick)
    pick.add_argument("--limit", type=int)
    pick.add_argument("--exclude-question-id", action="append")
    pick.add_argument("--with-answers", action="store_true")

    invalidate = with_kb(sub.add_parser("invalidate"))
    invalidate.add_argument("--question-id", required=True)

    revalidate = with_kb(sub.add_parser("revalidate"))
    revalidate.add_argument("--question-id", required=True)

    references = with_kb(sub.add_parser("references"))
    references.add_argument("--kp")
    references.add_argument("--format", choices=FORMATS)
    references.add_argument("--difficulty", choices=DIFFICULTIES)
    return p


def emit(obj: dict) -> None:
    try:
        print(json.dumps(obj, ensure_ascii=False, allow_nan=False))
    except UnicodeEncodeError:
        print(json.dumps(obj, ensure_ascii=True, allow_nan=False))


def main(argv=None, stdin=None) -> int:
    stdin = stdin or sys.stdin
    try:
        args = parser().parse_args(argv)
        if args.command in ("add", "add-reference"):
            result = {"add": cmd_add, "add-reference": cmd_add_reference}[args.command](
                args, stdin, args.root)
        else:
            result = {"list": cmd_list, "get": cmd_get, "pick": cmd_pick,
                      "invalidate": cmd_invalidate, "revalidate": cmd_revalidate,
                      "references": cmd_references}[args.command](args)
    except BankError as exc:
        emit({"ok": False, "error": str(exc), **exc.details})
        return 1
    except ll.LedgerError as exc:
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
