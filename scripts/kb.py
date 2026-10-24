"""Page-indexed knowledge base for course materials.

Stored in <root>/.tutor/kb/<name>/index.sqlite (SQLite FTS5, standard library).
No embeddings and no model calls: Claude rewrites queries and reads the hits.

Every unit of text keeps its place (PDF page, slide number, or document
section) so explanations can cite it and `verify` can check quotes against it.

Commands (JSON on stdout; failures give {"ok": false, "error": ...}, exit 1):
  ingest PATH... [--scanned] add files or folders; reports what was and was not read
  status                     per-source units, empty units, pages awaiting transcription
  search QUERY [--query Q]   ranked pages with snippets (and page text)
  get --source S --page N    one unit's full text
  render --source S --pages 1,3-5   render PDF pages to PNG for Claude to read
  register --source S --page N      store Claude's transcription (text on stdin)
  verify                     check quotes against the stored text (JSON on stdin)
  remove --source S          drop a source and its cached images
  list                       knowledge bases under this root

External tools: pdftotext/pdftoppm/pdfimages (poppler) for PDFs, markitdown for
pptx/docx/xlsx. Missing tools produce an error that says what to install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata

MIN_UNIT_CHARS = 20  # fewer non-space characters than this = no usable text
IMAGE_PAGE_TEXT_MAX = 100  # a page with a big image and less text than this counts as "low text"
SCAN_RATIO = 0.8  # a PDF is a scan only if at least this share of its pages are low-text (a picture-heavy deck reached 0.63)
BIG_IMAGE_PX = 400
MIN_QUOTE_WEIGHT = 15  # CJK characters count double: they carry more per character
MIN_SEGMENT_WEIGHT = 5
RENDER_DPI = 130
TEXT_CAP = 3000

PDF_EXT = {".pdf"}
MARKITDOWN_EXT = {".pptx", ".docx", ".xlsx"}
PLAIN_EXT = {".md", ".markdown", ".txt"}
SUPPORTED = PDF_EXT | MARKITDOWN_EXT | PLAIN_EXT

CJK_RANGE = "぀-ヿ㐀-鿿가-힯"
CJK = re.compile(f"[{CJK_RANGE}]")
CJK_GAP = re.compile(f"(?<=[{CJK_RANGE}])\\s+(?=[{CJK_RANGE}])")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, display TEXT NOT NULL, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, kind TEXT NOT NULL, unit TEXT NOT NULL,
  units INTEGER NOT NULL, ingested_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pages (
  source_id TEXT NOT NULL, num INTEGER NOT NULL, origin TEXT NOT NULL, text TEXT NOT NULL,
  alt_text TEXT,
  PRIMARY KEY (source_id, num));
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
  text, source_id UNINDEXED, num UNINDEXED, tokenize = 'unicode61 remove_diacritics 2');
"""
# pages.rowid is also the rowid of its pages_fts row.
# alt_text: the same PDF page in reading order (pdftotext without -layout). Used only by `verify`,
# because layout mode interleaves the columns of two-column slides and breaks real quotes.
# NULL = never extracted, '' = extraction was not possible, so it is not retried.
# origin: text-layer | markitdown | plain | ai-transcription | needs-transcription | empty


class KbError(Exception):
    pass


_OPEN: list[sqlite3.Connection] = []  # closed at the end of main()


# --------------------------------------------------------------------- paths


def check_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name or "") or set(name) == {"."}:
        raise KbError("kb name may contain only letters, digits, _ . - and must not start with a dot")
    return name


def kb_dir(root: str, name: str) -> Path:
    return Path(root) / ".tutor" / "kb" / check_name(name)


def connect(root: str, name: str, create: bool = False) -> sqlite3.Connection:
    directory = kb_dir(root, name)
    db = directory / "index.sqlite"
    if not db.exists():
        if not create:
            raise KbError(f"knowledge base {name!r} does not exist (run ingest first)")
        directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    _OPEN.append(conn)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if "alt_text" not in {r[1] for r in conn.execute("PRAGMA table_info(pages)")}:  # database from 0.1.0 to 0.1.2
        try:
            conn.execute("ALTER TABLE pages ADD COLUMN alt_text TEXT")
            conn.commit()
        except sqlite3.OperationalError as exc:  # another process migrated it first
            if "duplicate column" not in str(exc):
                raise
    return conn


# ---------------------------------------------------------------- extraction


