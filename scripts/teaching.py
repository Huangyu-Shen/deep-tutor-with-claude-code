#!/usr/bin/env python3
"""Teaching profile for DeepTutor@Claude Code.

One small JSON file per knowledge base and learner, at
<root>/.tutor/teaching/<kb>/<learner>.json. It keeps what the learner said about
how they want to be taught: explicit preferences ("use more examples") and
per-knowledge-point observations (an explanation strategy that helped or did not,
with the learner's own words). It never infers understanding and never touches
mastery, gates or the review schedule; those stay in the ledger (learner_log.py).

Every entry carries `evidence`: a verbatim quote from the learner's current
message. The script checks that the quote appears in the message it is handed.
That message comes from the caller, so this enforces the quoting format; it
cannot prove the learner really wrote it.

Commands (JSON on stdout; failures give {"ok": false, "error": ...}, exit 1):
  show   --kb K [--kp ID]   preferences, plus the latest 3 observations for one
                            knowledge point; read before explaining
  list   --kb K             every preference and observation with its key, for
                            the learner to review or name an entry to forget
  update --kb K  < batch    all-or-nothing batch of upserts and removals; the
                            batch is {"learner_message": "...", "updates": [...]}
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learner_log as ll  # noqa: E402

SCHEMA_VERSION = 1
OUTCOMES = ("helped", "not_helped")
KINDS = ("preference", "observation")
OPS = ("upsert", "remove")
MAX_PREFERENCES = 6
MAX_OBSERVATIONS = 12
FOCUS_LIMIT = 3
TEXT_MAX = 400
STRATEGY_MAX = 300
EVIDENCE_MAX = 300
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")

UPSERT_FIELDS = {
    "preference": {"kind", "op", "key", "text", "evidence"},
    "observation": {"kind", "op", "key", "kp", "strategy", "outcome", "evidence"},
}
REMOVE_FIELDS = {"kind", "op", "key", "evidence"}


class TeachingError(Exception):
    pass


class TeachingArgumentParser(argparse.ArgumentParser):
    """Usage errors are failures too: JSON on stdout, exit 1."""

    def error(self, message: str):
        raise TeachingError(f"usage error: {message}")


# ------------------------------------------------------------------- storage


def check_name(value: str, label: str) -> str:
    if not _NAME.fullmatch(value or "") or set(value) == {"."}:
        raise TeachingError(
            f"{label} may contain only letters, digits, _ . - and must not start with a dot")
    return value


def store_path(root: str, kb: str, learner: str) -> Path:
    return (Path(root) / ".tutor" / "teaching" / check_name(kb, "kb name")
            / f"{check_name(learner, 'learner name')}.json")


def empty_store(kb: str, learner: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "kb": kb, "learner": learner,
            "preferences": {}, "observations": {}}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text_ok(value, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _check_stored(store: dict, path: Path) -> None:
    def damaged(why: str):
        raise TeachingError(f"teaching profile {path} is damaged: {why}")

    for key, entry in store["preferences"].items():
        if not _KEY.fullmatch(key):
            damaged(f"preference key {key!r} is invalid")
        if not isinstance(entry, dict) or not _text_ok(entry.get("text"), TEXT_MAX) \
                or not _text_ok(entry.get("evidence"), EVIDENCE_MAX) \
                or not _is_number(entry.get("updated_at")):
            damaged(f"preference {key!r} is incomplete")
    for key, entry in store["observations"].items():
        if not _KEY.fullmatch(key):
            damaged(f"observation key {key!r} is invalid")
        if not isinstance(entry, dict) or not _text_ok(entry.get("kp"), 200) \
                or not _text_ok(entry.get("strategy"), STRATEGY_MAX) \
                or entry.get("outcome") not in OUTCOMES \
                or not _text_ok(entry.get("evidence"), EVIDENCE_MAX) \
                or not _is_number(entry.get("updated_at")):
            damaged(f"observation {key!r} is incomplete")


def load_store(root: str, kb: str, learner: str) -> dict:
    """Load one profile. A missing file is empty; damage is an error, never empty."""
    path = store_path(root, kb, learner)
    if not path.exists():
        return empty_store(kb, learner)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TeachingError(f"teaching profile {path} is damaged: {exc}") from exc
    if not isinstance(data, dict):
        raise TeachingError(f"teaching profile {path} is damaged: root must be an object")
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise TeachingError(f"teaching profile {path} uses unsupported schema_version {version!r}")
    if data.get("kb") != kb or data.get("learner") != learner:
        raise TeachingError(f"teaching profile {path} is not for {kb!r} / {learner!r}")
    for key in ("preferences", "observations"):
        if not isinstance(data.get(key), dict):
            raise TeachingError(f"teaching profile {path} is damaged: {key} must be an object")
    _check_stored(data, path)
    return data


def write_store(store: dict, root: str, kb: str, learner: str) -> None:
    target = store_path(root, kb, learner)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


# ------------------------------------------------------------------- reading


def _ordered(entries: dict) -> list[tuple[str, dict]]:
    return sorted(entries.items(), key=lambda kv: (-kv[1]["updated_at"], kv[0]))


def cmd_show(args) -> dict:
    store = load_store(args.root, args.kb, args.learner)
    observations = []
    if args.kp:
        _require_canonical_kp(args.kb, args.kp)
        for key, entry in _ordered(store["observations"]):
            if entry["kp"] == args.kp:
                observations.append({"key": key, "kp": entry["kp"],
                                     "strategy": entry["strategy"],
                                     "outcome": entry["outcome"],
                                     "evidence": entry["evidence"]})
        observations = observations[:FOCUS_LIMIT]
    return {"kb": args.kb, "learner": args.learner,
            "teaching_preferences": {key: {"text": e["text"], "evidence": e["evidence"]}
                                     for key, e in _ordered(store["preferences"])},
            "teaching_observations": observations}


def cmd_list(args) -> dict:
    store = load_store(args.root, args.kb, args.learner)
    return {"kb": args.kb, "learner": args.learner,
            "preferences": dict(_ordered(store["preferences"])),
            "observations": dict(_ordered(store["observations"])),
            "preference_count": len(store["preferences"]),
            "observation_count": len(store["observations"])}


# ------------------------------------------------------------------ updating


def _read_batch(stdin) -> dict:
    try:
        payload = json.loads(stdin.read())
    except (ValueError, OSError) as exc:
        raise TeachingError(f"update payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TeachingError("update payload must be an object")
    message = payload.get("learner_message")
    if not isinstance(message, str) or not message.strip():
        raise TeachingError("learner_message must be the learner's current message, non-empty")
    updates = payload.get("updates")
    if not isinstance(updates, list) or not updates:
        raise TeachingError("updates must be a non-empty list")
    return payload


def _field(item: dict, name: str, limit: int | None = None) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise TeachingError(f"{name} must be a non-empty string")
    value = value.strip()
    if limit is not None and len(value) > limit:
        raise TeachingError(f"{name} must be at most {limit} characters")
    return value


def _registered_kps(root: str, learner: str) -> set[str]:
    events, _ = ll.read_events(ll.log_path(root, learner))
    return {e["kp"] for e in events if e.get("type") == "kp_define"}


def _require_canonical_kp(kb: str, kp: str) -> None:
    prefix = kb + "::"
    if not kp.startswith(prefix):
        raise TeachingError(f"kp {kp!r} does not belong to {kb!r}")
    try:
        canonical = ll.kp_id(kb, kp[len(prefix):])
    except ll.LedgerError as exc:
        raise TeachingError(str(exc)) from exc
    if canonical != kp:
        raise TeachingError(f"kp {kp!r} is not normalized; use {canonical!r}")


def _check_kp(kb: str, kp: str, defined: set[str]) -> None:
    _require_canonical_kp(kb, kp)
    if kp not in defined:
        raise TeachingError(
            f"kp {kp!r} is not registered in the ledger: register it with a kp_define "
            "event first, then record the observation")


def _parse_item(raw, message: str) -> dict:
    if not isinstance(raw, dict):
        raise TeachingError("each update must be an object")
    kind, op = raw.get("kind"), raw.get("op")
    if kind not in KINDS:
        raise TeachingError(f"kind must be one of {KINDS}")
    if op not in OPS:
        raise TeachingError(f"op must be one of {OPS}")
    allowed = REMOVE_FIELDS if op == "remove" else UPSERT_FIELDS[kind]
    extra = sorted(set(raw) - allowed)
    if extra:
        raise TeachingError(f"{kind} {op} does not accept: {', '.join(extra)}")
    key = raw.get("key")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise TeachingError("key must be 1-64 characters of letters, digits, _ or -")
    item = {"kind": kind, "op": op, "key": key,
            "evidence": _field(raw, "evidence", EVIDENCE_MAX)}
    if item["evidence"] not in message:
        raise TeachingError(
            f"evidence for {kind} {key!r} is not a verbatim quote from the learner's message")
    if op == "remove":
        return item
    if kind == "preference":
        item["text"] = _field(raw, "text", TEXT_MAX)
        return item
    item["kp"] = _field(raw, "kp", 200)
    item["strategy"] = _field(raw, "strategy", STRATEGY_MAX)
    outcome = raw.get("outcome")
    if outcome not in OUTCOMES:
        raise TeachingError(f"outcome must be one of {OUTCOMES}")
    item["outcome"] = outcome
    return item


def _content(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("kind", "op", "key")}


def cmd_update(args, stdin) -> dict:
    payload = _read_batch(stdin)
    message = payload["learner_message"]
    items = [_parse_item(raw, message) for raw in payload["updates"]]
    seen = set()
    for item in items:
        slot = (item["kind"], item["key"])
        if slot in seen:
            raise TeachingError(f"{item['kind']} key {item['key']!r} appears twice in one batch")
        seen.add(slot)
    store = load_store(args.root, args.kb, args.learner)
    observations = [i for i in items if i["kind"] == "observation" and i["op"] == "upsert"]
    if observations:
        defined = _registered_kps(args.root, args.learner)
        for item in observations:
            _check_kp(args.kb, item["kp"], defined)
    now = args.now if args.now is not None else time.time()
    recorded, unchanged, removed = [], [], []
    for item in items:
        target = store["preferences" if item["kind"] == "preference" else "observations"]
        ref = {"kind": item["kind"], "key": item["key"]}
        old = target.get(item["key"])
        if item["op"] == "remove":
            if old is None:
                raise TeachingError(f"no {item['kind']} with key {item['key']!r} to remove")
            del target[item["key"]]
            removed.append(ref)
            continue
        if item["kind"] == "observation" and old is not None and old["kp"] != item["kp"]:
            raise TeachingError(
                f"observation key {item['key']!r} belongs to {old['kp']!r}, not {item['kp']!r}")
        content = _content(item)
        if old is not None and {k: v for k, v in old.items() if k != "updated_at"} == content:
            unchanged.append(ref)
            continue
        target[item["key"]] = {**content, "updated_at": now}
        recorded.append(ref)
    if len(store["preferences"]) > MAX_PREFERENCES:
        raise TeachingError(
            f"at most {MAX_PREFERENCES} preferences: remove an obsolete one before adding another")
    if len(store["observations"]) > MAX_OBSERVATIONS:
        raise TeachingError(
            f"at most {MAX_OBSERVATIONS} observations: remove an obsolete one before adding another")
    if recorded or removed:
        write_store(store, args.root, args.kb, args.learner)
    return {"kb": args.kb, "learner": args.learner, "recorded": recorded,
            "unchanged": unchanged, "removed": removed}


# --------------------------------------------------------------------- CLI


def parser() -> argparse.ArgumentParser:
    p = TeachingArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project root containing .tutor/")
    p.add_argument("--learner", default="default")
    p.add_argument("--now", type=float, default=None, help="epoch seconds (testing)")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("show", "list", "update"):
        sp = sub.add_parser(name)
        sp.add_argument("--kb", required=True)
        if name == "show":
            sp.add_argument("--kp")
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
        if args.command == "update":
            result = cmd_update(args, stdin)
        else:
            result = {"show": cmd_show, "list": cmd_list}[args.command](args)
    except (TeachingError, ll.LedgerError) as exc:
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
