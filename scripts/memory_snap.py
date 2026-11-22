"""Snapshot entities, fingerprints, state diff and recall shaping.

Pure library (no file or network access, no CLI). Ported from DeepTutor 1.6.15
``deeptutor/services/memory/{snapshot/entity,snapshot/diff,recall}.py`` and the
``_sha1`` helper of ``snapshot/adapters.py`` (Apache-2.0, see NOTICE). Checked
against vectors from the original code (tests/golden/memory_vectors.json).

Differences from upstream:
* the dataclasses drop ``slots=True`` (upstream needs Python >= 3.10);
* the clock is injectable (``now=``) instead of read inside the function;
* ``recent`` / ``recent_queries`` become shaping functions over data the caller
  has already read (stamps and trace events) - this module never touches files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from memory_doc import SURFACES

# ---------------------------------------------------------------- entities


@dataclass
class Entity:
    id: str
    label: str
    ts: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntityStamp:
    """Identity and date of an entity without its content."""

    id: str
    label: str
    fingerprint: str
    ts: str = ""


@dataclass
class ChangeEntry:
    ts: str
    kind: str  # added | modified | removed
    entity_id: str
    label: str
    prev_fingerprint: Optional[str] = None
    new_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fingerprint(*parts: object) -> str:
    """First 16 hex chars of sha1 over the parts (None skipped, dict/list as sorted JSON)."""
    h = hashlib.sha1()
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (dict, list)):
            blob = json.dumps(part, sort_keys=True, ensure_ascii=False)
        else:
            blob = str(part)
        h.update(blob.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- diff


def _iso(now) -> str:
    if now is None:
        return datetime.now(tz=timezone.utc).isoformat()
    if isinstance(now, datetime):
        return now.isoformat()
    return str(now)


def diff_snapshots(
    prev: Dict[str, str],
    curr: Dict[str, str],
    *,
    label_map: Dict[str, str],
    prev_label_map: Optional[Dict[str, str]] = None,
    now=None,
) -> List[ChangeEntry]:
    """Changes moving ``prev`` to ``curr`` (both ``{entity_id: fingerprint}``).

    Order: all ``added``, then all ``removed``, then all ``modified``, each sorted by id.
    """
    ts = _iso(now)
    out = []  # type: List[ChangeEntry]
    prev_keys = set(prev)
    curr_keys = set(curr)

    for entity_id in sorted(curr_keys - prev_keys):
        out.append(
            ChangeEntry(
                ts=ts, kind="added", entity_id=entity_id,
                label=label_map.get(entity_id, entity_id),
                prev_fingerprint=None, new_fingerprint=curr[entity_id],
            )
        )
    for entity_id in sorted(prev_keys - curr_keys):
        prior_label = (prev_label_map or {}).get(entity_id, "") or entity_id
        out.append(
            ChangeEntry(
                ts=ts, kind="removed", entity_id=entity_id, label=prior_label,
                prev_fingerprint=prev[entity_id], new_fingerprint=None,
            )
        )
    for entity_id in sorted(prev_keys & curr_keys):
        if prev[entity_id] != curr[entity_id]:
            out.append(
                ChangeEntry(
                    ts=ts, kind="modified", entity_id=entity_id,
                    label=label_map.get(entity_id, entity_id),
                    prev_fingerprint=prev[entity_id], new_fingerprint=curr[entity_id],
                )
            )
    return out


# ---------------------------------------------------------------- recall shaping

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_DAYS = 7
MAX_LABEL_CHARS = 200
QUERY_KINDS = {"kb": "query"}


@dataclass(frozen=True)
class RecallHit:
    surface: str
    label: str
    ts: str
    days_ago: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return {"surface": self.surface, "label": self.label, "ts": self.ts, "days_ago": self.days_ago}


def parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def days_ago(ts: str, *, now: Optional[datetime] = None) -> Optional[int]:
    """Whole days between ``ts`` and ``now``; ``None`` for an unusable stamp; future reads 0."""
    parsed = parse_ts(ts)
    if parsed is None:
        return None
    delta = (now or datetime.now(tz=timezone.utc)) - parsed
    return max(0, delta.days)


def cutoff(days: Optional[int], now: datetime) -> Optional[datetime]:
    if days is None or days < 0:
        return None
    return now - timedelta(days=days)


def clean_label(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:MAX_LABEL_CHARS]


def resolve_surfaces(surfaces: Optional[Iterable[str]]) -> Tuple[str, ...]:
    if not surfaces:
        return SURFACES
    wanted = {str(s).strip().lower() for s in surfaces}
    resolved = tuple(s for s in SURFACES if s in wanted)
    return resolved or SURFACES


def clamp_limit(limit: Optional[int]) -> int:
    if not limit or limit < 1:
        return DEFAULT_LIMIT
    return min(int(limit), MAX_LIMIT)


def _newest_first(hits: List[RecallHit]) -> List[RecallHit]:
    # String sort on the timestamp, exactly as upstream does.
    hits.sort(key=lambda hit: hit.ts, reverse=True)
    seen = set()  # type: set
    deduped = []  # type: List[RecallHit]
    for hit in hits:
        key = (hit.surface, hit.label.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


def shape_recent(
    stamps_by_surface: Dict[str, List[Any]],
    *,
    now: datetime,
    days: Optional[int] = DEFAULT_DAYS,
    limit: Optional[int] = DEFAULT_LIMIT,
    surfaces: Optional[Iterable[str]] = None,
) -> List[RecallHit]:
    """``recent``: stamps (objects with ``label`` and ``ts``) -> newest-first hits."""
    since = cutoff(days, now)
    hits = []  # type: List[RecallHit]
    for surface in resolve_surfaces(surfaces):
        for stamp in stamps_by_surface.get(surface, []):
            parsed = parse_ts(stamp.ts)
            if parsed is None or (since is not None and parsed < since):
                continue
            label = clean_label(stamp.label)
            if not label:
                continue
            hits.append(RecallHit(surface=surface, label=label, ts=stamp.ts, days_ago=days_ago(stamp.ts, now=now)))
    return _newest_first(hits)[: clamp_limit(limit)]


def shape_queries(
    events_by_surface: Dict[str, List[Any]],
    *,
    now: datetime,
    days: Optional[int] = DEFAULT_DAYS,
    limit: Optional[int] = DEFAULT_LIMIT,
) -> List[RecallHit]:
    """``recent_queries``: trace events (``kind``, ``payload``, ``ts``) -> newest-first hits.

    The caller has already applied the time window when reading (events older than the
    cutoff are not passed in); this function only filters kind and blank queries.
    """
    hits = []  # type: List[RecallHit]
    for surface, kind in QUERY_KINDS.items():
        for event in events_by_surface.get(surface, []):
            if event.kind != kind:
                continue
            label = clean_label(str((event.payload or {}).get("query") or ""))
            if not label:
                continue
            hits.append(RecallHit(surface=surface, label=label, ts=event.ts, days_ago=days_ago(event.ts, now=now)))
    return _newest_first(hits)[: clamp_limit(limit)]