def run_tool(cmd: list[str], hint: str, timeout: int = 300) -> str:
    if shutil.which(cmd[0]) is None:
        raise KbError(f"{cmd[0]} not found. {hint}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise KbError(f"{cmd[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise KbError(f"{cmd[0]} failed: {proc.stderr.strip()[:300] or 'exit ' + str(proc.returncode)}")
    return proc.stdout


POPPLER_HINT = "Install poppler (macOS: brew install poppler)."


def _text_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _has_text(text: str) -> bool:
    return _text_len(text) >= MIN_UNIT_CHARS


def parse_pdfimages_list(out: str) -> set[int]:
    """Pages that carry a big image, from `pdfimages -list` output."""
    pages: set[int] = set()
    for line in out.splitlines()[2:]:
        parts = line.split()
        if (len(parts) >= 5 and parts[0].isdigit() and parts[2] == "image"
                and parts[3].isdigit() and parts[4].isdigit()
                and int(parts[3]) >= BIG_IMAGE_PX and int(parts[4]) >= BIG_IMAGE_PX):
            pages.add(int(parts[0]))
    return pages


def _split_pages(out: str) -> list[str]:
    pages = out.split("\f")
    if pages and pages[-1].strip() == "":
        pages = pages[:-1]
    return pages


def extract_pdf(path: Path, force_scanned: bool = False):
    """Split a PDF into pages and decide what each page is.

    A "low-text" page has almost no text layer (or a big image and only a line of text).
    In a scanned document nearly every page is low-text, and those pages need transcribing.
    In a slide deck or paper a minority of pages are photos, diagrams or title slides: their
    text layer (if any) is kept and a page with no text at all is `empty` (not indexed),
    because transcribing a photo adds nothing. `force_scanned` overrides the ratio.
    """
    out = run_tool(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], POPPLER_HINT)
    pages = _split_pages(out)
    alt_pages = _split_pages(run_tool(["pdftotext", "-enc", "UTF-8", str(path), "-"], POPPLER_HINT))
    reading_order_ok = len(alt_pages) == len(pages)
    if not reading_order_ok:
        alt_pages = [""] * len(pages)  # cannot line the two extractions up page by page
    try:  # without pdfimages we only lose the "scan with a text footer" check
        image_pages = parse_pdfimages_list(run_tool(["pdfimages", "-list", str(path)], POPPLER_HINT))
    except KbError:
        image_pages = set()
    low = [not _has_text(t) or (i in image_pages and _text_len(t) < IMAGE_PAGE_TEXT_MAX)
           for i, t in enumerate(pages, 1)]
    ratio = sum(low) / len(pages) if pages else 0.0
    scanned = bool(pages) and (force_scanned or ratio >= SCAN_RATIO)
    notes = {"low_text_pages": [i for i, flag in enumerate(low, 1) if flag],
             "low_text_ratio": round(ratio, 2), "treated_as_scan": scanned}
    if not reading_order_ok:
        notes["reading_order_unavailable"] = True  # verify then relies on layout text only
    units = []
    alt: dict[int, str] = {}
    for i, (text, is_low) in enumerate(zip(pages, low), 1):
        if scanned and is_low:
            units.append((i, "", "needs-transcription"))
        elif _text_len(text) > 0:
            units.append((i, text.strip("\n"), "text-layer"))
            alt[i] = alt_pages[i - 1].strip("\n")
        else:
            units.append((i, "", "empty"))
    notes["_alt_texts"] = alt  # stored for verify; not part of the report
    return "page", units, notes


SLIDE_MARK = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->")


def extract_markitdown(path: Path):
    out = run_tool(["markitdown", str(path)],
                   "Install markitdown (e.g. uv tool install 'markitdown[all]' or pip install 'markitdown[all]').")
    if path.suffix.lower() == ".pptx":
        parts = SLIDE_MARK.split(out)
        # parts = [preamble, n1, body1, n2, body2, ...]
        units = []
        for j in range(1, len(parts) - 1, 2):
            body = parts[j + 1].strip()
            units.append((int(parts[j]), body, "markitdown" if _has_text(body) else "empty"))
        if not units:
            raise KbError("no slides found in markitdown output")
        return "slide", units
    return "section", split_sections(out, "markitdown")


def split_sections(text: str, origin: str):
    """Split Markdown on level-1/2 headings; a heading-less file is one section."""
    chunks, current = [], []
    for line in text.splitlines():
        # a heading starts a new section only once the previous one has a body (no heading-only stubs)
        if re.match(r"#{1,2}\s", line) and any(l.strip() and not l.lstrip().startswith("#") for l in current):
            chunks.append("\n".join(current).strip())
            current = []
        current.append(line)
    if any(l.strip() for l in current):
        chunks.append("\n".join(current).strip())
    return [(i, c, origin if _has_text(c) else "empty") for i, c in enumerate(chunks, 1)] or [
        (1, "", "empty")
    ]


def extract_plain(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise KbError("file is not valid UTF-8 text") from exc
    return "section", split_sections(text, "plain")


def extract(path: Path, force_scanned: bool = False):
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        return "pdf", *extract_pdf(path, force_scanned)
    if ext in MARKITDOWN_EXT:
        return ext[1:], *extract_markitdown(path), {}
    return "text", *extract_plain(path), {}


# ------------------------------------------------------------------- ingest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_source_id(conn, path: Path, digest: str) -> str:
    row = conn.execute("SELECT source_id FROM sources WHERE path = ?", (str(path),)).fetchone()
    if row:
        return row["source_id"]
    slug = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-")
    if len(slug) < 3:  # e.g. a Chinese file name leaves only "md"
        slug = f"src-{digest[:8]}"
    if conn.execute("SELECT 1 FROM sources WHERE source_id = ?", (slug,)).fetchone():
        slug = f"{slug}-{hashlib.sha256(str(path).encode()).hexdigest()[:8]}"
    return slug


def delete_source_pages(conn, source_id: str) -> None:
    conn.execute("DELETE FROM pages_fts WHERE rowid IN (SELECT rowid FROM pages WHERE source_id = ?)", (source_id,))
    conn.execute("DELETE FROM pages WHERE source_id = ?", (source_id,))


def insert_unit(conn, source_id: str, num: int, origin: str, text: str, alt: str | None = None) -> None:
    row = conn.execute("SELECT rowid FROM pages WHERE source_id = ? AND num = ?", (source_id, num)).fetchone()
    if row:
        rid = row[0]
        conn.execute("UPDATE pages SET origin = ?, text = ?, alt_text = ? WHERE rowid = ?", (origin, text, alt, rid))
        conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (rid,))
    else:
        rid = conn.execute("INSERT INTO pages (source_id, num, origin, text, alt_text) VALUES (?,?,?,?,?)",
                           (source_id, num, origin, text, alt)).lastrowid
    conn.execute("INSERT INTO pages_fts (rowid, text, source_id, num) VALUES (?,?,?,?)", (rid, text, source_id, num))


def collect_files(paths: list[str]):
    """Return (files, skipped); skipped = (path, named_explicitly)."""
    files, skipped = [], []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file() or any(part.startswith(".") for part in f.relative_to(p).parts):
                    continue
                if f.suffix.lower() in SUPPORTED:
                    files.append(f)
                else:
                    skipped.append((f, False))
        elif p.is_file() and p.suffix.lower() in SUPPORTED:
            files.append(p)
        else:
            skipped.append((p, True))
    return files, skipped


def unit_report(conn, sid: str) -> dict:
    def nums(origin):
        return [r[0] for r in conn.execute(
            "SELECT num FROM pages WHERE source_id = ? AND origin = ? ORDER BY num", (sid, origin))]
    return {"needs_transcription": nums("needs-transcription"), "empty_units": nums("empty"),
            "ai_transcribed": nums("ai-transcription")}


def _needs_alt_refresh(conn, row) -> bool:
    """A PDF indexed before 0.1.3 has no reading-order text yet; re-read it once (transcriptions are kept)."""
    return row["kind"] == "pdf" and conn.execute(
        "SELECT 1 FROM pages WHERE source_id = ? AND origin = 'text-layer' AND alt_text IS NULL LIMIT 1",
        (row["source_id"],)).fetchone() is not None


def prepare_file(conn, path: Path, force_scanned: bool = False) -> dict:
    """Read and extract outside any write transaction (extraction can take minutes)."""
    path = path.resolve()
    info = {"path": str(path), "display": path.name, "digest": sha256_file(path)}
    row = conn.execute("SELECT * FROM sources WHERE path = ?", (str(path),)).fetchone()
    if row and row["sha256"] == info["digest"] and not force_scanned and not _needs_alt_refresh(conn, row):
        return {**info, "action": "unchanged", "row": row}
    if not row:
        twin = conn.execute("SELECT source_id, path FROM sources WHERE sha256 = ?", (info["digest"],)).fetchone()
        if twin:
            return {**info, "action": "duplicate", "duplicate_of": twin["source_id"], "duplicate_path": twin["path"]}
    kind, unit, units, notes = extract(path, force_scanned)
    alt = notes.pop("_alt_texts", {})
    return {**info, "action": "write", "row": row, "kind": kind, "unit": unit, "units": units, "notes": notes,
            "alt": alt}


def apply_prepared(conn, root: str, kb: str, prep: dict) -> dict:
    report = {"path": prep["path"], "display": prep["display"]}
    if prep["action"] == "duplicate":
        report.update(status="duplicate", duplicate_of=prep["duplicate_of"],
                      note=f"identical content is already indexed from {prep['duplicate_path']}; "
                           "use `remove` first if you want the new path instead")
        return report
    if prep["action"] == "unchanged":
        row = prep["row"]
        report.update(source_id=row["source_id"], status="unchanged", unit=row["unit"], units=row["units"])
        report.update(unit_report(conn, row["source_id"]))
        return report
    path = Path(prep["path"])
    row = prep["row"]
    sid = make_source_id(conn, path, prep["digest"])
    kept: dict[int, str] = {}
    if row:
        same_content = row["sha256"] == prep["digest"]  # re-read only because of --scanned
        ai_pages = {r["num"]: r["text"] for r in conn.execute(
            "SELECT num, text FROM pages WHERE source_id = ? AND origin = 'ai-transcription'", (sid,))}
        delete_source_pages(conn, sid)
        if same_content:
            kept = ai_pages  # paid-for transcriptions of identical content stay (and so do the cached images)
        else:
            shutil.rmtree(kb_dir(root, kb) / "scans" / sid, ignore_errors=True)  # cached images are stale now
            if ai_pages:
                report["dropped_transcriptions"] = len(ai_pages)
    alt = prep.get("alt", {})
    for num, text, origin in prep["units"]:
        if num in kept:
            insert_unit(conn, sid, num, "ai-transcription", kept[num])
        else:
            insert_unit(conn, sid, num, origin, text, alt.get(num))
    conn.execute(
        "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?)",
        (sid, path.name, str(path), prep["digest"], prep["kind"], prep["unit"], len(prep["units"]), time.time()),
    )
    report.update(source_id=sid, status="updated" if row else "added", unit=prep["unit"], units=len(prep["units"]))
    report.update(unit_report(conn, sid))
    if prep.get("notes"):
        report["scan_check"] = prep["notes"]  # lets the caller question a borderline ratio
    return report


def cmd_ingest(args) -> dict:
    files, skipped = collect_files(args.paths)
    conn = connect(args.root, args.kb, create=True)
    reports = []
    for f in files:
        try:
            prep = prepare_file(conn, f, args.scanned)
            with conn:  # one transaction per file: a bad file never rolls back good ones
                reports.append(apply_prepared(conn, args.root, args.kb, prep))
        except KbError as exc:
            reports.append({"path": str(f), "display": f.name, "status": "failed", "error": str(exc)})
        except Exception as exc:  # unreadable file, database trouble, ...
            reports.append({"path": str(f), "display": f.name, "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}"})
    pending = sum(len(r.get("needs_transcription", [])) for r in reports)
    return {
        "kb": args.kb,
        "files": reports,
        "skipped": [
            {"path": str(s), "named_explicitly": named, "reason": "unsupported type or not found"}
            for s, named in skipped
        ],
        "totals": {
            "added_or_updated": sum(r["status"] in ("added", "updated") for r in reports),
            "unchanged": sum(r["status"] == "unchanged" for r in reports),
            "duplicate": sum(r["status"] == "duplicate" for r in reports),
            "failed": sum(r["status"] == "failed" for r in reports),
            "skipped": len(skipped),
            "pages_needing_transcription": pending,
            "units_without_text": sum(len(r.get("empty_units", [])) for r in reports),
        },
        # Unsupported files met while walking a folder are listed but are not a problem;
        # a file the user named that we cannot read is.
        "all_ok": not any(named for _s, named in skipped)
        and all(r["status"] != "failed" for r in reports),
    }


def cmd_remove(args) -> dict:
    conn = connect(args.root, args.kb)
    src = resolve_source(conn, args.source)
    with conn:
        delete_source_pages(conn, src["source_id"])
        conn.execute("DELETE FROM sources WHERE source_id = ?", (src["source_id"],))
    shutil.rmtree(kb_dir(args.root, args.kb) / "scans" / src["source_id"], ignore_errors=True)
    return {"removed": src["source_id"], "display": src["display"]}


# ------------------------------------------------------------- status & list


def cmd_status(args) -> dict:
    conn = connect(args.root, args.kb)
    out = []
    for s in conn.execute("SELECT * FROM sources ORDER BY source_id"):
        out.append({"source_id": s["source_id"], "display": s["display"], "unit": s["unit"],
                    "units": s["units"], "path": s["path"], **unit_report(conn, s["source_id"])})
    return {"kb": args.kb, "sources": out}


def cmd_list(args) -> dict:
    base = Path(args.root) / ".tutor" / "kb"
    names = sorted(p.name for p in base.iterdir() if (p / "index.sqlite").exists()) if base.exists() else []
    return {"knowledge_bases": names}


# ------------------------------------------------------------------ lookups


def resolve_source(conn, ref: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT * FROM sources WHERE source_id = ? OR display = ? OR path = ?", (ref, ref, ref)
    ).fetchall()
    if not rows:
        raise KbError(f"unknown source {ref!r}")
    exact = [r for r in rows if r["source_id"] == ref]
    if exact:
        return exact[0]
    if len(rows) > 1:
        ids = ", ".join(r["source_id"] for r in rows)
        raise KbError(f"source name {ref!r} is ambiguous; use one of: {ids}")
    return rows[0]


def parse_pages(spec: str) -> list[int]:
    nums: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise KbError(f"bad page spec {part!r}; use e.g. 1,3-5")
        lo, hi = int(m[1]), int(m[2] or m[1])
        if hi < lo:
            raise KbError(f"bad page range {part!r}")
        nums.extend(range(lo, hi + 1))
    return sorted(set(nums))


def cmd_get(args) -> dict:
    conn = connect(args.root, args.kb)
    src = resolve_source(conn, args.source)
    row = conn.execute("SELECT * FROM pages WHERE source_id = ? AND num = ?", (src["source_id"], args.page)).fetchone()
    if not row:
        raise KbError(f"{src['display']} has no {src['unit']} {args.page} (1..{src['units']})")
    return {"source_id": src["source_id"], "display": src["display"], "unit": src["unit"],
            "num": row["num"], "origin": row["origin"], "text": row["text"]}


# ------------------------------------------------------------------- search


def fts_query(text: str, mode: str) -> str:
    words = [w for w in re.findall(r"[\w'’-]+", text, flags=re.UNICODE) if not CJK.search(w)]
    words = [w.replace('"', "") for w in words if w.strip("'’-")]
    return (" AND " if mode == "all" else " OR ").join(f'"{w}"' for w in words)


def make_snippet(text: str, needle: str, width: int = 160) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return re.sub(r"\s+", " ", text[:width]).strip()
    lo, hi = max(0, i - width // 2), min(len(text), i + len(needle) + width // 2)
    return ("… " if lo else "") + re.sub(r"\s+", " ", text[lo:hi]).strip() + (" …" if hi < len(text) else "")


def search_one(conn, query: str, mode: str, limit: int, source_id: str | None):
    hits = []
    match = fts_query(query, mode)
    if match:
        sql = ("SELECT p.source_id, p.num, p.origin, p.text, bm25(pages_fts) AS score, "
               "snippet(pages_fts, 0, '«', '»', ' … ', 30) AS snip "
               "FROM pages_fts JOIN pages p ON p.rowid = pages_fts.rowid "
               "WHERE pages_fts MATCH ?")
        params: list = [match]
        if source_id:
            sql += " AND p.source_id = ?"
            params.append(source_id)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        for r in conn.execute(sql, params):
            hits.append({"source_id": r["source_id"], "num": r["num"], "origin": r["origin"],
                         "score": -r["score"], "snippet": r["snip"], "text": r["text"]})
    cjk_terms = [t for t in re.findall(r"\S+", query) if CJK.search(t)]
    if cjk_terms:  # unicode61 cannot split CJK runs, so fall back to substring search
        seen = {(h["source_id"], h["num"]) for h in hits}
        for r in conn.execute("SELECT * FROM pages WHERE text != ''"):
            if source_id and r["source_id"] != source_id:
                continue
            found = [t for t in cjk_terms if t in r["text"]]
            if not found or (mode == "all" and len(found) != len(cjk_terms)):
                continue
            if (r["source_id"], r["num"]) not in seen:
                hits.append({"source_id": r["source_id"], "num": r["num"], "origin": r["origin"],
                             "score": float(len(found)), "snippet": make_snippet(r["text"], found[0]),
                             "text": r["text"]})
    return hits


def cmd_search(args) -> dict:
    queries = ([args.query] if args.query else []) + (args.extra or [])
    queries = [q for q in queries if q and q.strip()]
    if not queries:
        raise KbError("give at least one query")
    conn = connect(args.root, args.kb)
    source_id = resolve_source(conn, args.source)["source_id"] if args.source else None
    merged: dict[tuple, dict] = {}
    for q in queries:
        for h in search_one(conn, q, args.mode, args.limit, source_id):
            key = (h["source_id"], h["num"])
            if key not in merged or h["score"] > merged[key]["score"]:
                merged[key] = {**h, "matched_query": q}
    display = {r["source_id"]: (r["display"], r["unit"]) for r in conn.execute("SELECT * FROM sources")}
    hits = sorted(merged.values(), key=lambda h: -h["score"])[: args.limit]
    for h in hits:
        h["display"], h["unit"] = display[h["source_id"]]
        h["citation"] = f"{h['display']} {h['unit']} {h['num']}"
        h["low_confidence"] = h["origin"] == "ai-transcription"
        if args.no_text:
            h.pop("text")
        else:
            text = re.sub(r"[ \t]{2,}", " ", h["text"])  # layout padding would waste the cap
            h["text_truncated"] = len(text) > TEXT_CAP
            h["text"] = text[:TEXT_CAP]
    return {"queries": queries, "hits": hits}


# ------------------------------------------------ scanned pages (transcribe)


def cmd_render(args) -> dict:
    conn = connect(args.root, args.kb)
    src = resolve_source(conn, args.source)
    if src["kind"] != "pdf":
        raise KbError("only PDF pages can be rendered")
    pdf = Path(src["path"])
    if not pdf.exists():
        raise KbError(f"original file is gone: {pdf}")
    pages = parse_pages(args.pages)
    bad = [p for p in pages if not 1 <= p <= src["units"]]
    if bad:
        raise KbError(f"pages out of range (1..{src['units']}): {bad}")
    out_dir = kb_dir(args.root, args.kb) / "scans" / src["source_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for p in pages:
        target = out_dir / f"page-{p:04d}.png"
        if not target.exists():
            stem = out_dir / f"tmp-{p}"
            run_tool(["pdftoppm", "-r", str(RENDER_DPI), "-f", str(p), "-l", str(p), "-png",
                      "-singlefile", str(pdf), str(stem)], POPPLER_HINT)
            Path(f"{stem}.png").rename(target)
        images.append({"page": p, "image": str(target)})
    return {"source_id": src["source_id"], "images": images}


def cmd_register(args, stdin) -> dict:
    text = stdin.read().strip()
    if not _has_text(text):
        raise KbError("transcription is empty or too short")
    conn = connect(args.root, args.kb)
    src = resolve_source(conn, args.source)
    row = conn.execute("SELECT origin FROM pages WHERE source_id = ? AND num = ?",
                       (src["source_id"], args.page)).fetchone()
    if not row:
        raise KbError(f"{src['display']} has no {src['unit']} {args.page}")
    if row["origin"] not in ("needs-transcription", "ai-transcription", "empty"):
        raise KbError(f"page {args.page} already has {row['origin']} text; refusing to overwrite")
    with conn:
        insert_unit(conn, src["source_id"], args.page, "ai-transcription", text)
    remaining = unit_report(conn, src["source_id"])["needs_transcription"]
    return {"source_id": src["source_id"], "page": args.page, "origin": "ai-transcription",
            "remaining_needing_transcription": remaining}


# ------------------------------------------------------------------- verify
#
# Verification is deliberately strict. A quote is accepted only if its words
# appear on the cited page in order, on word boundaries, with these tolerances:
# whitespace, curly vs straight quotes, dash styles, ligatures and full-width
# forms (NFKC is NOT used: it would equate "mc²" with "mc2"), line-end hyphens
# from PDF layout, spaces inside CJK text. A quote cannot run across a wide gap inside one
# line (the gap between two columns or two table cells), so text from side-by-side columns is
# never joined into one "sentence". Ellipses are allowed but the words
# they skip are returned so the caller can check they do not change the meaning.

_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                            "–": "-", "—": "-", "−": "-", " ": " ", "　": " "})
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
              "ﬅ": "st", "ﬆ": "st"}
_LINE_HYPHEN = re.compile(r"(?<=\w)-[ \t]*\n[ \t]*(?=\w)")
_GAP = re.compile(r"(?<=\S)[ \t]{3,}(?=\S)")  # indentation at the start of a line is not a gap
_BLOCK = re.compile(r"\n[ \t]*\n\s*")


def _pre(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("­", "")
    out = []
    for ch in text:
        if ch in _LIGATURES:
            out.append(_LIGATURES[ch])
        elif "！" <= ch <= "～":  # full-width ASCII
            out.append(chr(ord(ch) - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _finish(text: str) -> str:
    text = text.translate(_QUOTE_MAP)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*-\s*", "-", text)  # a dash at a line end, or spaced, reads the same
    return CJK_GAP.sub("", text)


def normalize_quote(text: str) -> str:
    return _finish(_pre(text).replace("\x00", ""))  # the marker is ours; a quote must not carry it


def page_variants(text: str, *, gaps: bool = False, blocks: bool = False) -> list[str]:
    """Page text with a line-end hyphen both dropped (photo-/synthesis) and kept (well-/known).

    gaps: a wide gap inside a line separates columns or table cells (PDF layout text).
    blocks: a blank line separates text blocks, which is how reading-order text separates columns.
    Either becomes a hard boundary no quote can cross.
    """
    base = _pre(text)
    if gaps:
        base = _GAP.sub(" \x00 ", base)
    if blocks:
        base = _BLOCK.sub(" \x00 ", base)
    variants = [_finish(_LINE_HYPHEN.sub("", base)), _finish(_LINE_HYPHEN.sub("-", base))]
    return variants if variants[0] != variants[1] else variants[:1]


def page_haystacks(text: str, alt_text: str | None, pdf_text_layer: bool = False) -> list[str]:
    """Layout text plus, for PDFs, the reading-order text (two-column slides interleave in layout mode).

    The column rules apply only to the text of a PDF text layer; Markdown, Office text and
    AI transcriptions have no columns, and a run of spaces there is just spacing.
    """
    hays = page_variants(text, gaps=pdf_text_layer)
    for extra in page_variants(alt_text, blocks=True) if alt_text else []:
        if extra not in hays:
            hays.append(extra)
    return hays


def segments(quote: str) -> list[str]:
    parts = re.split(r"\s*(?:…|\.\.\.)\s*", normalize_quote(quote))
    return [p for p in parts if p]


def _weight(text: str) -> int:
    return len(text) + len(CJK.findall(text))


def _is_word(ch: str) -> bool:
    return ch.isalnum() and not CJK.match(ch)


def find_bounded(hay: str, seg: str, start: int) -> int:
    """str.find that refuses to start or end inside a word or number (`0.05` is not in `0.055`)."""
    i = hay.find(seg, start)
    while i >= 0:
        end = i + len(seg)
        before = not _is_word(seg[0]) or i == 0 or not _is_word(hay[i - 1])
        after = not _is_word(seg[-1]) or end == len(hay) or not _is_word(hay[end])
        if before and after:
            return i
        i = hay.find(seg, i + 1)
    return -1


def match_in_order(hays: list[str], segs: list[str], fold: bool):
    """Return (True, skipped_text_between_segments) or (False, [])."""
    for hay in hays:
        if fold:
            hay = hay.casefold()
        pos, gaps, ok = 0, [], True
        for n, seg in enumerate(segs):
            seg = seg.casefold() if fold else seg
            i = find_bounded(hay, seg, pos)
            if i < 0:
                ok = False
                break
            if n:
                gaps.append(hay[pos:i].strip())
            pos = i + len(seg)
        if ok:
            return True, gaps
    return False, []


def check_quote(conn, item: dict) -> dict:
    result = {"quote": item.get("quote"), "source": item.get("source"), "page": item.get("page")}
    quote = item.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return {**result, "status": "invalid", "detail": "quote is missing"}
    segs = segments(quote)
    if sum(_weight(s) for s in segs) < MIN_QUOTE_WEIGHT or any(_weight(s) < MIN_SEGMENT_WEIGHT for s in segs):
        return {**result, "status": "too_short",
                "detail": "quote (or a piece between ellipses) is too short to be evidence; paraphrase and cite instead"}
    try:
        src = resolve_source(conn, str(item.get("source")))
    except KbError as exc:
        return {**result, "status": "unknown_source", "detail": str(exc)}
    page = item.get("page")
    if not isinstance(page, int) or isinstance(page, bool):
        return {**result, "status": "invalid", "detail": "page must be an integer"}
    row = conn.execute("SELECT origin, text, alt_text FROM pages WHERE source_id = ? AND num = ?",
                       (src["source_id"], page)).fetchone()
    is_pdf_layer = src["kind"] == "pdf"
    if not row:
        return {**result, "status": "unknown_page", "detail": f"{src['unit']} {page} does not exist (1..{src['units']})"}
    result["low_confidence"] = row["origin"] == "ai-transcription"
    hays = page_haystacks(row["text"], row["alt_text"], is_pdf_layer and row["origin"] == "text-layer")
    for fold, status in ((False, "verified"), (True, "verified_case_insensitive")):
        ok, gaps = match_in_order(hays, segs, fold)
        if ok:
            if gaps:
                return {**result, "status": "verified_elided", "skipped_text": gaps}
            return {**result, "status": status}
    elsewhere = []
    for r in conn.execute("SELECT num, origin, text, alt_text FROM pages WHERE source_id = ? AND num != ?",
                          (src["source_id"], page)):
        if match_in_order(page_haystacks(r["text"], r["alt_text"], is_pdf_layer and r["origin"] == "text-layer"),
                          segs, True)[0]:
            elsewhere.append(r["num"])
    if elsewhere:
        return {**result, "status": "wrong_page", "found_on": elsewhere}
    if is_pdf_layer and row["origin"] == "text-layer" and row["alt_text"] is None:
        return {**result, "status": "not_found",
                "detail": "this index predates reading-order text, so a quote that crosses a column may be missed; "
                          "run ingest once to refresh the PDF, then verify again"}
    return {**result, "status": "not_found"}


def cmd_verify(args, stdin) -> dict:
    try:
        payload = json.loads(stdin.read())
        quotes = payload["quotes"]
        if not isinstance(quotes, list):
            raise TypeError("quotes is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise KbError('stdin must be JSON like {"quotes": [{"quote": ..., "source": ..., "page": N}]}') from exc
    conn = connect(args.root, args.kb)
    results = [check_quote(conn, q) if isinstance(q, dict) else {"status": "invalid", "detail": "not an object"}
               for q in quotes]
    return {"results": results,
            "all_verified": bool(results) and all(r["status"].startswith("verified") for r in results),
            "any_low_confidence": any(r.get("low_confidence") for r in results),
            "any_elided": any(r["status"] == "verified_elided" for r in results)}


# ---------------------------------------------------------------------- CLI


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project root containing .tutor/")
    sub = p.add_subparsers(dest="command", required=True)

    def with_kb(sp):
        sp.add_argument("--kb", required=True)
        return sp

    ing = with_kb(sub.add_parser("ingest"))
    ing.add_argument("paths", nargs="+")
    ing.add_argument("--scanned", action="store_true",
                     help="treat every low-text PDF page as scanned text to transcribe (re-reads unchanged files)")
    with_kb(sub.add_parser("status"))
    s = with_kb(sub.add_parser("search"))
    s.add_argument("query", nargs="?")
    s.add_argument("--query", dest="extra", action="append", help="additional query (repeatable)")
    s.add_argument("--mode", choices=("any", "all"), default="any")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--source")
    s.add_argument("--no-text", action="store_true")
    g = with_kb(sub.add_parser("get"))
    g.add_argument("--source", required=True)
    g.add_argument("--page", type=int, required=True)
    r = with_kb(sub.add_parser("render"))
    r.add_argument("--source", required=True)
    r.add_argument("--pages", required=True)
    reg = with_kb(sub.add_parser("register"))
    reg.add_argument("--source", required=True)
    reg.add_argument("--page", type=int, required=True)
    with_kb(sub.add_parser("verify"))
    with_kb(sub.add_parser("remove")).add_argument("--source", required=True)
    sub.add_parser("list")
    return p


def emit(obj: dict) -> None:
    try:
        print(json.dumps(obj, ensure_ascii=False))
    except UnicodeEncodeError:  # e.g. a lone surrogate in a quote
        print(json.dumps(obj, ensure_ascii=True))


def main(argv=None, stdin=None) -> int:
    args = parser().parse_args(argv)
    stdin = stdin or sys.stdin
    try:
        if args.command == "register":
            result = cmd_register(args, stdin)
        elif args.command == "verify":
            result = cmd_verify(args, stdin)
        else:
            result = {"ingest": cmd_ingest, "status": cmd_status, "search": cmd_search, "get": cmd_get,
                      "render": cmd_render, "remove": cmd_remove, "list": cmd_list}[args.command](args)
    except KbError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # keep the JSON contract even for unexpected failures
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        while _OPEN:
            _OPEN.pop().close()
    result["ok"] = True
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
