"""Ported memory libraries must reproduce vectors made by DeepTutor's original code.

Vectors: tests/golden/memory_vectors.json (tests/golden/generate_memory.py).
"""

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import memory_doc as md  # noqa: E402
import memory_snap as ms  # noqa: E402

from datetime import datetime  # noqa: E402
import types  # noqa: E402
import hashlib  # noqa: E402

VECTORS = json.loads((Path(__file__).parent / "golden" / "memory_vectors.json").read_text(encoding="utf-8"))


def doc_from_struct(struct):
    doc = md.Document(title=struct["title"])
    for name, entries in struct["sections"]:
        doc.sections.append(
            (name, [md.Entry(id=e["id"], section=name, text=e["text"], refs=list(e["refs"])) for e in entries])
        )
    return doc


def struct_from_doc(doc):
    return {
        "title": doc.title,
        "sections": [
            [name, [{"id": e.id, "text": e.text, "refs": list(e.refs)} for e in entries]]
            for name, entries in doc.sections
        ],
    }


def ops_from_specs(specs):
    out = []
    for spec in specs:
        if spec["op"] == "add":
            out.append(md.AddOp(spec["section"], spec["text"], list(spec["refs"])))
        elif spec["op"] == "edit":
            out.append(md.EditOp(spec["target_id"], spec["new_text"], list(spec["new_refs"])))
        elif spec["op"] == "delete":
            out.append(md.DeleteOp(spec["target_id"], spec["reason"]))
        else:
            class Weird:  # an object that is not an operation
                pass

            out.append(Weird())
    return out


class VectorsAreNotEmpty(unittest.TestCase):
    """The replay loops below would pass vacuously on an emptied vectors file."""

    def test_every_vector_group_has_cases(self):
        for key in ("ids", "documents", "parse", "ops", "sha1", "diff", "recent", "recent_queries", "days_ago"):
            group = VECTORS[key]
            cases = group["ulid"] + group["refs"] if key == "ids" else group
            self.assertGreaterEqual(len(cases), 5, key)
        self.assertEqual(len(VECTORS["upstream"]["source_sha256"]), 7)


class GoldenIds(unittest.TestCase):
    def test_ulid_encoding(self):
        for case in VECTORS["ids"]["ulid"]:
            self.assertEqual(md.new_ulid(case["now_ms"], case["rand"]), case["ulid"], case)

    def test_ref_predicates(self):
        for case in VECTORS["ids"]["refs"]:
            s = case["s"]
            self.assertEqual(md.is_entry_id(s), case["entry"], s)
            self.assertEqual(md.is_trace_id(s), case["trace"], s)
            self.assertEqual(md.is_snapshot_ref(s), case["snapshot"], s)
            self.assertEqual(md.is_shortname_ref(s), case["shortname"], s)
            self.assertEqual(md.is_valid_ref(s), case["valid"], s)


class GoldenDocuments(unittest.TestCase):
    def test_serialize_and_roundtrip(self):
        for case in VECTORS["documents"]:
            name = case["name"]
            doc = doc_from_struct(case["struct"])
            text = md.serialize(doc)
            self.assertEqual(text, case["serialized"], name)
            reparsed = md.parse(text)
            self.assertEqual(struct_from_doc(reparsed), case["reparsed"], name)
            self.assertEqual(md.serialize(reparsed), case["reserialized"], name)

    def test_parse_handwritten_inputs(self):
        for case in VECTORS["parse"]:
            name = case["name"]
            doc = md.parse(case["input"])
            self.assertEqual(struct_from_doc(doc), case["parsed"], name)
            self.assertEqual(md.serialize(doc), case["serialized"], name)


class GoldenOps(unittest.TestCase):
    def test_batches(self):
        for case in VECTORS["ops"]:
            name = case["name"]
            doc = doc_from_struct(case["struct"])
            counter = {"n": 0}
            alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

            def fake_id():
                counter["n"] += 1
                return "m_" + "0" * 25 + alphabet[counter["n"]]

            report = md.apply(doc, ops_from_specs(case["ops"]), new_id=fake_id)
            expected = case["result"]
            self.assertEqual(report.accepted, expected["accepted"], name)
            self.assertEqual(report.reason, expected["reason"], name)
            self.assertEqual([r.entry_id for r in report.results], expected["entry_ids"], name)
            self.assertEqual([r.detail for r in report.results], expected["details"], name)
            self.assertEqual(struct_from_doc(doc), expected["doc"], name)
            self.assertEqual(md.serialize(doc), expected["serialized"], name)


class GoldenFingerprint(unittest.TestCase):
    def test_fingerprint(self):
        for case in VECTORS["sha1"]:
            self.assertEqual(ms.fingerprint(*case["parts"]), case["fingerprint"], case["parts"])


class GoldenDiff(unittest.TestCase):
    def test_diff(self):
        for case in VECTORS["diff"]:
            changes = ms.diff_snapshots(
                case["prev"], case["curr"], label_map=case["labels"],
                prev_label_map=case["prev_labels"], now=case["now"],
            )
            self.assertEqual([c.to_dict() for c in changes], case["changes"], case["name"])


class GoldenRecall(unittest.TestCase):
    def test_days_ago(self):
        now = datetime.fromisoformat(VECTORS["recent"][0]["now"])
        for case in VECTORS["days_ago"]:
            self.assertEqual(ms.days_ago(case["ts"], now=now), case["days_ago"], case["ts"])

    def test_recent(self):
        for case in VECTORS["recent"]:
            now = datetime.fromisoformat(case["now"])
            by_surface = {}
            for row in case["stamps"]:
                by_surface.setdefault(row["surface"], []).append(
                    ms.EntityStamp(id=row["id"], label=row["label"], fingerprint="fp", ts=row["ts"])
                )
            hits = ms.shape_recent(by_surface, now=now, **case["kwargs"])
            self.assertEqual([h.to_dict() for h in hits], case["hits"], case["name"])

    def test_recent_queries(self):
        for case in VECTORS["recent_queries"]:
            now = datetime.fromisoformat(case["now"])
            kwargs = case["kwargs"]
            days = kwargs.get("days", ms.DEFAULT_DAYS)
            since = ms.cutoff(days, now)
            events = []
            for row in case["events"]:
                if since is not None and datetime.fromisoformat(row["ts"]) < since:
                    continue
                events.append(types.SimpleNamespace(kind=row["kind"], payload=row["payload"], ts=row["ts"]))
            hits = ms.shape_queries({"kb": events}, now=now, **kwargs)
            self.assertEqual([h.to_dict() for h in hits], case["hits"], case["name"])


class UpstreamProvenance(unittest.TestCase):
    """If the upstream checkout is present, it must still match the vectors' source hashes."""

    def test_source_hashes(self):
        base = ROOT / "DeepTutor-main" / "deeptutor" / "services" / "memory"
        if not base.is_dir():
            self.skipTest("DeepTutor-main/ not present")
        changed = []
        for rel, expected in VECTORS["upstream"]["source_sha256"].items():
            if hashlib.sha256((base / rel).read_bytes()).hexdigest() != expected:
                changed.append(rel)
        self.assertEqual(changed, [], "upstream files changed since the vectors were generated")


if __name__ == "__main__":
    unittest.main()
