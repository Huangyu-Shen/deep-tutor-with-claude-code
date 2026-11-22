"""Generate memory golden vectors by running DeepTutor's ORIGINAL code.

Development-time only. Needs a DeepTutor source checkout and Python >= 3.10
(upstream uses newer dataclass syntax; the ported scripts and the tests that
replay these vectors still run on Python 3.9):

    python3 -I tests/golden/generate_memory.py /path/to/DeepTutor-main

Writes tests/golden/memory_vectors.json. The ported scripts (memory_doc.py,
memory_snap.py) must reproduce every value in it (tests/test_golden_memory.py).

Only the pure modules are loaded. The upstream packages are stubbed (just a
``__path__``, so their ``__init__.py`` files never run), ``memory.paths`` is a
minimal stand-in exposing ``SURFACES``, and the two modules that ``recall.py``
imports lazily (``snapshot.adapters`` and ``trace``) are replaced by fakes fed
from the scenario. Clocks are patched to fixed values.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types

DT_ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
if DT_ROOT is None or not (DT_ROOT / "deeptutor").is_dir():
    raise SystemExit("usage: generate_memory.py /path/to/DeepTutor-main")
if sys.version_info < (3, 10):
    raise SystemExit("generate_memory.py needs Python >= 3.10")

MEM = DT_ROOT / "deeptutor" / "services" / "memory"
SURFACES = ["chat", "notebook", "quiz", "kb", "book", "partner", "cowriter"]
ORDER = ["chat", "notebook", "quiz", "kb", "book", "partner", "cowriter"]

SOURCE_FILES = [
    "ids.py", "document.py", "ops.py", "recall.py",
    "snapshot/entity.py", "snapshot/diff.py", "snapshot/adapters.py",
]


def sha256(rel: str) -> str:
    return hashlib.sha256((MEM / rel).read_bytes()).hexdigest()


def stub_package(name: str, path: Path) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]
    sys.modules[name] = mod
    return mod


stub_package("deeptutor", DT_ROOT / "deeptutor")
stub_package("deeptutor.services", DT_ROOT / "deeptutor" / "services")
stub_package("deeptutor.services.memory", MEM)
stub_package("deeptutor.services.memory.snapshot", MEM / "snapshot")

paths_stub = types.ModuleType("deeptutor.services.memory.paths")
paths_stub.SURFACES = tuple(SURFACES)
paths_stub.Surface = str
sys.modules["deeptutor.services.memory.paths"] = paths_stub

ids = importlib.import_module("deeptutor.services.memory.ids")
document = importlib.import_module("deeptutor.services.memory.document")
ops = importlib.import_module("deeptutor.services.memory.ops")
entity = importlib.import_module("deeptutor.services.memory.snapshot.entity")
diff = importlib.import_module("deeptutor.services.memory.snapshot.diff")
recall = importlib.import_module("deeptutor.services.memory.recall")

# adapters.py imports multi-user path code; only the tiny _sha1 helper is needed.
_tree = ast.parse((MEM / "snapshot" / "adapters.py").read_text(encoding="utf-8"))
_sha1_src = next(n for n in _tree.body if isinstance(n, ast.FunctionDef) and n.name == "_sha1")
_ns: dict = {"hashlib": hashlib, "json": json}
exec(ast.unparse(_sha1_src), _ns)  # noqa: S102
upstream_sha1 = _ns["_sha1"]

# ---------------------------------------------------------------- ids

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class FakeTime:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


class FakeSecrets:
    def __init__(self, value: int) -> None:
        self.value = value

    def randbits(self, bits: int) -> int:
        return self.value & ((1 << bits) - 1)


def upstream_ulid(now_ms: int, rand: int) -> str:
    real_time, real_secrets = ids.time, ids.secrets
    ids.time, ids.secrets = FakeTime(now_ms / 1000.0), FakeSecrets(rand)
    try:
        return ids.new_ulid()
    finally:
        ids.time, ids.secrets = real_time, real_secrets


ULID_CASES = [
    {"now_ms": 0, "rand": 0},
    {"now_ms": 1, "rand": 1},
    {"now_ms": 1_700_000_000_000, "rand": 0x123456789ABCDEF01234},
    {"now_ms": 1_700_000_000_123, "rand": (1 << 80) - 1},
    {"now_ms": 4_102_444_800_000, "rand": 31},
]
for c in ULID_CASES:
    c["ulid"] = upstream_ulid(c["now_ms"], c["rand"])

REF_CASES = [
    "m_01HZK4ABCDEFGHJKMNPQRSTVWX",
    "m_short",
    "m_01HZK4ABCDEFGHJKMNPQRSTVWI",  # I is not in the alphabet
    "m_01hzk4abcdefghjkmnpqrstvwx",
    "chat:01HZK4ABCDEFGHJKMNPQRSTVWX",
    "CHAT:01HZK4ABCDEFGHJKMNPQRSTVWX",
    "01HZK4ABCDEFGHJKMNPQRSTVWX",
    "quiz:abc123",
    "quiz:session:question-1",
    "kb:my_kb.v2",
    "kb:has space",
    "kb:has,comma",
    "notebook:",
    "1chat:abc",
    "chat",
    "notebook",
    "quiz",
    "kb",
    "book",
    "partner",
    "cowriter",
    "CHAT",
    "co-writer",
    "not-an-id",
    "",
    "learn-abc",
    "chat:learn-abc_1",
]
ID_VECTORS = {
    "ulid": ULID_CASES,
    "refs": [
        {
            "s": s,
            "entry": ids.is_entry_id(s),
            "trace": ids.is_trace_id(s),
            "snapshot": ids.is_snapshot_ref(s),
            "shortname": ids.is_shortname_ref(s),
            "valid": ids.is_valid_ref(s),
        }
        for s in REF_CASES
    ],
}

# ---------------------------------------------------------------- documents

E1, E2, E3, E4 = (f"m_01HZK{c}ABCDEFGHJKMNPQRSTVWX" for c in "4567")

DOC_STRUCTS = {
    "two_sections_shared_refs": {
        "title": "Chat memory",
        "sections": [
            ["Concepts", [
                {"id": E1, "text": "Limit definition uses ε-δ pairing", "refs": ["chat:S1"]},
                {"id": E2, "text": "Continuity implies sequential continuity", "refs": ["chat:S1", "chat:S2"]},
            ]],
            ["Mastery", [
                {"id": E3, "text": "Has geometric intuition", "refs": ["chat:S2"]},
            ]],
        ],
    },
    "first_appearance_order": {
        "title": "X",
        "sections": [["S", [
            {"id": E1, "text": "a", "refs": ["chat:01", "chat:02"]},
            {"id": E2, "text": "b", "refs": ["chat:03", "chat:01"]},
        ]]],
    },
    "no_refs": {
        "title": "X",
        "sections": [["S", [{"id": E1, "text": "plain", "refs": []}]]],
    },
    "empty_section_skipped": {
        "title": "X",
        "sections": [["Empty", []], ["S", [{"id": E1, "text": "t", "refs": ["kb:one"]}]]],
    },
    "no_title": {
        "title": "",
        "sections": [["S", [{"id": E1, "text": "t", "refs": ["quiz:q:1"]}]]],
    },
    "empty": {"title": "", "sections": []},
    "five_same_ref": {
        "title": "Notebook",
        "sections": [["Concepts", [
            {"id": f"m_01HZK{i}ABCDEFGHJKMNPQRSTVWX", "text": f"fact {i}", "refs": ["notebook:3a563e6f"]}
            for i in range(5)
        ]]],
    },
    "chinese_text": {
        "title": "偏好",
        "sections": [["Preferences", [
            {"id": E4, "text": "回答用中文，多举例子", "refs": ["chat:01HZK4AAAAAAAAAAAAAAAAAAAA"]},
        ]]],
    },
}


def build_doc(struct: dict) -> "document.Document":
    doc = document.Document(title=struct["title"])
    for name, entries in struct["sections"]:
        doc.sections.append((
            name,
            [document.Entry(id=e["id"], section=name, text=e["text"], refs=list(e["refs"])) for e in entries],
        ))
    return doc


def doc_to_struct(doc: "document.Document") -> dict:
    return {
        "title": doc.title,
        "sections": [
            [name, [{"id": e.id, "text": e.text, "refs": list(e.refs)} for e in entries]]
            for name, entries in doc.sections
        ],
    }


DOC_VECTORS = []
for name, struct in DOC_STRUCTS.items():
    text = document.serialize(build_doc(struct))
    reparsed = document.parse(text)
    DOC_VECTORS.append({
        "name": name,
        "struct": struct,
        "serialized": text,
        "reparsed": doc_to_struct(reparsed),
        "reserialized": document.serialize(reparsed),
    })

# parse inputs written by hand (new format only; the old footnote format is not ported)
PARSE_INPUTS = {
    "new_format": (
        "# Notebook memory\n\n## Concepts\n"
        f"- Limit definition uses ε-δ pairing [^1] <!--{E1}-->\n"
        f"- Continuity implies sequential continuity [^1][^2] <!--{E2}-->\n\n"
        f"## Mastery\n- Has geometric intuition [^2] <!--{E3}-->\n\n---\n\n"
        "[^1]: notebook:01HZK4AAAAAAAAAAAAAAAAAAAA\n[^2]: notebook:01HZK4BBBBBBBBBBBBBBBBBBBB\n"
    ),
    "comma_markers": (
        f"# T\n\n## S\n- text [^1], [^2] <!--{E1}-->\n\n---\n\n[^1]: chat:a\n[^2]: chat:b\n"
    ),
    "unknown_label_dropped": (
        f"# T\n\n## S\n- text [^9] <!--{E1}-->\n"
    ),
    "bullet_without_anchor_ignored": (
        f"# T\n\n## S\n- no anchor here\n- anchored <!--{E1}-->\n\nrandom prose\n"
    ),
    "bullet_before_section_ignored": (
        f"# T\n- orphan <!--{E1}-->\n\n## S\n- kept <!--{E2}-->\n"
    ),
    "duplicate_ref_collapsed": (
        f"# T\n\n## S\n- t [^1][^1] <!--{E1}-->\n\n---\n\n[^1]: chat:a\n"
    ),
    "second_title_ignored": (
        f"# First\n\n# Second\n\n## S\n- t <!--{E1}-->\n"
    ),
    "empty_string": "",
    "crlf": f"# T\r\n\r\n## S\r\n- t <!--{E1}-->\r\n",
    "chinese": f"# 偏好\n\n## Preferences\n- 回答用中文 <!--{E2}-->\n",
}
PARSE_VECTORS = []
for name, md in PARSE_INPUTS.items():
    doc = document.parse(md)
    PARSE_VECTORS.append({
        "name": name,
        "input": md,
        "parsed": doc_to_struct(doc),
        "serialized": document.serialize(doc),
    })

# ---------------------------------------------------------------- ops

SAMPLE_STRUCT = DOC_STRUCTS["two_sections_shared_refs"]
LONG = "x" * 241
OK240 = "y" * 240


def run_ops(struct: dict, op_specs: list[dict]) -> dict:
    doc = build_doc(struct)
    counter = {"n": 0}

    def fake_new_entry_id() -> str:
        counter["n"] += 1
        return "m_" + "0" * 25 + CROCKFORD[counter["n"]]

    real = ops.new_entry_id
    ops.new_entry_id = fake_new_entry_id
    try:
        parsed = []
        for spec in op_specs:
            if spec["op"] == "add":
                parsed.append(ops.AddOp(spec["section"], spec["text"], list(spec["refs"])))
            elif spec["op"] == "edit":
                parsed.append(ops.EditOp(spec["target_id"], spec["new_text"], list(spec["new_refs"])))
            elif spec["op"] == "delete":
                parsed.append(ops.DeleteOp(spec["target_id"], spec["reason"]))
            else:
                class Weird:
                    pass
                parsed.append(Weird())
        report = ops.apply(doc, parsed)
    finally:
        ops.new_entry_id = real
    return {
        "accepted": report.accepted,
        "reason": report.reason,
        "entry_ids": [r.entry_id for r in report.results],
        "details": [r.detail for r in report.results],
        "doc": doc_to_struct(doc),
        "serialized": document.serialize(doc),
    }


OPS_SCENARIOS = {
    "add_new_section": [{"op": "add", "section": "Prefs", "text": "likes examples", "refs": ["chat"]}],
    "add_existing_section_two_adds": [
        {"op": "add", "section": "Concepts", "text": "one", "refs": ["chat:S9"]},
        {"op": "add", "section": "Concepts", "text": "two", "refs": ["chat:S9", "quiz:q:1"]},
    ],
    "edit_ok": [{"op": "edit", "target_id": E2, "new_text": "revised", "new_refs": ["kb:one"]}],
    "delete_ok": [{"op": "delete", "target_id": E1, "reason": "stale"}],
    "mixed_ok": [
        {"op": "delete", "target_id": E1, "reason": "superseded"},
        {"op": "edit", "target_id": E3, "new_text": "edited", "new_refs": ["chat:S2"]},
        {"op": "add", "section": "Mastery", "text": "new", "refs": ["chat:S3"]},
    ],
    "edit_then_delete_same_id": [
        {"op": "edit", "target_id": E1, "new_text": "e", "new_refs": ["chat"]},
        {"op": "delete", "target_id": E1, "reason": "stale"},
    ],
    "delete_then_edit_same_id": [
        {"op": "delete", "target_id": E1, "reason": "stale"},
        {"op": "edit", "target_id": E1, "new_text": "e", "new_refs": ["chat"]},
    ],
    "valid_add_plus_overlong_add": [
        {"op": "add", "section": "Concepts", "text": "fine", "refs": ["chat"]},
        {"op": "add", "section": "Concepts", "text": LONG, "refs": ["chat"]},
    ],
    "text_exactly_240": [{"op": "add", "section": "S", "text": OK240, "refs": ["chat"]}],
    "empty_text": [{"op": "add", "section": "S", "text": "", "refs": ["chat"]}],
    "empty_section": [{"op": "add", "section": "", "text": "t", "refs": ["chat"]}],
    "section_81_chars": [{"op": "add", "section": "s" * 81, "text": "t", "refs": ["chat"]}],
    "section_80_chars": [{"op": "add", "section": "s" * 80, "text": "t", "refs": ["chat"]}],
    "add_no_refs": [{"op": "add", "section": "S", "text": "t", "refs": []}],
    "add_bad_ref": [{"op": "add", "section": "S", "text": "t", "refs": ["not-an-id"]}],
    "add_one_bad_ref_of_two": [{"op": "add", "section": "S", "text": "t", "refs": ["chat", "nope"]}],
    "edit_missing_target": [{"op": "edit", "target_id": "m_01HZK9ABCDEFGHJKMNPQRSTVWX", "new_text": "t", "new_refs": ["chat"]}],
    "edit_malformed_target": [{"op": "edit", "target_id": "m_short", "new_text": "t", "new_refs": ["chat"]}],
    "edit_overlong": [{"op": "edit", "target_id": E1, "new_text": LONG, "new_refs": ["chat"]}],
    "edit_empty_refs": [{"op": "edit", "target_id": E1, "new_text": "t", "new_refs": []}],
    "edit_bad_ref": [{"op": "edit", "target_id": E1, "new_text": "t", "new_refs": ["zzz"]}],
    "delete_missing_target": [{"op": "delete", "target_id": "m_01HZK9ABCDEFGHJKMNPQRSTVWX", "reason": "stale"}],
    "delete_bad_reason": [{"op": "delete", "target_id": E1, "reason": "because"}],
    "delete_empty_reason": [{"op": "delete", "target_id": E1, "reason": ""}],
    "delete_each_reason": [
        {"op": "delete", "target_id": E1, "reason": "contradicted"},
        {"op": "delete", "target_id": E2, "reason": "low-signal"},
    ],
    "unknown_op": [{"op": "weird"}],
    "empty_batch": [],
}
OPS_VECTORS = [
    {"name": name, "struct": SAMPLE_STRUCT, "ops": specs, "result": run_ops(SAMPLE_STRUCT, specs)}
    for name, specs in OPS_SCENARIOS.items()
]

# ---------------------------------------------------------------- fingerprints

SHA1_CASES = [
    [],
    ["a"],
    ["a", None, "b"],
    [None, None],
    ["a", "b"],
    ["ab"],
    [{"b": 1, "a": 2}],
    [{"a": 2, "b": 1}],
    [["x", "y"], {"k": [1, 2, {"z": None}]}],
    ["中文", "ε-δ"],
    [1, 2.5, True, False],
    ["", ""],
    [{"nested": {"b": "中", "a": [3, 2, 1]}}],
]
SHA1_VECTORS = [{"parts": parts, "fingerprint": upstream_sha1(*parts)} for parts in SHA1_CASES]

# ---------------------------------------------------------------- diff

import datetime as _dt  # noqa: E402

FIXED_NOW = "2026-01-02T03:04:05+00:00"


class _FixedDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return _dt.datetime.fromisoformat(FIXED_NOW)


diff.datetime = _FixedDatetime


def change_to_dict(c) -> dict:
    return {
        "ts": c.ts, "kind": c.kind, "entity_id": c.entity_id, "label": c.label,
        "prev_fingerprint": c.prev_fingerprint, "new_fingerprint": c.new_fingerprint,
    }


DIFF_SCENARIOS = {
    "first_refresh_all_added": {"prev": {}, "curr": {"c": "fc", "a": "fa", "b": "fb"},
                                "labels": {"a": "Alpha", "b": "Beta", "c": "Gamma"}, "prev_labels": {}},
    "no_change": {"prev": {"a": "fa", "b": "fb"}, "curr": {"a": "fa", "b": "fb"},
                  "labels": {"a": "Alpha", "b": "Beta"}, "prev_labels": {"a": "Alpha", "b": "Beta"}},
    "added_only": {"prev": {"a": "fa"}, "curr": {"a": "fa", "c": "fc"},
                   "labels": {"a": "Alpha", "c": "Gamma"}, "prev_labels": {"a": "Alpha"}},
    "removed_uses_old_label": {"prev": {"a": "fa", "b": "fb"}, "curr": {"a": "fa"},
                               "labels": {"a": "Alpha"}, "prev_labels": {"a": "Alpha", "b": "Beta"}},
    "modified_only": {"prev": {"a": "fa", "b": "fb"}, "curr": {"a": "fa", "b": "fb2"},
                      "labels": {"a": "Alpha", "b": "Beta v2"}, "prev_labels": {"a": "Alpha", "b": "Beta"}},
    "mixed_sorted_by_id": {"prev": {"a": "fa", "b": "fb", "y": "fy"},
                           "curr": {"z": "fz", "b": "fb2", "c": "fc"},
                           "labels": {"z": "Zeta", "b": "Beta v2", "c": "Gamma"},
                           "prev_labels": {"a": "Alpha", "b": "Beta", "y": ""}},
    "removed_without_label_falls_back_to_id": {"prev": {"y": "fy"}, "curr": {},
                                               "labels": {}, "prev_labels": {"y": ""}},
    "removed_without_prev_label_map": {"prev": {"y": "fy"}, "curr": {},
                                       "labels": {}, "prev_labels": None},
    "empty_current_removes_everything": {"prev": {"a": "fa", "b": "fb", "c": "fc"}, "curr": {},
                                         "labels": {}, "prev_labels": {"a": "A", "b": "B", "c": "C"}},
    "added_without_label_falls_back_to_id": {"prev": {}, "curr": {"q": "fq"}, "labels": {}, "prev_labels": {}},
}
DIFF_VECTORS = []
for name, sc in DIFF_SCENARIOS.items():
    changes = diff.diff_snapshots(sc["prev"], sc["curr"], label_map=sc["labels"], prev_label_map=sc["prev_labels"])
    DIFF_VECTORS.append({"name": name, **sc, "now": FIXED_NOW, "changes": [change_to_dict(c) for c in changes]})

# ---------------------------------------------------------------- recall

NOW_ISO = "2026-03-10T12:00:00+00:00"
recall._now = lambda: _dt.datetime.fromisoformat(NOW_ISO)

_STATE: dict = {"stamps": {}, "events": {}}
fake_adapters = types.ModuleType("deeptutor.services.memory.snapshot.adapters")


def _read_stamps(surface):
    return [entity.EntityStamp(id=r["id"], label=r["label"], fingerprint="fp", ts=r["ts"])
            for r in _STATE["stamps"].get(surface, [])]


fake_adapters.read_stamps = _read_stamps
sys.modules["deeptutor.services.memory.snapshot.adapters"] = fake_adapters
sys.modules["deeptutor.services.memory.snapshot"].adapters = fake_adapters

fake_trace = types.ModuleType("deeptutor.services.memory.trace")


def _iter_since(surface, since):
    for row in _STATE["events"].get(surface, []):
        if since is not None and _dt.datetime.fromisoformat(row["ts"]) < since:
            continue
        yield types.SimpleNamespace(kind=row["kind"], payload=row["payload"], ts=row["ts"], id=row["id"])


fake_trace.iter_since = _iter_since
sys.modules["deeptutor.services.memory.trace"] = fake_trace
sys.modules["deeptutor.services.memory"].trace = fake_trace


def stamp_row(surface: str, i: str, label: str, ts) -> dict:
    return {"surface": surface, "id": i, "label": label, "ts": ts}


def day(n: int, hh: int = 0) -> str:
    return f"2026-03-{n:02d}T{hh:02d}:00:00+00:00"


RECENT_SCENARIOS = {
    "basic_order_newest_first": {
        "stamps": [stamp_row("chat", "c1", "older chat", day(5, 12)), stamp_row("quiz", "q1", "newer quiz", day(9, 12)),
                   stamp_row("kb", "k1", "newest kb", day(10, 11))],
        "kwargs": {},
    },
    "window_boundary": {
        "stamps": [stamp_row("chat", "c1", "just inside", "2026-03-03T12:00:01+00:00"),
                   stamp_row("chat", "c2", "exactly on cutoff", "2026-03-03T12:00:00+00:00"),
                   stamp_row("chat", "c3", "just outside", "2026-03-03T11:59:59+00:00")],
        "kwargs": {},
    },
    "no_timestamp_dropped": {
        "stamps": [stamp_row("chat", "c1", "no ts", ""), stamp_row("chat", "c2", "has ts", day(9))],
        "kwargs": {},
    },
    "unparseable_timestamp_dropped": {
        "stamps": [stamp_row("chat", "c1", "bad", "not a date"), stamp_row("chat", "c2", "ok", day(9))],
        "kwargs": {},
    },
    "z_suffix_and_naive_timestamps": {
        "stamps": [stamp_row("chat", "c1", "zulu", "2026-03-09T00:00:00Z"), stamp_row("chat", "c2", "naive", "2026-03-08T00:00:00")],
        "kwargs": {},
    },
    "future_timestamp_days_ago_zero": {
        "stamps": [stamp_row("chat", "c1", "future", day(12))],
        "kwargs": {},
    },
    "same_surface_same_label_keeps_newest": {
        "stamps": [stamp_row("chat", "c1", "Same", day(8)), stamp_row("chat", "c2", "same", day(9)),
                   stamp_row("chat", "c3", "SAME", day(7))],
        "kwargs": {},
    },
    "different_surface_same_label_both_kept": {
        "stamps": [stamp_row("chat", "c1", "same", day(8)), stamp_row("quiz", "q1", "same", day(9))],
        "kwargs": {},
    },
    "label_whitespace_collapsed": {
        "stamps": [stamp_row("chat", "c1", "  a\n\n b\t c  ", day(9))],
        "kwargs": {},
    },
    "label_truncated_200": {
        "stamps": [stamp_row("chat", "c1", "x" * 300, day(9))],
        "kwargs": {},
    },
    "empty_label_dropped": {
        "stamps": [stamp_row("chat", "c1", "   ", day(9)), stamp_row("chat", "c2", "kept", day(9))],
        "kwargs": {},
    },
    "limit_default_20": {
        "stamps": [stamp_row("chat", f"c{i}", f"l{i}", f"2026-03-09T{i % 24:02d}:{i % 60:02d}:00+00:00") for i in range(30)],
        "kwargs": {},
    },
    "limit_clamped_to_100": {
        "stamps": [stamp_row("chat", f"c{i}", f"l{i}", f"2026-03-{4 + i % 6:02d}T{i % 24:02d}:{i % 60:02d}:00+00:00") for i in range(120)],
        "kwargs": {"limit": 500},
    },
    "limit_zero_uses_default": {
        "stamps": [stamp_row("chat", f"c{i}", f"l{i}", f"2026-03-09T{i % 24:02d}:00:00+00:00") for i in range(25)],
        "kwargs": {"limit": 0},
    },
    "limit_negative_uses_default": {
        "stamps": [stamp_row("chat", f"c{i}", f"l{i}", f"2026-03-09T{i % 24:02d}:00:00+00:00") for i in range(25)],
        "kwargs": {"limit": -5},
    },
    "limit_none_uses_default": {
        "stamps": [stamp_row("chat", f"c{i}", f"l{i}", f"2026-03-09T{i % 24:02d}:00:00+00:00") for i in range(25)],
        "kwargs": {"limit": None},
    },
    "limit_small": {
        "stamps": [stamp_row("chat", f"c{i}", f"l{i}", f"2026-03-09T{i:02d}:00:00+00:00") for i in range(10)],
        "kwargs": {"limit": 3},
    },
    "days_zero_drops_everything_older_than_now": {
        "stamps": [stamp_row("chat", "c1", "in", day(9)), stamp_row("chat", "c2", "future", day(12))],
        "kwargs": {"days": 0},
    },
    "days_none_has_no_cutoff": {
        "stamps": [stamp_row("chat", "c1", "ancient", "2020-01-01T00:00:00+00:00")],
        "kwargs": {"days": None},
    },
    "days_negative_has_no_cutoff": {
        "stamps": [stamp_row("chat", "c1", "ancient", "2020-01-01T00:00:00+00:00")],
        "kwargs": {"days": -3},
    },
    "days_30_widens_window": {
        "stamps": [stamp_row("chat", "c1", "in", "2026-02-20T00:00:00+00:00")],
        "kwargs": {"days": 30},
    },
    "surface_filter": {
        "stamps": [stamp_row("chat", "c1", "chat one", day(9)), stamp_row("quiz", "q1", "quiz one", day(9))],
        "kwargs": {"surfaces": ["quiz"]},
    },
    "surface_filter_unknown_falls_back_to_all": {
        "stamps": [stamp_row("chat", "c1", "chat one", day(9)), stamp_row("quiz", "q1", "quiz one", day(9))],
        "kwargs": {"surfaces": ["nonsense"]},
    },
    "surface_filter_case_insensitive": {
        "stamps": [stamp_row("chat", "c1", "chat one", day(9)), stamp_row("quiz", "q1", "quiz one", day(9))],
        "kwargs": {"surfaces": [" QUIZ "]},
    },
}


def qev(i, ts, kind, payload):
    return {"id": f"kb:E{i}", "ts": ts, "kind": kind, "payload": payload}


QUERY_SCENARIOS = {
    "same_sentence_five_times": {
        "events": [qev(i, day(4 + i), "query", {"query": "渗透作用是什么"}) for i in range(5)], "kwargs": {}},
    "newest_first_and_limit": {
        "events": [qev(i, day(1 + i), "query", {"query": f"q{i}"}) for i in range(9)], "kwargs": {"limit": 3}},
    "non_query_kinds_ignored": {
        "events": [qev(1, day(9), "other", {"query": "x"}), qev(2, day(9, 1), "query", {"query": "y"})], "kwargs": {}},
    "blank_or_missing_query_ignored": {
        "events": [qev(1, day(9), "query", {"query": "   "}), qev(2, day(9, 1), "query", {}),
                   qev(3, day(9, 2), "query", {"query": "ok"}), qev(4, day(9, 3), "query", None)], "kwargs": {}},
    "old_events_out_of_window": {
        "events": [qev(1, "2026-02-01T00:00:00+00:00", "query", {"query": "old"}), qev(2, day(9), "query", {"query": "new"})],
        "kwargs": {}},
    "days_ago_values": {
        "events": [qev(1, day(10, 6), "query", {"query": "today"}), qev(2, day(9, 11), "query", {"query": "yesterday-ish"}),
                   qev(3, day(5), "query", {"query": "five days"})], "kwargs": {}},
    "label_collapsed": {
        "events": [qev(1, day(9), "query", {"query": "  what\n is   this? "})], "kwargs": {}},
}


def hit_dicts(hits) -> list[dict]:
    return [h.to_dict() for h in hits]


RECENT_VECTORS = []
for name, sc in RECENT_SCENARIOS.items():
    _STATE["stamps"] = {}
    for row in sc["stamps"]:
        _STATE["stamps"].setdefault(row["surface"], []).append(row)
    RECENT_VECTORS.append({"name": name, "now": NOW_ISO, **sc, "hits": hit_dicts(recall.recent(**sc["kwargs"]))})

QUERY_VECTORS = []
for name, sc in QUERY_SCENARIOS.items():
    _STATE["events"] = {"kb": list(sc["events"])}
    QUERY_VECTORS.append({"name": name, "now": NOW_ISO, **sc, "hits": hit_dicts(recall.recent_queries(**sc["kwargs"]))})

DAYS_AGO_CASES = [
    {"ts": ts, "days_ago": recall.days_ago(ts)}
    for ts in ["", "junk", "2026-03-10T12:00:00+00:00", "2026-03-09T12:00:01+00:00", "2026-03-09T11:59:59+00:00",
               "2026-03-12T00:00:00+00:00", "2026-03-01T00:00:00Z", "2026-03-01T00:00:00"]
]

out = {
    "upstream": {
        "package_version": (DT_ROOT / "deeptutor" / "__version__.py").read_text(encoding="utf-8").split('"')[1],
        "source_sha256": {f: sha256(f) for f in SOURCE_FILES},
    },
    "ids": ID_VECTORS,
    "documents": DOC_VECTORS,
    "parse": PARSE_VECTORS,
    "ops": OPS_VECTORS,
    "sha1": SHA1_VECTORS,
    "diff": DIFF_VECTORS,
    "recent": RECENT_VECTORS,
    "recent_queries": QUERY_VECTORS,
    "days_ago": DAYS_AGO_CASES,
}
Path(__file__).with_name("memory_vectors.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
)
print("wrote memory_vectors.json")
