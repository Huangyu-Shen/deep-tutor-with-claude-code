"""Memory identifiers, L2/L3 Markdown documents and atomic batch operations.

Pure library (no file or network access, no CLI). Ported from DeepTutor 1.6.15
``deeptutor/services/memory/{ids,document,ops}.py`` (Apache-2.0, see NOTICE);
every behavior is checked against vectors produced by the original code
(tests/golden/memory_vectors.json, tests/test_golden_memory.py).

Deliberate differences from upstream:
* ``parse`` reads only the current footnote format
  (``- text [^1], [^2] <!--m_ID-->`` plus ``[^1]: ref`` rows). The old
  ``[^m_ID]`` format is not read: this plugin never wrote it.
* ``new_ulid`` / ``new_entry_id`` / ``new_trace_id`` accept ``now_ms`` and
  ``rand`` so tests are deterministic; ``apply`` accepts ``new_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import time
from typing import List, Optional, Tuple, Union

# ---------------------------------------------------------------- identifiers

SURFACES = ("chat", "notebook", "quiz", "kb", "book", "partner", "cowriter")

_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford
_ULID_TS_LEN = 10
_ULID_RAND_LEN = 16

_ENTRY_RE = re.compile(r"^m_[0-9A-HJKMNP-TV-Z]{26}\Z")
_TRACE_RE = re.compile(r"^[a-z][a-z0-9_-]*:[0-9A-HJKMNP-TV-Z]{26}\Z")
_SNAPSHOT_RE = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.:\-]+\Z")
_SHORTNAME_REFS = frozenset(SURFACES)


def _encode(n: int, length: int) -> str:
    chars = []  # type: List[str]
    for _ in range(length):
        chars.append(_BASE32[n & 0x1F])
        n >>= 5
    return "".join(reversed(chars))


def new_ulid(now_ms: Optional[int] = None, rand: Optional[int] = None) -> str:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    ts = now_ms & ((1 << (_ULID_TS_LEN * 5)) - 1)
    if rand is None:
        rand = secrets.randbits(_ULID_RAND_LEN * 5)
    rand &= (1 << (_ULID_RAND_LEN * 5)) - 1
    return _encode(ts, _ULID_TS_LEN) + _encode(rand, _ULID_RAND_LEN)


def new_entry_id(now_ms: Optional[int] = None, rand: Optional[int] = None) -> str:
    return "m_" + new_ulid(now_ms, rand)


def new_trace_id(surface: str, now_ms: Optional[int] = None, rand: Optional[int] = None) -> str:
    return surface + ":" + new_ulid(now_ms, rand)


def is_entry_id(s: str) -> bool:
    return bool(_ENTRY_RE.match(s))


def is_trace_id(s: str) -> bool:
    return bool(_TRACE_RE.match(s))


def is_snapshot_ref(s: str) -> bool:
    return bool(_SNAPSHOT_RE.match(s))


def is_shortname_ref(s: str) -> bool:
    """Bare surface name: the form used by L3 refs (whitelist)."""
    return s in _SHORTNAME_REFS


def is_valid_ref(s: str) -> bool:
    return is_entry_id(s) or is_trace_id(s) or is_snapshot_ref(s) or is_shortname_ref(s)


# ---------------------------------------------------------------- documents

_ENTRY_ID_RE = r"m_[0-9A-HJKMNP-TV-Z]{26}"
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(
    r"^\s*-\s+(?P<text>.*?)(?P<markers>(?:\s*,?\s*\[\^[^\]]+\])*)\s*<!--\s*(?P<id>"
    + _ENTRY_ID_RE
    + r")\s*-->\s*$"
)
_FOOTNOTE_RE = re.compile(r"^\[\^(?P<label>[^\]]+)\]:\s*(?P<ref>.*?)\s*$")
_MARKER_RE = re.compile(r"\[\^([^\]]+)\]")


@dataclass
class Entry:
    id: str
    section: str
    text: str
    refs: List[str] = field(default_factory=list)


@dataclass
class Document:
    title: str = ""
    sections: List[Tuple[str, List[Entry]]] = field(default_factory=list)

    def all_entries(self) -> List[Entry]:
        return [e for _, entries in self.sections for e in entries]

    def find(self, entry_id: str) -> Optional[Entry]:
        for _, entries in self.sections:
            for entry in entries:
                if entry.id == entry_id:
                    return entry
        return None

    def section_entries(self, name: str) -> List[Entry]:
        """Entry list for ``name``; creates the section when absent."""
        for section, entries in self.sections:
            if section == name:
                return entries
        new_entries = []  # type: List[Entry]
        self.sections.append((name, new_entries))
        return new_entries

    def remove(self, entry_id: str) -> bool:
        for _, entries in self.sections:
            for i, entry in enumerate(entries):
                if entry.id == entry_id:
                    del entries[i]
                    return True
        return False


def parse(md: str) -> Document:
    """Parse a memory Markdown document (current footnote format only)."""
    raw_lines = md.splitlines()

    ref_by_label = {}  # type: dict
    for raw in raw_lines:
        m = _FOOTNOTE_RE.match(raw.rstrip())
        if m:
            label = m.group("label")
            if label.startswith("m_"):
                continue
            ref_by_label[label] = m.group("ref").strip()

    doc = Document()
    current_entries = None  # type: Optional[List[Entry]]
    current_section = None  # type: Optional[str]
    for raw in raw_lines:
        line = raw.rstrip()

        if not doc.title:
            m_title = _TITLE_RE.match(line)
            if m_title:
                doc.title = m_title.group(1).strip()
                continue

        m_section = _SECTION_RE.match(line)
        if m_section:
            current_section = m_section.group(1).strip()
            current_entries = []
            doc.sections.append((current_section, current_entries))
            continue

        m_bullet = _BULLET_RE.match(line)
        if m_bullet and current_entries is not None and current_section is not None:
            entry_refs = []  # type: List[str]
            for marker in _MARKER_RE.findall(m_bullet.group("markers") or ""):
                ref = ref_by_label.get(marker)
                if ref is not None and ref not in entry_refs:
                    entry_refs.append(ref)
            current_entries.append(
                Entry(
                    id=m_bullet.group("id"),
                    section=current_section,
                    text=m_bullet.group("text").rstrip(),
                    refs=entry_refs,
                )
            )
    return doc


def unparsed_lines(md: str) -> List[str]:
    """Non-blank lines that :func:`parse` would drop (so a rewrite from the parsed document
    would silently erase them): anything that is not the title, a section heading, an
    anchored bullet under a section, a footnote definition or the ``---`` separator."""
    title_seen = False
    in_section = False
    lost = []  # type: List[str]
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip() == "---" or _FOOTNOTE_RE.match(line):
            continue
        if not title_seen and _TITLE_RE.match(line):
            title_seen = True
            continue
        if _SECTION_RE.match(line):
            in_section = True
            continue
        if in_section and _BULLET_RE.match(line):
            continue
        lost.append(line)
    return lost


def serialize(doc: Document) -> str:
    """Render ``doc``; each distinct ref gets one footnote, numbered by first appearance."""
    ref_order = []  # type: List[str]
    ref_to_label = {}  # type: dict
    for entry in doc.all_entries():
        for ref in entry.refs:
            if ref in ref_to_label:
                continue
            ref_to_label[ref] = len(ref_order) + 1
            ref_order.append(ref)

    lines = []  # type: List[str]
    if doc.title:
        lines.append("# " + doc.title)
        lines.append("")

    for section, entries in doc.sections:
        if not entries:
            continue
        lines.append("## " + section)
        lines.append("")
        for entry in entries:
            markers = ", ".join("[^%d]" % ref_to_label[r] for r in entry.refs if r in ref_to_label)
            text = entry.text.rstrip()
            if markers:
                lines.append("- %s %s <!--%s-->" % (text, markers, entry.id))
            else:
                lines.append("- %s <!--%s-->" % (text, entry.id))
        lines.append("")

    if ref_order:
        lines.append("---")
        lines.append("")
        for i, ref in enumerate(ref_order, start=1):
            lines.append("[^%d]: %s" % (i, ref))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- operations

MAX_TEXT_LEN = 240
MAX_SECTION_LEN = 80
DELETE_REASONS = frozenset({"contradicted", "superseded", "stale", "low-signal"})


@dataclass(frozen=True)
class AddOp:
    section: str
    text: str
    refs: List[str]
    op: str = "add"


@dataclass(frozen=True)
class EditOp:
    target_id: str
    new_text: str
    new_refs: List[str]
    op: str = "edit"


@dataclass(frozen=True)
class DeleteOp:
    target_id: str
    reason: str
    op: str = "delete"


Op = Union[AddOp, EditOp, DeleteOp]


@dataclass
class OpResult:
    op: Op
    status: str = "applied"
    entry_id: Optional[str] = None  # set for add ops
    detail: str = ""


@dataclass
class ApplyReport:
    accepted: bool
    results: List[OpResult] = field(default_factory=list)
    reason: str = ""


class OpValidationError(Exception):
    """A batch failed validation; nothing was applied."""


def _one_line(value: str) -> bool:
    """Entry text and section names must fit on one Markdown line, and must not carry the
    id-anchor marker, or a re-parse would split or lose the entry (deviation from upstream,
    which accepts them)."""
    return not any(ch in value for ch in "\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029") \
        and "<!--" not in value and "-->" not in value


def _validate(doc: Document, ops: List[Op]) -> None:
    edits = set()  # type: set
    deletes = set()  # type: set

    for op in ops:
        if isinstance(op, AddOp):
            if not op.text or len(op.text) > MAX_TEXT_LEN:
                raise OpValidationError(
                    "add: text length must be 1..%d (got %d)" % (MAX_TEXT_LEN, len(op.text))
                )
            if not op.section or len(op.section) > MAX_SECTION_LEN or not _one_line(op.section):
                raise OpValidationError("add: invalid section %r" % (op.section,))
            if not _one_line(op.text):
                raise OpValidationError("add: text must be a single line without comment markers")
            if not op.refs:
                raise OpValidationError("add: refs must be non-empty")
            for ref in op.refs:
                if not is_valid_ref(ref):
                    raise OpValidationError("add: malformed ref %r" % (ref,))
        elif isinstance(op, EditOp):
            if not is_entry_id(op.target_id):
                raise OpValidationError("edit: malformed target_id %r" % (op.target_id,))
            if doc.find(op.target_id) is None:
                raise OpValidationError("edit: target_id %s not found" % op.target_id)
            if not op.new_text or len(op.new_text) > MAX_TEXT_LEN:
                raise OpValidationError(
                    "edit: text length must be 1..%d (got %d)" % (MAX_TEXT_LEN, len(op.new_text))
                )
            if not _one_line(op.new_text):
                raise OpValidationError("edit: text must be a single line without comment markers")
            if not op.new_refs:
                raise OpValidationError("edit: refs must be non-empty")
            for ref in op.new_refs:
                if not is_valid_ref(ref):
                    raise OpValidationError("edit: malformed ref %r" % (ref,))
            if op.target_id in deletes:
                raise OpValidationError("batch conflict: edit and delete on same id %s" % op.target_id)
            edits.add(op.target_id)
        elif isinstance(op, DeleteOp):
            if not is_entry_id(op.target_id):
                raise OpValidationError("delete: malformed target_id %r" % (op.target_id,))
            if doc.find(op.target_id) is None:
                raise OpValidationError("delete: target_id %s not found" % op.target_id)
            if op.reason not in DELETE_REASONS:
                raise OpValidationError("delete: reason must be one of %s" % sorted(DELETE_REASONS))
            if op.target_id in edits:
                raise OpValidationError("batch conflict: edit and delete on same id %s" % op.target_id)
            deletes.add(op.target_id)
        else:
            raise OpValidationError("unknown op %s" % type(op).__name__)


def apply(doc: Document, ops: List[Op], new_id=None) -> ApplyReport:
    """Apply ``ops`` as one atomic batch. Mutates ``doc`` only when every op is valid.

    ``new_id`` (optional) supplies entry ids for add ops; the default is
    :func:`new_entry_id`.
    """
    try:
        _validate(doc, ops)
    except OpValidationError as exc:
        return ApplyReport(accepted=False, reason=str(exc))

    make_id = new_id or new_entry_id
    results = []  # type: List[OpResult]
    for op in ops:
        if isinstance(op, AddOp):
            entry_id = make_id()
            doc.section_entries(op.section).append(
                Entry(id=entry_id, section=op.section, text=op.text, refs=list(op.refs))
            )
            results.append(OpResult(op=op, entry_id=entry_id))
        elif isinstance(op, EditOp):
            entry = doc.find(op.target_id)
            assert entry is not None  # _validate checked this
            entry.text = op.new_text
            entry.refs = list(op.new_refs)
            results.append(OpResult(op=op))
        else:
            doc.remove(op.target_id)
            results.append(OpResult(op=op, detail=op.reason))
    return ApplyReport(accepted=True, results=results)
