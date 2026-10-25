"""Learning paths for a knowledge base: stored definitions and the JSON CLI.

A path is configuration, not evidence: it lives in
<root>/.tutor/path/<kb>/paths.json, while answers stay in the learner's
append-only ledger (see learner_log.py). Module states are never stored; they
are replayed from those two inputs by `status`.

Knowledge points are identified exactly as in the ledger: `<kb>::<name>`
(normalized). A path must cite real, non-empty pages of an ingested knowledge
base, and saving a path records the missing `kp_define` events so attempts can
refer to them later.

Commands (JSON on stdout; failures give {"ok": false, "error": ...}, exit 1):
  list --kb K                        paths and the active pointer
  show --kb K [--path P]             one definition, with evidence
  validate --kb K < outline.json     check an outline without writing
  save --kb K [--activate] < outline.json
                                     create or overwrite a definition
  activate --kb K --path P           set the active pointer
  deactivate --kb K                  clear the active pointer
  rename --kb K --path P --name N    rename a path (its id never changes)
  delete --kb K --path P             remove a definition (evidence is kept)
  status --kb K [--path P | --all]   module states, gaps and the next action
  resolve --kb K (--module M | --kp N)
                                     may learn/quiz touch this scope now?
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))

import learner_log as ll  # noqa: E402
from mastery import compute_mastery  # noqa: E402

SCHEMA_VERSION = 1
GATE_THRESHOLD = 0.9
GATE_UNAIDED = 3
NUMERIC_TYPES = ("memory", "procedure")
QUALITATIVE_TYPES = ("concept", "design")
GATE_ORIGINS = (None, "learn", "quiz", "review", "exemption")  # diagnostic never opens a gate
_KB_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class PathError(Exception):
    pass


class PathArgumentParser(argparse.ArgumentParser):
    """Usage errors are failures too: JSON on stdout, exit 1 (KTD7)."""

    def error(self, message: str):
        raise PathError(f"usage error: {message}")


# ------------------------------------------------------------------- storage


def check_kb(name: str) -> str:
    if not _KB_NAME.fullmatch(name or "") or set(name) == {"."}:
        raise PathError("kb name may contain only letters, digits, _ . - and must not start with a dot")
    return name


def kb_dir(root: str, kb: str) -> Path:
    return Path(root) / ".tutor" / "kb" / check_kb(kb)


def store_path(root: str, kb: str) -> Path:
    return Path(root) / ".tutor" / "path" / check_kb(kb) / "paths.json"


def connect_index(root: str, kb: str) -> sqlite3.Connection:
    db = kb_dir(root, kb) / "index.sqlite"
    if not db.exists():
        raise PathError(f"knowledge base {kb!r} does not exist (run kb.py ingest first)")
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT source_id FROM sources LIMIT 1").fetchone()
        conn.execute("SELECT source_id FROM pages LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise PathError(f"knowledge base {kb!r} has an unreadable index; run kb.py ingest again: {exc}") from exc
    return conn


def require_kb(root: str, kb: str) -> None:
    """Every command that touches a path store requires an ingested knowledge base."""
    connect_index(root, kb).close()


def new_store(kb: str) -> dict:
    return {"schema": SCHEMA_VERSION, "kb": kb, "active_path_id": None, "paths": []}


def load_store(root: str, kb: str, tolerate_dangling: bool = False) -> dict:
    """Read paths.json. A damaged file is an error, never an empty path list.

    A dangling active pointer is an error too, except for `deactivate`, which is the
    command that repairs it (`tolerate_dangling=True`).
    """
    path = store_path(root, kb)
    if not path.exists():
        return new_store(kb)
    try:
        data = ll.loads_strict(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PathError(f"path file {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise PathError(f"path file {path} has an unsupported format")
    if data.get("kb") != check_kb(kb):
        raise PathError(f"path file {path} belongs to another knowledge base")
    if not isinstance(data.get("paths"), list):
        raise PathError(f"path file {path} is damaged: paths is not a list")
    active = data.get("active_path_id")
    if active is not None and (not isinstance(active, str) or not active):
        raise PathError(f"path file {path} is damaged: active_path_id is not null or a path id")
    ids = set()
    for item in data["paths"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise PathError(f"path file {path} is damaged: a path has no id")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise PathError(f"path file {path} is damaged: path {item['id']} has no name")
        if not isinstance(item.get("modules"), list):
            raise PathError(f"path file {path} is damaged: path {item['id']} has no modules")
        if item["id"] in ids:
            raise PathError(f"path file {path} is damaged: duplicate path id {item['id']}")
        ids.add(item["id"])
        _check_structure(f"path file {path}", item)
    if active is not None and active not in ids:
        if tolerate_dangling:
            data["active_path_id"] = None
        else:
            raise PathError(f"active path {active!r} is missing from {path}; "
                            f"run `path.py deactivate --kb {kb}` to clear the pointer, "
                            "or restore the path definition")
    return data


def _check_structure(location: str, item: dict) -> None:
    """Refuse a definition that `status` could not replay.

    `load_store` runs this on stored definitions and `cmd_save` on the outline it is
    about to write, so a hand-edited file and a saved path obey the same rules.
    """
    for key in ("created_at", "updated_at"):
        value = item.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise PathError(f"{location} is damaged: path {item.get('id')} has a bad {key}")
    module_ids: list[str] = []
    kp_ids: set[str] = set()
    for module in item["modules"]:
        if not isinstance(module, dict) or not isinstance(module.get("id"), str) or not module["id"]:
            raise PathError(f"{location} is damaged: path {item['id']} has a module without id")
        if module["id"] in module_ids:
            raise PathError(f"{location} is damaged: duplicate module id {module['id']}")
        module_ids.append(module["id"])
        if not isinstance(module.get("title"), str) or not module["title"].strip():
            raise PathError(f"{location} is damaged: module {module['id']} has no title")
        if not isinstance(module.get("prerequisites", []), list) or not all(
                isinstance(p, str) for p in module.get("prerequisites", [])):
            raise PathError(f"{location} is damaged: module {module['id']} has bad prerequisites")
        kps = module.get("knowledge_points")
        if not isinstance(kps, list) or not kps:
            raise PathError(f"{location} is damaged: module {module['id']} has no knowledge points")
        local: set[str] = set()
        for kp in kps:
            if not isinstance(kp, dict) or not isinstance(kp.get("kp"), str) or not kp["kp"]:
                raise PathError(f"{location} is damaged: module {module['id']} has a knowledge point without kp")
            if kp["kp"] in kp_ids:
                raise PathError(f"{location} is damaged: duplicate knowledge point {kp['kp']}")
            kp_ids.add(kp["kp"])
            local.add(kp["kp"])
            if kp.get("knowledge_type") not in ll.KNOWLEDGE_TYPES:
                raise PathError(f"{location} is damaged: knowledge point {kp['kp']} has an unknown type")
            if not isinstance(kp.get("name"), str) or not kp["name"].strip():
                raise PathError(f"{location} is damaged: knowledge point {kp['kp']} has no name")
            if not isinstance(kp.get("objective"), str) or not kp["objective"].strip():
                raise PathError(f"{location} is damaged: knowledge point {kp['kp']} has no objective")
            if not isinstance(kp.get("evidence"), list) or not kp["evidence"]:
                raise PathError(f"{location} is damaged: knowledge point {kp['kp']} has no evidence")
            for entry in kp["evidence"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("source_id"), str) \
                        or not entry["source_id"].strip() or not isinstance(entry.get("page"), int) \
                        or isinstance(entry.get("page"), bool):
                    raise PathError(f"{location} is damaged: knowledge point {kp['kp']} "
                                    "has an evidence entry without source_id and integer page")
            if not isinstance(kp.get("prerequisites", []), list) or not all(
                    isinstance(p, str) for p in kp.get("prerequisites", [])):
                raise PathError(f"{location} is damaged: knowledge point {kp['kp']} has bad prerequisites")
        for kp in kps:
            for dep in kp.get("prerequisites", []):
                if dep not in local:
                    raise PathError(f"{location} is damaged: knowledge point {kp['kp']} in module "
                                    f"{module['id']} has unknown prerequisite {dep}")
        cycle = find_cycle({kp["kp"]: [p for p in kp.get("prerequisites", []) if p in local] for kp in kps})
        if cycle:
            raise PathError(f"{location} is damaged: module {module['id']} has a knowledge-point cycle: "
                            f"{' -> '.join(cycle)}")
    for module in item["modules"]:
        for dep in module.get("prerequisites", []):
            if dep == module["id"]:
                raise PathError(f"{location} is damaged: module {module['id']} lists itself as a prerequisite")
            if dep not in module_ids:
                raise PathError(f"{location} is damaged: module {module['id']} has unknown prerequisite {dep}")
    cycle = find_cycle({m["id"]: [p for p in m.get("prerequisites", []) if p != m["id"]] for m in item["modules"]})
    if cycle:
        raise PathError(f"{location} is damaged: module cycle {' -> '.join(cycle)}")


def _write_temp(store: dict, root: str, kb: str) -> Path:
    """Write a staged copy next to the target; the caller commits with os.replace."""
    target = store_path(root, kb)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return tmp


def commit_store(tmp: Path, root: str, kb: str) -> None:
    os.replace(tmp, store_path(root, kb))


def write_store(store: dict, root: str, kb: str) -> None:
    """Atomic replace via a temporary file in the same directory."""
    tmp = _write_temp(store, root, kb)
    try:
        commit_store(tmp, root, kb)
    finally:
        if tmp.exists():
            tmp.unlink()


# ----------------------------------------------------------------- knowledge


def _text(value, label: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return False
    return True


def _id_list(value, label: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        errors.append(f"{label} must be a list of non-empty ids")
        return []
    return list(dict.fromkeys(x.strip() for x in value))


def find_cycle(nodes: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle path (first id repeated at the end) or None."""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in nodes.get(node, []):
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if dep in nodes and state.get(dep, 0) == 0:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in nodes:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def _evidence(conn, kp: str, items, errors: list[str], warnings: list[str]):
    """Validate evidence against the KB index and resolve display/unit."""
    if not isinstance(items, list) or not items:
        errors.append(f"knowledge point {kp!r} has no evidence (at least one source_id + page is required)")
        return []
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"knowledge point {kp!r} has an evidence entry that is not an object")
            continue
        source_id, page = item.get("source_id"), item.get("page")
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"knowledge point {kp!r} has evidence without source_id")
            continue
        if not isinstance(page, int) or isinstance(page, bool):
            errors.append(f"knowledge point {kp!r} cites {source_id!r} without an integer page")
            continue
        if (source_id, page) in seen:
            warnings.append(f"knowledge point {kp!r} lists {source_id!r} {page} more than once; duplicates removed")
            continue
        seen.add((source_id, page))
        src = conn.execute("SELECT source_id, display, unit, units FROM sources WHERE source_id = ?",
                           (source_id,)).fetchone()
        if src is None:
            errors.append(f"knowledge point {kp!r} cites unknown source {source_id!r}")
            continue
        row = conn.execute("SELECT origin, text FROM pages WHERE source_id = ? AND num = ?",
                           (source_id, page)).fetchone()
        if row is None:
            errors.append(f"knowledge point {kp!r} cites {src['display']!r} {src['unit']} {page}, "
                          f"which does not exist (1..{src['units']})")
            continue
        if not row["text"].strip():
            errors.append(f"knowledge point {kp!r} cites {src['display']!r} {src['unit']} {page}, "
                          f"which has no text ({row['origin']}) and cannot be evidence")
            continue
        out.append({"source_id": src["source_id"], "page": page,
                    "display": src["display"], "unit": src["unit"]})
    return out


