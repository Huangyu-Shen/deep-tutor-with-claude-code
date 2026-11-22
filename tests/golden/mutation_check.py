"""Check that the tests would notice a one-edit change to the ported code.

Development-time only:  python3 -I tests/golden/mutation_check.py

Each mutant is a one-edit change to one script (applied to a temporary copy of
the project) and is run against the tests that are meant to cover it: the
scheduler against the golden vectors, the memory libraries against the memory
golden vectors, and the memory command line against its own tests. A mutant
"survives" if those tests still pass, meaning they do not cover that behaviour.
Exit code 1 if any mutant survives.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]

MUTANTS = [
    ("spacing bonus", "spacing_ratio * 0.15", "spacing_ratio * 0.16"),
    ("same-session window", "_SAME_SESSION_DAYS = 0.25", "_SAME_SESSION_DAYS = 0.30"),
    ("same-session half factor", "DEFAULT_DESIRED_RETENTION) * 0.5,", "DEFAULT_DESIRED_RETENTION) * 0.6,"),
    ("drop scheduled_after_failure condition", "if state.scheduled_after_failure and (", "if (True) and ("),
    ("qualitative branch", '''evidence.result == "partial" or evidence.assessment_type == "qualitative"''',
     '''evidence.result == "partial"'''),
    ("partial floor", "state.retrievability = max(quality, 0.2)\n                return state",
     "state.retrievability = quality\n                return state"),
    ("fail threshold", "_FAIL_QUALITY = 0.5", "_FAIL_QUALITY = 0.51"),
    ("same-session success comparison", "if quality >= _FAIL_QUALITY:\n                state.review_count += 1",
     "if quality > _FAIL_QUALITY:\n                state.review_count += 1"),
    ("strong-recall bonus threshold", "if quality >= 0.8 and state.consecutive_correct >= 2:",
     "if quality > 0.8 and state.consecutive_correct >= 2:"),
    ("strong-recall bonus factor", "growth *= 1.15", "growth *= 1.16"),
    ("lapse retention factor", "0.55 - 0.25 * previous_retrievability", "0.55 - 0.26 * previous_retrievability"),
    ("difficulty update", "0.12 * outcome_error", "0.13 * outcome_error"),
    ("overdue cap", "min(overdue_days / 7.0, 0.25)", "min(overdue_days / 7.0, 0.26)"),
    ("error boost", "risk += 0.2", "risk += 0.21"),
    ("hint penalty", "evidence.hints_used * 0.08", "evidence.hints_used * 0.09"),
    ("attempt penalty", "max(0, evidence.attempt_count - 1) * 0.05", "max(0, evidence.attempt_count - 1) * 0.06"),
    ("partial quality", "quality = 0.4", "quality = 0.41"),
    ("clamp out-of-order time", "max(event_moment, state.last_review_at)", "event_moment"),
    ("failure shortens interval", "max(interval_days * 0.5, _MIN_STABILITY_DAYS * 0.2)",
     "max(interval_days * 0.6, _MIN_STABILITY_DAYS * 0.2)"),
]


DOC_MUTANTS = [
    ("memory_doc: text length limit", "MAX_TEXT_LEN = 240", "MAX_TEXT_LEN = 241"),
    ("memory_doc: section length limit", "MAX_SECTION_LEN = 80", "MAX_SECTION_LEN = 81"),
    ("memory_doc: delete reason set", '{"contradicted", "superseded", "stale", "low-signal"}',
     '{"contradicted", "superseded", "low-signal"}'),
    ("memory_doc: delete-then-edit conflict", "if op.target_id in deletes:", "if False:"),
    ("memory_doc: edit-then-delete conflict", "if op.target_id in edits:", "if False:"),
    ("memory_doc: add needs refs", "if not op.refs:", "if False:"),
    ("memory_doc: edit needs refs", "if not op.new_refs:", "if False:"),
    ("memory_doc: footnote numbering starts at 1", "ref_to_label[ref] = len(ref_order) + 1",
     "ref_to_label[ref] = len(ref_order)"),
    ("memory_doc: shared footnotes", "if ref in ref_to_label:", "if False:"),
    ("memory_doc: timestamp length", "_ULID_TS_LEN = 10", "_ULID_TS_LEN = 9"),
    ("memory_doc: entry id length", 'r"^m_[0-9A-HJKMNP-TV-Z]{26}\\Z"', 'r"^m_[0-9A-HJKMNP-TV-Z]{25}\\Z"'),
    ("memory_doc: id anchored at the end", 'r"^m_[0-9A-HJKMNP-TV-Z]{26}\\Z"', 'r"^m_[0-9A-HJKMNP-TV-Z]{26}$"'),
    ("memory_doc: line breaks allowed in text", 'if not _one_line(op.text):', 'if False:'),
    ("memory_doc: line breaks allowed in edits", 'if not _one_line(op.new_text):', 'if False:'),
    ("memory_doc: comment markers allowed", 'and "<!--" not in value', 'and False'),
    ("memory_doc: unparsed lines ignored", "        lost.append(line)\n    return lost", "        pass\n    return lost"),
    ("memory_doc: empty sections are skipped", "if not entries:\n            continue", "if False:\n            continue"),
    ("memory_doc: marker separator", 'markers = ", ".join(', 'markers = "".join('),
    ("memory_doc: shortname refs", "_SHORTNAME_REFS = frozenset(SURFACES)", "_SHORTNAME_REFS = frozenset()"),
]

SNAP_MUTANTS = [
    ("memory_snap: fingerprint separator", 'h.update(b"\\0")', 'h.update(b"\\x01")'),
    ("memory_snap: fingerprint length", "h.hexdigest()[:16]", "h.hexdigest()[:15]"),
    ("memory_snap: removed order", "for entity_id in sorted(prev_keys - curr_keys):",
     "for entity_id in sorted(prev_keys - curr_keys, reverse=True):"),
    ("memory_snap: added order", "for entity_id in sorted(curr_keys - prev_keys):",
     "for entity_id in sorted(curr_keys - prev_keys, reverse=True):"),
    ("memory_snap: removed label fallback", '.get(entity_id, "") or entity_id', '.get(entity_id, "")'),
    ("memory_snap: days_ago floor", "return max(0, delta.days)", "return delta.days"),
    ("memory_snap: limit cap", "return min(int(limit), MAX_LIMIT)", "return min(int(limit), MAX_LIMIT + 1)"),
    ("memory_snap: default limit", "DEFAULT_LIMIT = 20", "DEFAULT_LIMIT = 21"),
    ("memory_snap: label length", "[:MAX_LABEL_CHARS]", "[:MAX_LABEL_CHARS + 1]"),
    ("memory_snap: dedupe ignores case", "hit.label.casefold()", "hit.label"),
    ("memory_snap: window edge", "parsed < since", "parsed <= since"),
    ("memory_snap: zero-day window", "if days is None or days < 0:", "if days is None or days < 1:"),
    ("memory_snap: unknown surface filter", "return resolved or SURFACES", "return resolved"),
]

CLI_MUTANTS = [
    ("memory: preference key ignores case", 'return " ".join(str(text or "").split()).casefold()',
     'return " ".join(str(text or "").split())'),
    ("memory: preference key drops whitespace", 'return " ".join(str(text or "").split()).casefold()',
     'return "".join(str(text or "").split()).casefold()'),
    ("memory: preference length", "PREF_MAX = md.MAX_TEXT_LEN", "PREF_MAX = md.MAX_TEXT_LEN + 1"),
    ("memory: empty-read guard", 'if not adapted.entities and prev["fingerprints"] and not allow_empty:',
     'if False:'),
    ("memory: degraded read keeps removals", "if adapted.warnings:  # degraded read", "if False:  # degraded read"),
    ("memory: quiz includes qualitative attempts", 'not in ("quiz", "review"):', 'not in ("quiz", "review", "qualitative"):'),
    ("memory: chat point count", "MAX_POINTS = 8", "MAX_POINTS = 9"),
    ("memory: changes page cap", "limit = max(1, min(args.limit, 1000))", "limit = max(1, min(args.limit, 1001))"),
    ("memory: edits may erase unparsed lines", "if for_write:", "if False:"),
    ("memory: preference keeps line breaks", 'text = " ".join(text.split())  # one line', 'text = text.strip()  # one line'),
    ("memory: trace ignores the time inside a day", 'if since and obj["ts"] < since:', 'if False:'),
    ("memory: kb name reaches the path", "if kb and not _NAME.fullmatch(kb):", "if False:"),
    ("memory: kb index uri unescaped", 'f"{db.resolve().as_uri()}?mode=ro"', 'f"file:{db}?mode=ro"'),
    ("memory: encode errors escape emit", "except (OSError, ValueError) as exc:  # ValueError", "except OSError as exc:  # ValueError"),
    ("memory: torn line not repaired", "    ll.append_line(path, line)", '    with path.open("a", encoding="utf-8") as fh:\n        fh.write(line + "\\n")'),
    ("memory: no-change refresh still writes", "dirty = bool(changes) or", "dirty = True or bool(changes) or"),
    ("memory: kb timestamp is the latest source", "earliest = min(", "earliest = max("),
    ("memory: ops file written when rejected", 'raise MemoryError_(f"batch rejected, nothing written: {report.reason}")',
     'atomic_write(path, md.serialize(doc)); raise MemoryError_(f"batch rejected, nothing written: {report.reason}")'),
]

GROUPS = [
    ("scheduler", "scripts/scheduler.py", "test_golden.py", MUTANTS),
    ("memory_doc", "scripts/memory_doc.py", "test_*memory*.py", DOC_MUTANTS),
    ("memory_snap", "scripts/memory_snap.py", "test_*memory*.py", SNAP_MUTANTS),
    ("memory", "scripts/memory.py", "test_memory.py", CLI_MUTANTS),
]


def main() -> int:
    survivors = []
    total = 0
    for group, rel, test_file, mutants in GROUPS:
        original = (ROOT / rel).read_text(encoding="utf-8")
        for name, old, new in mutants:
            if original.count(old) != 1:
                print(f"BAD MUTANT (pattern found {original.count(old)}x): {name}")
                return 2
            total += 1
            with tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                shutil.copytree(ROOT / "scripts", tmp_root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
                shutil.copytree(ROOT / "tests", tmp_root / "tests", ignore=shutil.ignore_patterns("__pycache__"))
                (tmp_root / rel).write_text(original.replace(old, new), encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, "-I", "-m", "unittest", "discover", "-s", "tests", "-p", test_file],
                    cwd=tmp_root, capture_output=True, text=True,
                )
            killed = proc.returncode != 0
            print(("killed   " if killed else "SURVIVED ") + name)
            if not killed:
                survivors.append(name)
    print(f"\n{total - len(survivors)}/{total} mutants killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