# ---------------------------------------------------------------- validation


def validate_outline(outline: dict, store: dict, kb: str, conn, defined: dict[str, str]):
    """Return (path, errors, warnings). `defined` maps kp -> knowledge_type."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(outline, dict):
        raise PathError("outline must be a JSON object")
    name = outline.get("name")
    target_id = outline.get("id")
    if target_id is not None and (not isinstance(target_id, str) or not target_id.strip()):
        errors.append("path id must be a non-empty string when given")
        target_id = None
    if _text(name, "path name", errors):
        norm = ll.normalize_name(name)
        for other in store["paths"]:
            if other["id"] != target_id and ll.normalize_name(other["name"]) == norm:
                errors.append(f"path name {name.strip()!r} is already used by path {other['id']}")

    raw_modules = outline.get("modules")
    if not isinstance(raw_modules, list) or not raw_modules:
        errors.append("modules must be a non-empty list")
        return None, errors, warnings

    modules, module_ids, kp_seen = [], {}, {}
    for index, raw in enumerate(raw_modules, 1):
        if not isinstance(raw, dict):
            errors.append(f"module #{index} is not an object")
            continue
        mid = raw.get("id")
        if not isinstance(mid, str) or not mid.strip():
            errors.append(f"module #{index} has no id")
            continue
        mid = mid.strip()
        if mid in module_ids:
            errors.append(f"duplicate module id {mid!r}")
            continue
        module_ids[mid] = []
        if not _text(raw.get("title"), f"module {mid!r} title", errors):
            continue
        raw_kps = raw.get("knowledge_points")
        if not isinstance(raw_kps, list) or not raw_kps:
            errors.append(f"module {mid!r} has no knowledge points")
            continue
        kps, local_ids = [], {}
        for kp_index, raw_kp in enumerate(raw_kps, 1):
            if not isinstance(raw_kp, dict):
                errors.append(f"module {mid!r} knowledge point #{kp_index} is not an object")
                continue
            kp_name = raw_kp.get("name")
            if not _text(kp_name, f"module {mid!r} knowledge point #{kp_index} name", errors):
                continue
            kp = ll.kp_id(kb, kp_name)
            if kp in kp_seen:
                errors.append(f"duplicate knowledge point {kp!r} (modules {kp_seen[kp]!r} and {mid!r})")
                continue
            kp_seen[kp] = mid
            ktype = raw_kp.get("knowledge_type")
            if ktype not in ll.KNOWLEDGE_TYPES:
                errors.append(f"knowledge point {kp!r} knowledge_type must be one of {ll.KNOWLEDGE_TYPES}")
                continue
            if kp in defined and defined[kp] != ktype:
                errors.append(f"knowledge point {kp!r} is already defined as {defined[kp]!r} in the learner log; "
                              "changing its type requires a new name")
                continue
            if not _text(raw_kp.get("objective"), f"knowledge point {kp!r} objective", errors):
                continue
            prereqs = _id_list(raw_kp.get("prerequisites"), f"knowledge point {kp!r} prerequisites", errors)
            local_ids[kp] = prereqs
            evidence = _evidence(conn, kp, raw_kp.get("evidence"), errors, warnings)
            kps.append({"kp": kp, "name": kp_name.strip(), "knowledge_type": ktype,
                        "objective": raw_kp["objective"].strip(), "prerequisites": prereqs,
                        "evidence": evidence})
        module_ids[mid] = kps
        modules.append({"id": mid, "title": raw["title"].strip(),
                        "prerequisites": _id_list(raw.get("prerequisites"), f"module {mid!r} prerequisites", errors),
                        "knowledge_points": kps})
        for kp, prereqs in local_ids.items():
            for dep in (p for p in prereqs if p not in local_ids):
                errors.append(f"knowledge point {kp!r} in module {mid!r} has an unknown or cross-module "
                              f"prerequisite {dep!r}; knowledge-point prerequisites stay inside their module")
        cycle = find_cycle({k: [p for p in v if p in local_ids] for k, v in local_ids.items()})
        if cycle:
            errors.append(f"knowledge-point prerequisites in module {mid!r} form a cycle: {' -> '.join(cycle)}")

    graph = {m["id"]: m["prerequisites"] for m in modules}
    for module in modules:
        for dep in module["prerequisites"]:
            if dep == module["id"]:
                errors.append(f"module {module['id']!r} lists itself as a prerequisite")
            elif dep not in module_ids:
                errors.append(f"module {module['id']!r} has unknown prerequisite {dep!r}")
    cycle = find_cycle({k: [p for p in v if p != k] for k, v in graph.items()})
    if cycle:
        errors.append(f"module prerequisites form a cycle: {' -> '.join(cycle)}")

    path = {"id": target_id or None, "name": name.strip() if isinstance(name, str) and name.strip() else "",
            "modules": modules}
    return path, errors, warnings


def load_definitions(root: str, learner: str, warnings: list[str]):
    """Return (events, {kp: knowledge_type}) from one read of the learner log."""
    events, log_warnings = ll.read_events(ll.log_path(root, learner))
    for item in log_warnings:
        warnings.append(f"learner log line {item['line']}: {item['problem']}")
    defined: dict[str, str] = {}
    for event in events:
        if event["type"] == "kp_define":
            defined[event["kp"]] = event["knowledge_type"]
    return events, defined


# -------------------------------------------------------------- status replay


def _eligible(event: dict) -> bool:
    """Attempts that may open a gate. Legacy attempts (no origin) are evidence too."""
    return event.get("origin") in GATE_ORIGINS


def _question_id(attempt: dict) -> str | None:
    qid = attempt.get("question_id")
    return qid if isinstance(qid, str) and qid else None


def _unique_attempts(attempts: list[dict]) -> list[dict]:
    """One record per question_id; unaided evidence outranks a hinted retry.

    Re-recording an already answered question must not multiply evidence, and a
    hinted retry of the same question must not erase the unaided answer. Answers
    without a question_id are returned unchanged; `_gate_attempts` drops them.
    """
    best: dict[str, int] = {}
    for index, attempt in enumerate(attempts):
        qid = _question_id(attempt)
        if qid is None:
            continue
        current = best.get(qid)
        if current is None or not attempt.get("hints_used") or attempts[current].get("hints_used"):
            best[qid] = index
    keep = set(best.values())
    return [attempt for index, attempt in enumerate(attempts)
            if index in keep or _question_id(attempt) is None]


def _display_mastery(attempts: list[dict]):
    """The same number `ledger.py state` shows: all attempts, hints excluded."""
    scores = ll.mastery_scores(attempts)
    return round(compute_mastery(scores), 4) if scores else None


def _gate_attempts(attempts: list[dict]) -> list[dict]:
    """Attempts that may open a gate: eligible origin, a question id, deduplicated.

    A question without an id cannot be told apart from a duplicate, so it never
    counts toward a gate (it still shows in `mastery`, as in the ledger).
    """
    return [a for a in _unique_attempts(attempts)
            if _eligible(a) and _question_id(a) is not None]


def _gate(kp: dict, attempts: list[dict]):
    """Return (gate, gaps, qualitative). Gates ignore hints and diagnostic attempts."""
    eligible = _gate_attempts(attempts)
    unaided = [a for a in eligible if not a.get("hints_used")]
    if kp["knowledge_type"] in NUMERIC_TYPES:
        scored = [a for a in unaided if a.get("assessment_type", "quiz") != "qualitative"]
        failed_qualitative = [a for a in unaided
                              if a.get("assessment_type") == "qualitative" and a["result"] != "correct"]
        mastery = compute_mastery(ll.mastery_scores(scored)) if scored else None
        recent = [a["result"] for a in scored[-GATE_UNAIDED:]]
        passed = bool(mastery is not None and mastery >= GATE_THRESHOLD
                      and len(scored) >= GATE_UNAIDED and all(r == "correct" for r in recent)
                      and not failed_qualitative)
        gate = {"kind": "numeric", "passed": passed,
                "mastery": round(mastery, 4) if mastery is not None else None,
                "unaided": len(scored), "recent": recent,
                "needed": {"mastery": GATE_THRESHOLD, "unaided": GATE_UNAIDED}}
        gaps = []
        if mastery is None or mastery < GATE_THRESHOLD:
            gaps.append({"code": "mastery_below", "value": round(mastery, 4) if mastery is not None else None,
                         "needed": GATE_THRESHOLD})
        if len(scored) < GATE_UNAIDED:
            gaps.append({"code": "unaided_below", "value": len(scored), "needed": GATE_UNAIDED})
        elif not all(r == "correct" for r in recent):
            gaps.append({"code": "recent_failure", "value": recent})
        if failed_qualitative:
            gaps.append({"code": "qualitative_failure",
                         "value": [a["result"] for a in failed_qualitative]})
        return gate, gaps, None
    qualitative = [a for a in unaided if a.get("assessment_type") == "qualitative"]
    latest = qualitative[-1]["result"] if qualitative else None
    gate = {"kind": "qualitative", "passed": latest == "correct", "latest": latest,
            "attempts": len(qualitative)}
    gaps = []
    if latest is None:
        gaps.append({"code": "qualitative_missing"})
    elif latest != "correct":
        gaps.append({"code": "qualitative_not_correct", "value": latest})
    return gate, gaps, latest


def _evidence_status(conn, kp: dict) -> list[dict]:
    out = []
    for item in kp["evidence"]:
        source = conn.execute("SELECT display, unit FROM sources WHERE source_id = ?",
                              (item.get("source_id"),)).fetchone()
        problem = None
        if source is None:
            problem = "unknown_source"
        else:
            row = conn.execute("SELECT origin, text FROM pages WHERE source_id = ? AND num = ?",
                               (item.get("source_id"), item.get("page"))).fetchone()
            if row is None:
                problem = "unknown_page"
            elif not row["text"].strip():
                problem = "empty_page"
        out.append({"source_id": item.get("source_id"), "page": item.get("page"),
                    "display": item.get("display") or (source["display"] if source else None),
                    "unit": item.get("unit") or (source["unit"] if source else None),
                    "valid": problem is None, "problem": problem})
    return out


def _kp_status(kp: dict, attempts: list[dict], evidence: list[dict]) -> dict:
    gate, gaps, qualitative = _gate(kp, attempts)
    invalid = [e for e in evidence if not e["valid"]]
    if invalid:
        gaps.append({"code": "evidence_invalid",
                     "value": [f"{e['source_id']} {e['page']}" for e in invalid]})
    unaided = [a for a in _gate_attempts(attempts) if not a.get("hints_used")]
    return {"kp": kp["kp"], "name": kp["name"], "knowledge_type": kp["knowledge_type"],
            "objective": kp["objective"], "prerequisites": kp.get("prerequisites", []),
            "mastery": _display_mastery(attempts),
            "unaided_attempts": len(unaided),
            "last_unaided": unaided[-1]["result"] if unaided else None,
            "qualitative": qualitative, "gate": gate,
            "pass": bool(gate["passed"] and not invalid),
            "evidence": evidence, "gaps": gaps}


def topo_order(path: dict) -> list[str]:
    ids = [m["id"] for m in path["modules"]]
    deps = {m["id"]: set(m["prerequisites"]) for m in path["modules"]}
    order: list[str] = []
    while len(order) < len(ids):
        ready = [mid for mid in ids if mid not in order and deps[mid] <= set(order)]
        if not ready:
            raise PathError("path definition has a prerequisite cycle; fix it and save again")
        order.extend(ready)
    return order


def _mastered_at(path: dict, events: list[dict], evidence_ok: dict, order: list[str]) -> dict:
    """Forward replay in log order: first event time a module is unlocked and all its gates pass."""
    module_map = {m["id"]: m for m in path["modules"]}
    kp_defs = {kp["kp"]: kp for m in path["modules"] for kp in m["knowledge_points"]}
    seen: dict[str, list[dict]] = {kp: [] for kp in kp_defs}
    passes = {kp: False for kp in kp_defs}
    mastered: dict[str, object] = {}
    for event in events:
        kp = event["kp"]
        if kp not in kp_defs:
            continue
        seen[kp].append(event)
        gate, _gaps, _q = _gate(kp_defs[kp], seen[kp])
        passes[kp] = gate["passed"] and evidence_ok[kp]
        for mid in order:
            if mid in mastered:
                continue
            module = module_map[mid]
            if not all(passes[k["kp"]] for k in module["knowledge_points"]):
                continue
            if not all(dep in mastered for dep in module["prerequisites"]):
                continue
            mastered[mid] = event["ts"]
    return mastered


def _ancestors(path: dict, mid: str, seen=None) -> set[str]:
    seen = seen or set()
    for module in path["modules"]:
        if module["id"] == mid:
            for dep in module["prerequisites"]:
                if dep not in seen:
                    seen.add(dep)
                    _ancestors(path, dep, seen)
            break
    return seen


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


def _status_payload(root: str, kb: str, learner: str, path: dict) -> dict:
    conn = connect_index(root, kb)
    try:
        events, log_warnings = ll.read_events(ll.log_path(root, learner))
        attempts: dict[str, list[dict]] = {}
        ordered_attempts = []
        for event in events:
            if event["type"] == "attempt":
                ordered_attempts.append(event)
                attempts.setdefault(event["kp"], []).append(event)
        order = topo_order(path)

        module_invalid: dict[str, list[dict]] = {}
        statuses: dict[str, dict] = {}
        evidence_ok: dict[str, bool] = {}
        for module in path["modules"]:
            kps = []
            invalid = []
            for kp in module["knowledge_points"]:
                evidence = _evidence_status(conn, kp)
                evidence_ok[kp["kp"]] = all(e["valid"] for e in evidence)
                status = _kp_status(kp, attempts.get(kp["kp"], []), evidence)
                kps.append(status)
                invalid.extend({"module": module["id"], "kp": kp["kp"]}
                               for e in evidence if not e["valid"])
            module_invalid[module["id"]] = invalid
            statuses[module["id"]] = {"kps": kps, "invalid": invalid}

        mastered_at = _mastered_at(path, ordered_attempts, evidence_ok, order)
        invalid_entries = {mid: list(entries) for mid, entries in module_invalid.items()}
        for mid in statuses:
            for ancestor in _ancestors(path, mid):
                invalid_entries[mid].extend(module_invalid[ancestor])
            invalid_entries[mid].sort(key=lambda e: (e["module"], e["kp"]))
        effective = {mid for mid in mastered_at if not invalid_entries[mid]}

        modules = []
        for module in path["modules"]:
            locked_by = [dep for dep in module["prerequisites"] if dep not in effective]
            if locked_by:
                state = "locked"
            elif module["id"] in effective:
                state = "mastered"
            elif any(_eligible(a) and a.get("origin") != "exemption" for kp in module["knowledge_points"]
                     for a in attempts.get(kp["kp"], [])):
                state = "in_progress"
            else:
                state = "available"
            missing = [] if state == "mastered" else [
                kp["kp"] for kp in statuses[module["id"]]["kps"] if not kp["pass"]]
            regressed = [kp["kp"] for kp in statuses[module["id"]]["kps"]
                         if state == "mastered" and not kp["pass"]]
            modules.append({"id": module["id"], "title": module["title"],
                            "status": state, "mastered_at": mastered_at.get(module["id"]) if state == "mastered" else None,
                            "mastered_at_iso": _iso(mastered_at.get(module["id"])) if state == "mastered" else None,
                            "locked_by": locked_by, "invalidated_by": invalid_entries[module["id"]],
                            "knowledge_points": statuses[module["id"]]["kps"], "missing": missing,
                            "regressed": regressed})

        next_action = None
        for module in modules:
            if module["status"] not in ("available", "in_progress"):
                continue
            by_kp = {kp["kp"]: kp for kp in module["knowledge_points"]}
            for kp in module["knowledge_points"]:
                if kp["pass"] or not all(by_kp[dep]["pass"] for dep in kp["prerequisites"]):
                    continue
                if any(g["code"] == "evidence_invalid" for g in kp["gaps"]):
                    continue
                has_attempts = any(_eligible(a) for a in attempts.get(kp["kp"], []))
                action = "learn" if kp["knowledge_type"] in QUALITATIVE_TYPES or not has_attempts else "quiz"
                next_action = {"module": module["id"], "kp": kp["kp"], "action": action,
                               "exemption_available": module["status"] == "available"}
                break
            if next_action:
                break

        progress = {"modules": len(modules), "mastered": sum(m["status"] == "mastered" for m in modules)}
        all_invalid = sorted({(e["module"], e["kp"]) for entries in invalid_entries.values() for e in entries})
        has_available_work = any(m["status"] in ("available", "in_progress") for m in modules)
        result = {"path": {"id": path["id"], "name": path["name"]}, "modules": modules,
                  "progress": progress, "next_action": next_action,
                  "path_complete": progress["modules"] > 0 and progress["mastered"] == progress["modules"],
                  "invalidated_by": [{"module": m, "kp": kp} for m, kp in all_invalid]}
        if not next_action and has_available_work and all_invalid:
            result["blocked_by_invalid_evidence"] = True
        if log_warnings:
            result["warnings"] = [f"learner log line {w['line']}: {w['problem']}" for w in log_warnings]
        return result
    finally:
        conn.close()


# ------------------------------------------------------------------- commands


def _affected_modules(store: dict, path: dict) -> list[str]:
    """Modules whose definition changed, plus everything downstream of them."""
    existing = next((p for p in store["paths"] if p["id"] == path["id"]), None) if path["id"] else None
    if existing is None:
        return [m["id"] for m in path["modules"]]
    old = {m["id"]: m for m in existing["modules"]}
    new = {m["id"]: m for m in path["modules"]}
    changed = {mid for mid in new if mid not in old or new[mid] != old[mid]}
    changed |= {mid for mid in old if mid not in new}
    grown = True
    while grown:
        grown = False
        for module in path["modules"]:
            if module["id"] not in changed and any(dep in changed for dep in module["prerequisites"]):
                changed.add(module["id"])
                grown = True
    return [m["id"] for m in path["modules"] if m["id"] in changed]


def cmd_list(args) -> dict:
    connect_index(args.root, args.kb).close()
    store = load_store(args.root, args.kb)
    return {
        "kb": args.kb,
        "active_path_id": store["active_path_id"],
        "paths": [{"id": p["id"], "name": p["name"],
                   "module_count": len(p["modules"]),
                   "knowledge_point_count": sum(len(m["knowledge_points"]) for m in p["modules"]),
                   "created_at": p.get("created_at"), "updated_at": p.get("updated_at")}
                  for p in store["paths"]],
    }


def find_path(store: dict, path_id: str | None) -> dict:
    if path_id is None:
        path_id = store["active_path_id"]
        if path_id is None:
            raise PathError("no active path; pass --path or activate one first")
    for item in store["paths"]:
        if item["id"] == path_id:
            return item
    raise PathError(f"unknown path {path_id!r}")


def cmd_show(args) -> dict:
    connect_index(args.root, args.kb).close()
    store = load_store(args.root, args.kb)
    path = find_path(store, args.path)
    return {"kb": args.kb, "active_path_id": store["active_path_id"], "active": store["active_path_id"] == path["id"],
            "path": path}


def cmd_validate(args, stdin) -> dict:
    outline = _read_json(stdin, "outline")
    conn = connect_index(args.root, args.kb)
    try:
        store = load_store(args.root, args.kb)
        warnings: list[str] = []
        _events, defined = load_definitions(args.root, args.learner, warnings)
        path, errors, outline_warnings = validate_outline(outline, store, args.kb, conn, defined)
        warnings.extend(outline_warnings)
    finally:
        conn.close()
    affected: list[str] = []
    if path is not None and not errors:
        try:
            _check_structure("outline", path)
            affected = _affected_modules(store, path)
        except PathError as exc:
            errors.append(str(exc))
    kps = [kp for module in (path or {}).get("modules", []) for kp in module["knowledge_points"]]
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "affected_modules": affected,
            "summary": {"name": (path or {}).get("name"),
                        "modules": len((path or {}).get("modules", [])),
                        "knowledge_points": len(kps),
                        "evidence": sum(len(kp["evidence"]) for kp in kps),
                        "new_definitions": sum(kp["kp"] not in defined for kp in kps),
                        "existing_definitions": sum(kp["kp"] in defined for kp in kps)}}


def cmd_save(args, stdin) -> dict:
    outline = _read_json(stdin, "outline")
    conn = connect_index(args.root, args.kb)
    try:
        store = load_store(args.root, args.kb)
        warnings: list[str] = []
        existing_events, defined = load_definitions(args.root, args.learner, warnings)
        path, errors, outline_warnings = validate_outline(outline, store, args.kb, conn, defined)
        warnings.extend(outline_warnings)
        if errors:
            raise PathError("cannot save: " + "; ".join(errors))
        try:
            _check_structure("outline", path)
        except PathError as exc:
            raise PathError(f"cannot save: {exc}") from exc
        affected = _affected_modules(store, path)
        now = args.now if args.now is not None else time.time()
        existing = next((p for p in store["paths"] if p["id"] == path["id"]), None)
        created = existing is None
        path["id"] = path["id"] or f"p_{uuid.uuid4().hex[:8]}"
        path["created_at"] = int(now) if created else existing.get("created_at", int(now))
        path["updated_at"] = int(now)
        store["paths"] = [p for p in store["paths"] if p["id"] != path["id"]] + [path]
        store["paths"].sort(key=lambda p: p.get("created_at") or 0)
        if args.activate:
            store["active_path_id"] = path["id"]

        log = ll.log_path(args.root, args.learner)
        pending = [
            ll.prepare_event({
                "type": "kp_define", "kb": args.kb, "name": kp["name"],
                "knowledge_type": kp["knowledge_type"],
                "sources": [f"{e['display']} {e['unit']} {e['page']}" for e in kp["evidence"]],
            }, existing_events, now)
            for module in path["modules"] for kp in module["knowledge_points"]
            if kp["kp"] not in defined
        ]
        # Stage the definition, append the missing definitions, then commit (KTD4).
        tmp = _write_temp(store, args.root, args.kb)
        written = []
        try:
            if pending:
                log.parent.mkdir(parents=True, exist_ok=True)
                for event in pending:
                    ll.append_line(log, json.dumps(event, ensure_ascii=False, sort_keys=True))
                    written.append({"kp": event["kp"], "name": event["name"],
                                    "knowledge_type": event["knowledge_type"]})
            commit_store(tmp, args.root, args.kb)
        except OSError as exc:
            names = ", ".join(f"{w['name']!r} ({w['knowledge_type']})" for w in written) or "none"
            raise PathError(f"path {path['id']} ({path['name']!r}) was not saved: {exc}; "
                            f"definitions written before the failure: {names}. "
                            "Save again: they are reused, not duplicated") from exc
        finally:
            if tmp.exists():
                tmp.unlink()
    finally:
        conn.close()
    result = {"path_id": path["id"], "name": path["name"], "created": created,
              "active_path_id": store["active_path_id"],
              "affected_modules": affected,
              "kps_defined": written,
              "summary": {"modules": len(path["modules"]),
                          "knowledge_points": sum(len(m["knowledge_points"]) for m in path["modules"])}}
    if warnings:
        result["warnings"] = warnings
    return result


def cmd_activate(args) -> dict:
    require_kb(args.root, args.kb)
    store = load_store(args.root, args.kb)
    find_path(store, args.path)
    store["active_path_id"] = args.path
    write_store(store, args.root, args.kb)
    return {"kb": args.kb, "active_path_id": store["active_path_id"]}


def cmd_deactivate(args) -> dict:
    require_kb(args.root, args.kb)
    store = load_store(args.root, args.kb, tolerate_dangling=True)
    store["active_path_id"] = None
    write_store(store, args.root, args.kb)
    return {"kb": args.kb, "active_path_id": None}


def cmd_rename(args) -> dict:
    require_kb(args.root, args.kb)
    store = load_store(args.root, args.kb)
    path = find_path(store, args.path)
    norm = ll.normalize_name(args.name)
    if not args.name.strip():
        raise PathError("path name must be a non-empty string")
    for other in store["paths"]:
        if other["id"] != path["id"] and ll.normalize_name(other["name"]) == norm:
            raise PathError(f"path name {args.name.strip()!r} is already used by path {other['id']}")
    path["name"] = args.name.strip()
    path["updated_at"] = int(args.now if args.now is not None else time.time())
    write_store(store, args.root, args.kb)
    return {"kb": args.kb, "path_id": path["id"], "name": path["name"]}


def cmd_delete(args) -> dict:
    require_kb(args.root, args.kb)
    store = load_store(args.root, args.kb)
    path = find_path(store, args.path)
    store["paths"] = [p for p in store["paths"] if p["id"] != path["id"]]
    if store["active_path_id"] == path["id"]:
        store["active_path_id"] = None
    write_store(store, args.root, args.kb)
    return {"kb": args.kb, "deleted": path["id"], "active_path_id": store["active_path_id"]}


def cmd_status(args) -> dict:
    require_kb(args.root, args.kb)
    ll.log_path(args.root, args.learner)  # validate the learner name even with no paths
    store = load_store(args.root, args.kb)
    if args.all:
        paths = []
        for item in store["paths"]:
            payload = _status_payload(args.root, args.kb, args.learner, item)
            paths.append({"active": item["id"] == store["active_path_id"], **payload})
        return {"kb": args.kb, "learner": args.learner,
                "active_path_id": store["active_path_id"], "paths": paths}
    if args.path is None and store["active_path_id"] is None:
        return {"kb": args.kb, "learner": args.learner, "active_path_id": None, "path": None,
                "modules": [], "progress": {"modules": 0, "mastered": 0},
                "next_action": None, "path_complete": False, "invalidated_by": []}
    path = find_path(store, args.path)
    payload = _status_payload(args.root, args.kb, args.learner, path)
    return {"kb": args.kb, "learner": args.learner, "active_path_id": store["active_path_id"],
            "path": payload.pop("path"), **payload}


def cmd_resolve(args) -> dict:
    require_kb(args.root, args.kb)
    ll.log_path(args.root, args.learner)
    store = load_store(args.root, args.kb)
    if store["active_path_id"] is None:
        return {"allowed": True, "reason": "no_active_path", "active_path_id": None}
    path = find_path(store, None)
    payload = _status_payload(args.root, args.kb, args.learner, path)
    module = next((m for m in payload["modules"] if m["id"] == args.module), None)
    kp_id = None
    if args.kp is not None:
        kp_ids = [kp["kp"] for m in payload["modules"] for kp in m["knowledge_points"]]
        kp_id = args.kp if args.kp in kp_ids else ll.kp_id(args.kb, args.kp)
        module = next((m for m in payload["modules"]
                       if any(kp["kp"] == kp_id for kp in m["knowledge_points"])), None)
        if module is None:
            return {"allowed": False, "reason": "out_of_path", "kp": args.kp,
                    "active_path_id": store["active_path_id"]}
    if module is None:
        return {"allowed": False, "reason": "unknown_module", "module": args.module,
                "active_path_id": store["active_path_id"]}
    if module["status"] == "locked":
        return {"allowed": False, "reason": "locked", "module": module["id"],
                "locked_by": module["locked_by"], "next_action": payload["next_action"],
                "active_path_id": store["active_path_id"]}
    if kp_id is None and module["invalidated_by"]:
        return {"allowed": False, "reason": "evidence_invalid", "module": module["id"],
                "invalidated_by": module["invalidated_by"], "next_action": payload["next_action"],
                "active_path_id": store["active_path_id"]}
    if module["status"] == "mastered":
        return {"allowed": False, "reason": "mastered", "module": module["id"],
                "next_action": payload["next_action"], "hint": "review remains available",
                "active_path_id": store["active_path_id"]}
    if kp_id is not None:
        kp = next(k for k in module["knowledge_points"] if k["kp"] == kp_id)
        if any(g["code"] == "evidence_invalid" for g in kp["gaps"]):
            return {"allowed": False, "reason": "evidence_invalid", "module": module["id"], "kp": kp_id,
                    "invalidated_by": [{"module": module["id"], "kp": kp_id}],
                    "next_action": payload["next_action"], "active_path_id": store["active_path_id"]}
        if kp["pass"]:
            return {"allowed": False, "reason": "already_passed", "module": module["id"], "kp": kp_id,
                    "module_status": module["status"], "next_action": payload["next_action"],
                    "hint": "review remains available", "active_path_id": store["active_path_id"]}
    return {"allowed": True, "reason": module["status"], "module": module["id"], "kp": kp_id,
            "next_action": payload["next_action"], "active_path_id": store["active_path_id"]}


# ----------------------------------------------------------------------- CLI


def _read_json(stdin, label: str) -> dict:
    try:
        data = ll.loads_strict(stdin.read())
    except ValueError as exc:
        raise PathError(f"stdin is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PathError(f"{label} must be a JSON object")
    return data


def parser() -> argparse.ArgumentParser:
    p = PathArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project root containing .tutor/")
    p.add_argument("--learner", default="default", help="learner whose kp_define events are checked")
    p.add_argument("--now", type=float, default=None, help="epoch seconds (testing)")
    sub = p.add_subparsers(dest="command", required=True)

    def with_kb(sp):
        sp.add_argument("--kb", required=True)
        return sp

    with_kb(sub.add_parser("list"))
    show = with_kb(sub.add_parser("show"))
    show.add_argument("--path", default=None)
    with_kb(sub.add_parser("validate"))
    save = with_kb(sub.add_parser("save"))
    save.add_argument("--activate", action="store_true")
    with_kb(sub.add_parser("activate")).add_argument("--path", required=True)
    with_kb(sub.add_parser("deactivate"))
    ren = with_kb(sub.add_parser("rename"))
    ren.add_argument("--path", required=True)
    ren.add_argument("--name", required=True)
    with_kb(sub.add_parser("delete")).add_argument("--path", required=True)
    status = with_kb(sub.add_parser("status"))
    status_view = status.add_mutually_exclusive_group()
    status_view.add_argument("--path", default=None)
    status_view.add_argument("--all", action="store_true", help="every path and its state")
    status.add_argument("--learner", default=argparse.SUPPRESS,
                        help="learner to replay (also accepted before the command)")
    resolve = with_kb(sub.add_parser("resolve"))
    resolve.add_argument("--learner", default=argparse.SUPPRESS,
                         help="learner to replay (also accepted before the command)")
    target = resolve.add_mutually_exclusive_group(required=True)
    target.add_argument("--module", default=None)
    target.add_argument("--kp", default=None)
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
        if args.command in ("validate", "save"):
            result = {"validate": cmd_validate, "save": cmd_save}[args.command](args, stdin)
        else:
            result = {"list": cmd_list, "show": cmd_show, "activate": cmd_activate,
                      "deactivate": cmd_deactivate, "rename": cmd_rename,
                      "delete": cmd_delete, "status": cmd_status,
                      "resolve": cmd_resolve}[args.command](args)
    except (PathError, ll.LedgerError) as exc:
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
