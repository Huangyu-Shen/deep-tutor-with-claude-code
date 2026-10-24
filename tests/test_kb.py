import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import kb  # noqa: E402

_spec = importlib.util.spec_from_file_location("fixture_build", FIX / "build.py")
fixture_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_build)

HAVE_POPPLER = bool(shutil.which("pdftotext") and shutil.which("pdftoppm"))
HAVE_MARKITDOWN = bool(shutil.which("markitdown"))


class KbCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.work = Path(self.root) / "materials"
        self.work.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def cli(self, *argv, stdin=""):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kb.main(["--root", self.root, *argv], stdin=io.StringIO(stdin))
        return code, json.loads(out.getvalue())

    def ok(self, *argv, **kw):
        code, data = self.cli(*argv, **kw)
        self.assertEqual(code, 0, data)
        return data

    def err(self, *argv, **kw):
        code, data = self.cli(*argv, **kw)
        self.assertEqual(code, 1, data)
        self.assertFalse(data["ok"])
        return data["error"]

    def put(self, name, content):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def ingest(self, *paths, name="t"):
        return self.ok("ingest", "--kb", name, *[str(p) for p in paths])

    def verify(self, quotes, name="t"):
        return self.ok("verify", "--kb", name, stdin=json.dumps({"quotes": quotes}))["results"]


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class PdfIngest(KbCase):
    def test_text_pdf_pages_and_report(self):
        data = self.ingest(FIX / "lecture.pdf")
        f = data["files"][0]
        self.assertEqual((f["status"], f["unit"], f["units"]), ("added", "page", 3))
        self.assertEqual(f["needs_transcription"], [])
        self.assertIn("thylakoid", self.ok("get", "--kb", "t", "--source", "lecture.pdf", "--page", "1")["text"])
        self.assertIn("Calvin", self.ok("get", "--kb", "t", "--source", "lecture-pdf", "--page", "2")["text"])
        self.assertTrue(data["all_ok"])

    def test_scanned_pdf_pages_are_flagged_not_silently_empty(self):
        f = self.ingest(FIX / "scanned.pdf")["files"][0]
        self.assertEqual((f["units"], f["needs_transcription"]), (2, [1, 2]))
        self.assertEqual(self.ok("search", "--kb", "t", "mitochondria")["hits"], [])

    def test_reingest_unchanged_then_changed(self):
        pdf = self.put("a.pdf", fixture_build.build_text_pdf([["First version of the lecture text."]]))
        self.assertEqual(self.ingest(pdf)["files"][0]["status"], "added")
        self.assertEqual(self.ingest(pdf)["files"][0]["status"], "unchanged")
        pdf.write_bytes(fixture_build.build_text_pdf([["Second version of the lecture text."], ["Page two has more text."]]))
        f = self.ingest(pdf)["files"][0]
        self.assertEqual((f["status"], f["units"]), ("updated", 2))
        self.assertEqual(self.ok("search", "--kb", "t", "First")["hits"], [])
        self.assertTrue(self.ok("search", "--kb", "t", "Second")["hits"])

    def test_corrupt_pdf_is_reported_as_failed_and_others_still_ingest(self):
        bad = self.put("bad.pdf", b"this is not a pdf")
        data = self.ingest(bad, FIX / "lecture.pdf")
        statuses = {f["display"]: f["status"] for f in data["files"]}
        self.assertEqual(statuses, {"bad.pdf": "failed", "lecture.pdf": "added"})
        self.assertFalse(data["all_ok"])
        self.assertTrue(data["files"][0]["error"] if data["files"][0]["status"] == "failed" else data["files"][1]["error"])

    def test_missing_tool_gives_install_hint(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            f = self.ingest(FIX / "lecture.pdf")["files"][0]
        self.assertEqual(f["status"], "failed")
        self.assertIn("brew install poppler", f["error"])

    def test_render_and_register_transcription(self):
        self.ingest(FIX / "scanned.pdf")
        r = self.ok("render", "--kb", "t", "--source", "scanned.pdf", "--pages", "1-2")
        self.assertEqual([i["page"] for i in r["images"]], [1, 2])
        for img in r["images"]:
            self.assertEqual(Path(img["image"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        again = self.ok("render", "--kb", "t", "--source", "scanned.pdf", "--pages", "1")
        self.assertEqual(again["images"][0]["image"], r["images"][0]["image"])
        reg = self.ok("register", "--kb", "t", "--source", "scanned.pdf", "--page", "1",
                      stdin="Mitochondria produce ATP through oxidative phosphorylation.")
        self.assertEqual(reg["remaining_needing_transcription"], [2])
        hit = self.ok("search", "--kb", "t", "oxidative phosphorylation")["hits"][0]
        self.assertTrue(hit["low_confidence"])
        self.assertEqual(hit["citation"], "scanned.pdf page 1")
        # may be corrected, but only while it is still an AI transcription
        self.ok("register", "--kb", "t", "--source", "scanned.pdf", "--page", "1",
                stdin="Mitochondria produce ATP by oxidative phosphorylation.")
        self.assertEqual(self.ok("status", "--kb", "t")["sources"][0]["ai_transcribed"], [1])

    def test_render_and_register_guards(self):
        self.ingest(FIX / "lecture.pdf", FIX / "scanned.pdf", FIX / "notes.md")
        self.assertIn("out of range", self.err("render", "--kb", "t", "--source", "scanned.pdf", "--pages", "3"))
        self.assertIn("bad page spec", self.err("render", "--kb", "t", "--source", "scanned.pdf", "--pages", "x"))
        self.assertIn("only PDF", self.err("render", "--kb", "t", "--source", "notes.md", "--pages", "1"))
        self.assertIn("refusing to overwrite", self.err(
            "register", "--kb", "t", "--source", "lecture.pdf", "--page", "1", stdin="Replacement text for the page."))
        self.assertIn("empty", self.err("register", "--kb", "t", "--source", "scanned.pdf", "--page", "1", stdin="  "))
        self.assertIn("no page 9", self.err(
            "register", "--kb", "t", "--source", "scanned.pdf", "--page", "9", stdin="Some long enough text here."))


@unittest.skipUnless(HAVE_MARKITDOWN, "markitdown not installed")
class OfficeIngest(KbCase):
    def test_pptx_slides_keep_their_numbers(self):
        f = self.ingest(FIX / "slides.pptx")["files"][0]
        self.assertEqual((f["unit"], f["units"]), ("slide", 3))
        hit = self.ok("search", "--kb", "t", "fermentation NAD")["hits"][0]
        self.assertEqual((hit["citation"], hit["origin"]), ("slides.pptx slide 3", "markitdown"))
        self.assertNotIn("Slide number", self.ok("get", "--kb", "t", "--source", "slides.pptx", "--page", "1")["text"])


class PlainIngest(KbCase):
    def test_markdown_splits_on_headings_and_names_collide_safely(self):
        a = self.put("one/notes.md", "# Intro\nSome introduction text here.\n\n## Part A\nOsmosis moves water across membranes.\n")
        b = self.put("two/notes.md", "Different notes file with enough words in it.\n")
        data = self.ingest(a, b)
        ids = [f["source_id"] for f in data["files"]]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(data["files"][0]["units"], 2)
        self.assertEqual(self.ok("search", "--kb", "t", "osmosis")["hits"][0]["num"], 2)
        self.assertIn("ambiguous", self.err("get", "--kb", "t", "--source", "notes.md", "--page", "1"))

    def test_folder_walk_skips_hidden_and_lists_unsupported(self):
        self.put("keep.txt", "A plain text file with plenty of characters.")
        self.put(".hidden/secret.txt", "Should never be read at all by the walker.")
        self.put("image.png", b"\x89PNG")
        data = self.ingest(self.work)
        self.assertEqual([f["display"] for f in data["files"]], ["keep.txt"])
        self.assertEqual([Path(s["path"]).name for s in data["skipped"]], ["image.png"])
        self.assertTrue(data["all_ok"])  # found while walking: informational

    def test_explicitly_named_unreadable_file_is_not_all_ok(self):
        data = self.ingest(self.work / "nope.txt")
        self.assertFalse(data["all_ok"])
        self.assertEqual(data["totals"]["skipped"], 1)

    def test_non_utf8_text_fails_cleanly(self):
        f = self.ingest(self.put("latin.txt", "caf\xe9 is nice and long enough".encode("latin-1")))["files"][0]
        self.assertEqual(f["status"], "failed")

    def test_empty_file_reported_as_empty_unit(self):
        f = self.ingest(self.put("blank.md", "\n\n"))["files"][0]
        self.assertEqual(f["empty_units"], [1])


class Search(KbCase):
    def setUp(self):
        super().setUp()
        self.put("bio.md", "# Light\nThe light reactions occur in the thylakoid membranes.\n\n"
                           "# Dark\nThe Calvin cycle fixes carbon dioxide using Rubisco.\n")
        self.put("chem.txt", "Rubisco is mentioned here too, in a chemistry note about enzymes.")
        self.put("cn.md", "# 牛顿定律\n牛顿第三定律:作用力与反作用力大小相等,方向相反。\n")
        self.ingest(self.work)

    def test_ranking_and_citation(self):
        hits = self.ok("search", "--kb", "t", "calvin cycle carbon")["hits"]
        self.assertEqual((hits[0]["display"], hits[0]["num"]), ("bio.md", 2))
        self.assertEqual(hits[0]["citation"], "bio.md section 2")
        self.assertIn("«Calvin»", hits[0]["snippet"])

    def test_multiple_queries_merge_without_duplicates(self):
        data = self.ok("search", "--kb", "t", "Rubisco", "--query", "calvin cycle", "--limit", "10")
        keys = [(h["source_id"], h["num"]) for h in data["hits"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 2)

    def test_source_filter_and_no_text(self):
        hits = self.ok("search", "--kb", "t", "Rubisco", "--source", "chem.txt", "--no-text")["hits"]
        self.assertEqual([h["display"] for h in hits], ["chem.txt"])
        self.assertNotIn("text", hits[0])

    def test_all_mode_requires_every_word(self):
        self.assertEqual(self.ok("search", "--kb", "t", "thylakoid Rubisco", "--mode", "all")["hits"], [])
        self.assertTrue(self.ok("search", "--kb", "t", "thylakoid Rubisco")["hits"])

    def test_all_mode_with_mixed_cjk_and_latin_needs_both(self):
        self.put("mix.md", "# Mix\nRubisco is an enzyme; 作用力 is a force concept here.\n")
        self.put("jn.md", "# J\nRubisco and 作用力 and 合成 all appear together here.\n")
        self.ingest(self.work)
        hits = self.ok("search", "--kb", "t", "Rubisco 作用力", "--mode", "all")["hits"]
        self.assertEqual({(h["display"], h["num"]) for h in hits}, {("mix.md", 1), ("jn.md", 1)})
        neither = self.ok("search", "--kb", "t", "thylakoid 作用力", "--mode", "all")["hits"]
        self.assertEqual(neither, [])  # bio.md has thylakoid, cn.md has 作用力, no page has both
        # more CJK terms than one, plus a Latin word: only jn.md has all three
        tri = self.ok("search", "--kb", "t", "Rubisco 作用力 合成", "--mode", "all")["hits"]
        self.assertEqual([(h["display"], h["num"]) for h in tri], [("jn.md", 1)])
        # duplicate CJK terms must not count twice or inflate the all-mode constraint
        dup = self.ok("search", "--kb", "t", "Rubisco 作用力 作用力", "--mode", "all")["hits"]
        self.assertEqual({(h["display"], h["num"]) for h in dup}, {("mix.md", 1), ("jn.md", 1)})
        any_mode = self.ok("search", "--kb", "t", "Rubisco 作用力")["hits"]
        self.assertEqual({(h["display"], h["num"]) for h in any_mode},
                         {("bio.md", 2), ("chem.txt", 1), ("cn.md", 1), ("mix.md", 1), ("jn.md", 1)})

    def test_all_mode_mixed_with_source_filter(self):
        self.put("mix.md", "# Mix\nRubisco is an enzyme; 作用力 is a force concept here.\n")
        self.ingest(self.work / "mix.md")
        hits = self.ok("search", "--kb", "t", "Rubisco 作用力", "--mode", "all",
                       "--source", "mix.md")["hits"]
        self.assertEqual([(h["display"], h["num"]) for h in hits], [("mix.md", 1)])
        self.assertEqual(self.ok("search", "--kb", "t", "Rubisco 作用力", "--mode", "all",
                                 "--source", "cn.md")["hits"], [])

    def test_all_mode_mixed_ranks_by_bm25_under_limit(self):
        for name, reps in (("a.md", 10), ("b.md", 5), ("c.md", 1)):
            self.put(name, f"# {name[0]}\n" + "Rubisco " * reps + "作用力 appears here too.\n")
        self.ingest(self.work)
        hits = self.ok("search", "--kb", "t", "Rubisco 作用力", "--mode", "all", "--limit", "2")["hits"]
        self.assertEqual([h["display"] for h in hits], ["a.md", "b.md"])

    def test_cjk_substring_search(self):
        hits = self.ok("search", "--kb", "t", "反作用力")["hits"]
        self.assertEqual(hits[0]["display"], "cn.md")

    def test_hostile_query_text_does_not_break_fts(self):
        for q in ['"', 'Rubisco" OR "x', "NEAR(a b)", "(((", "a*", "col:umn", "-", "'", "AND OR NOT"]:
            with self.subTest(q):
                code, data = self.cli("search", "--kb", "t", q)
                self.assertEqual(code, 0, data)

    def test_empty_query_and_unknown_kb(self):
        self.assertIn("at least one", self.err("search", "--kb", "t", "  "))
        self.assertIn("does not exist", self.err("search", "--kb", "missing", "x"))

    def test_bad_kb_name_rejected(self):
        self.assertIn("kb name", self.err("search", "--kb", "../x", "y"))

    def test_text_is_capped_and_flagged(self):
        self.put("long.txt", "needleword " + "filler " * 1000)
        self.ingest(self.work / "long.txt")
        hit = self.ok("search", "--kb", "t", "needleword")["hits"][0]
        self.assertTrue(hit["text_truncated"])
        self.assertEqual(len(hit["text"]), kb.TEXT_CAP)

    def test_list_and_status(self):
        self.assertEqual(self.ok("list")["knowledge_bases"], ["t"])
        self.assertEqual(len(self.ok("status", "--kb", "t")["sources"]), 3)


class Verify(KbCase):
    def setUp(self):
        super().setUp()
        page1 = ["Lecture notes: photo-", "synthesis converts light energy.", "It’s “fast” – really fast."]
        page2 = ["The Calvin cycle fixes carbon dioxide into sugar.", "Rubisco is the enzyme that catalyses carbon fixation."]
        if HAVE_POPPLER:
            self.pdf = self.put("v.pdf", fixture_build.build_text_pdf([page1, page2]))
        self.put("scan.md", "# s\nplaceholder text that is long enough here.\n")

    def q(self, quote, page=1, source="v.pdf"):
        return {"quote": quote, "source": source, "page": page}

    @unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
    def test_statuses(self):
        self.ingest(self.pdf)
        cases = [
            (self.q("The Calvin cycle fixes carbon dioxide into sugar.", 2), 2, "verified"),
            (self.q("the calvin cycle fixes carbon dioxide", 2), 2, "verified_case_insensitive"),
            (self.q("Calvin   cycle\nfixes carbon dioxide", 2), 2, "verified"),
            (self.q("The Calvin cycle ... carbon fixation.", 2), 2, "verified_elided"),
            (self.q("carbon fixation ... The Calvin cycle", 2), 2, "not_found"),  # order matters
            (self.q("photosynthesis converts light energy"), 1, "verified"),  # hyphen at line break
            (self.q("It's \"fast\" - really fast"), 1, "verified"),  # curly quotes and dashes
            (self.q("The Calvin cycle fixes carbon dioxide into sugar.", 1), 1, "wrong_page"),
            (self.q("Plants use sunshine to make food from air."), 1, "not_found"),
            (self.q("sugar"), 1, "too_short"),
            (self.q("a long enough but ... x ... another long piece"), 1, "too_short"),
            (self.q("The Calvin cycle fixes carbon dioxide", 9), 1, "unknown_page"),
            (self.q("The Calvin cycle fixes carbon dioxide", 1, "other.pdf"), 1, "unknown_source"),
            ({"source": "v.pdf", "page": 1}, 1, "invalid"),
            ({"quote": "The Calvin cycle fixes carbon dioxide", "source": "v.pdf", "page": "2"}, 1, "invalid"),
        ]
        results = self.verify([c[0] for c in cases])
        self.assertEqual([r["status"] for r in results], [c[2] for c in cases])
        self.assertEqual(results[7]["found_on"], [2])

    @unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
    def test_all_verified_requires_every_quote_and_nonempty(self):
        self.ingest(self.pdf)
        good = self.q("The Calvin cycle fixes carbon dioxide", 2)
        data = self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": [good]}))
        self.assertTrue(data["all_verified"])
        data = self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": [good, self.q("Nothing like this is there at all.", 2)]}))
        self.assertFalse(data["all_verified"])
        self.assertFalse(self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": []}))["all_verified"])

    def test_ai_transcribed_pages_are_flagged_low_confidence(self):
        self.ingest(FIX / "scanned.pdf") if HAVE_POPPLER else self.skipTest("poppler not installed")
        self.ok("register", "--kb", "t", "--source", "scanned.pdf", "--page", "1",
                stdin="Mitochondria produce ATP through oxidative phosphorylation.")
        data = self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": [
            {"quote": "produce ATP through oxidative phosphorylation", "source": "scanned.pdf", "page": 1}]}))
        self.assertEqual(data["results"][0]["status"], "verified")
        self.assertTrue(data["results"][0]["low_confidence"])
        self.assertTrue(data["any_low_confidence"])

    def test_ellipsis_that_skips_nothing_is_plain_verified(self):
        self.ingest(self.work / "scan.md")
        q = {"quote": "placeholder text that is … long enough here", "source": "scan.md", "page": 1}
        ok = self.verify([q])[0]
        self.assertEqual(ok["status"], "verified")
        self.assertNotIn("skipped_text", ok)
        self.assertFalse(self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": [q]}))["any_elided"])
        real = self.verify([{"quote": "placeholder … long enough here", "source": "scan.md", "page": 1}])[0]
        self.assertEqual(real["status"], "verified_elided")
        self.assertEqual(real["skipped_text"], ["text that is"])
        caseless = self.verify([{"quote": "Placeholder text that is … LONG enough here", "source": "scan.md",
                                 "page": 1}])[0]
        self.assertEqual(caseless["status"], "verified_case_insensitive")
        self.assertNotIn("skipped_text", caseless)
        data = self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": [
            q, {"quote": "placeholder … long enough here", "source": "scan.md", "page": 1}]}))
        self.assertTrue(data["any_elided"])  # real elision and no-op elision coexist

    def test_bad_stdin(self):
        self.ingest(self.work / "scan.md")
        for bad in ("not json", "{}", '{"quotes": "x"}'):
            with self.subTest(bad):
                self.assertIn("stdin must be JSON", self.err("verify", "--kb", "t", stdin=bad))


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class VerifyStrictness(KbCase):
    """Regression tests for review findings: quotes must not verify by accident."""

    def setUp(self):
        super().setUp()
        pages = [
            ["The dose is not effective for adults.", "Result significant at p < 0.055 overall.",
             "The price is 5 dollars in total."],
            ["Energy is E = mc\u00b2 where m is mass.", "The water formula is H2O in notes."],
            ["This is a well-", "known fact about cells\u2014", "which divide quickly in culture."],
        ]
        self.ingest(self.put("s.pdf", fixture_build.build_text_pdf(pages)))

    def status(self, quote, page):
        return self.verify([{"quote": quote, "source": "s.pdf", "page": page}])[0]

    def test_ellipsis_returns_the_words_it_skipped(self):
        r = self.status("The dose is ... effective for adults", 1)
        self.assertEqual(r["status"], "verified_elided")
        self.assertEqual(r["skipped_text"], ["not"])
        self.assertTrue(self.ok("verify", "--kb", "t", stdin=json.dumps({"quotes": [
            {"quote": "The dose is ... effective for adults", "source": "s.pdf", "page": 1}]}))["any_elided"])

    def test_no_match_inside_a_longer_number_or_word(self):
        self.assertEqual(self.status("Result significant at p < 0.05", 1)["status"], "not_found")
        self.assertEqual(self.status("Result significant at p < 0.055", 1)["status"], "verified")
        self.assertEqual(self.status("The price is 5 dollar", 1)["status"], "not_found")
        self.assertEqual(self.status("The price is 5 dollars", 1)["status"], "verified")

    def test_superscripts_and_subscripts_are_not_folded(self):
        self.assertEqual(self.status("Energy is E = mc2 where m", 2)["status"], "not_found")
        self.assertEqual(self.status("Energy is E = mc\u00b2 where m", 2)["status"], "verified")
        self.assertEqual(self.status("The water formula is H\u2082O in", 2)["status"], "not_found")
        self.assertEqual(self.status("The water formula is H2O in", 2)["status"], "verified")

    def test_line_end_hyphen_and_dash(self):
        self.assertEqual(self.status("This is a well-known fact", 3)["status"], "verified")
        self.assertEqual(self.status("This is a wellknown fact", 3)["status"], "verified")  # joined form accepted
        self.assertEqual(self.status("known fact about cells\u2014which divide quickly", 3)["status"], "verified")
        self.assertEqual(self.status("known fact about cells - which divide", 3)["status"], "verified")


class VerifyCjk(KbCase):
    def test_cjk_length_rule_and_inner_spaces(self):
        self.put("cn.md", "# 光合作用\n光合作用是植物利用 光能 的过程,并释放氧气。\n")
        self.ingest(self.work / "cn.md")
        ok = self.verify([{"quote": "光合作用是植物利用光能的过程", "source": "cn.md", "page": 1}])[0]
        self.assertEqual(ok["status"], "verified")
        short = self.verify([{"quote": "光合作用", "source": "cn.md", "page": 1}])[0]
        self.assertEqual(short["status"], "too_short")


class IngestRobustness(KbCase):
    def test_unreadable_file_does_not_sink_the_batch(self):
        good = self.put("good.md", "A readable file with plenty of text in it.")
        locked = self.put("locked.md", "Content that cannot be read by us.")
        locked.chmod(0)
        self.addCleanup(lambda: locked.chmod(0o644))
        if os.access(locked, os.R_OK):
            self.skipTest("running with privileges that ignore file modes")
        data = self.ingest(good, locked)
        self.assertEqual({f["display"]: f["status"] for f in data["files"]}, {"good.md": "added", "locked.md": "failed"})
        self.assertEqual(len(self.ok("status", "--kb", "t")["sources"]), 1)

    def test_duplicate_content_is_reported_not_doubled(self):
        a = self.put("a/one.md", "The very same text appears in both files here.")
        b = self.put("b/two.md", "The very same text appears in both files here.")
        data = self.ingest(a, b)
        self.assertEqual([f["status"] for f in data["files"]], ["added", "duplicate"])
        self.assertEqual(data["totals"]["duplicate"], 1)
        self.assertEqual(len(self.ok("search", "--kb", "t", "very same")["hits"]), 1)

    def test_remove_source(self):
        self.ingest(self.put("gone.md", "Text that will be removed from the index."))
        self.ok("remove", "--kb", "t", "--source", "gone.md")
        self.assertEqual(self.ok("search", "--kb", "t", "removed")["hits"], [])
        self.assertEqual(self.ok("status", "--kb", "t")["sources"], [])

    def test_cjk_file_name_gets_a_usable_id(self):
        f = self.ingest(self.put("第一章 讲义.md", "# 标题\n这是一个足够长的中文讲义内容。\n"))["files"][0]
        self.assertTrue(f["source_id"].startswith("src-"))

    def test_search_index_follows_reingest_and_register(self):
        p = self.put("x.md", "Alpha beta gamma delta epsilon text here.")
        self.ingest(p)
        p.write_text("Zeta eta theta iota kappa text here now.", encoding="utf-8")
        self.ingest(p)
        self.assertEqual(self.ok("search", "--kb", "t", "alpha")["hits"], [])
        self.assertEqual(len(self.ok("search", "--kb", "t", "kappa")["hits"]), 1)

    def test_kb_name_cannot_be_dots(self):
        self.assertIn("kb name", self.err("search", "--kb", "..", "x"))

    def test_lone_surrogate_in_quote_still_returns_json(self):
        self.ingest(self.put("n.md", "Some plain note text that is long enough."))
        code, data = self.cli("verify", "--kb", "t",
                              stdin='{"quotes": [{"quote": "bad \\ud800 surrogate text here", "source": "n.md", "page": 1}]}')
        self.assertEqual(code, 0)

    def test_cjk_search_respects_mode_all(self):
        self.put("c.md", "# a\n光合作用发生在叶绿体中,这是教材内容。\n")
        self.ingest(self.work / "c.md")
        self.assertTrue(self.ok("search", "--kb", "t", "光合作用 叶绿体", "--mode", "all")["hits"])
        self.assertEqual(self.ok("search", "--kb", "t", "光合作用 线粒体", "--mode", "all")["hits"], [])


class PdfImages(unittest.TestCase):
    def test_parse_pdfimages_list(self):
        out = ("page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
               "-----------------------------------------------------------------------------------------\n"
               "   1     0 image    1240  1754  rgb     3   8  jpeg   no         1  0   150   150 49.2K 0.8%\n"
               "   2     1 image      64    64  rgb     3   8  jpeg   no         4  0   150   150  2.2K 0.8%\n"
               "   3     2 smask    1240  1754  gray    1   8  image  no         7  0   150   150  2.2K 0.8%\n")
        self.assertEqual(kb.parse_pdfimages_list(out), {1})

    @unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
    def test_big_image_with_only_a_footer_is_treated_as_scan(self):
        footer = "Lecture 5 - University of X page footer"
        with mock.patch.object(kb, "run_tool") as run:
            real = kb.run_tool
            run.side_effect = lambda cmd, hint, timeout=300: (
                f"{footer}\n\f" if cmd[0] == "pdftotext"
                else "page num type width height\n----\n   1 0 image 1240 1754 rgb\n")
            kind, units, notes = kb.extract_pdf(Path("x.pdf"))
        self.assertEqual(units[0][2], "needs-transcription")
        self.assertTrue(notes["treated_as_scan"])


class StaleScans(KbCase):
    @unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
    def test_changed_pdf_drops_cached_page_images(self):
        pdf = self.put("s.pdf", fixture_build.build_text_pdf([["x" * 5]]))
        shutil.copy(FIX / "scanned.pdf", pdf)
        self.ingest(pdf)
        img = Path(self.ok("render", "--kb", "t", "--source", "s.pdf", "--pages", "1")["images"][0]["image"])
        self.assertTrue(img.exists())
        shutil.copy(FIX / "lecture.pdf", pdf)
        self.assertEqual(self.ingest(pdf)["files"][0]["status"], "updated")
        self.assertFalse(img.exists())


class ScanDetection(unittest.TestCase):
    """A scan is a document that is mostly low-text pages; a deck with a few photos is not.

    Regression for the first real course (16 slide decks): 37% of pages were flagged as scans
    because they held a photo or a title, which would have meant transcribing 121 pictures.
    """

    BODY = "This slide has a normal amount of explanatory text on it. " * 3
    IMG = "page num type width height\n----\n{rows}"

    def extract(self, pages, image_pages=(), force=False):
        text = "\f".join(pages) + "\f"
        rows = "".join(f"   {p} 0 image 1920 1080 rgb\n" for p in image_pages)
        with mock.patch.object(kb, "run_tool") as run:
            run.side_effect = lambda cmd, hint, timeout=300: text if cmd[0] == "pdftotext" else self.IMG.format(rows=rows)
            return {n: origin for n, _t, origin in kb.extract_pdf(Path("x.pdf"), force)[1]}

    def test_slide_deck_with_photos_is_not_a_scan(self):
        pages = [self.BODY, "Section title", self.BODY, "", self.BODY, self.BODY, "A caption under a big photo"]
        origins = self.extract(pages, image_pages=(2, 4, 7))
        self.assertEqual(origins, {1: "text-layer", 2: "text-layer", 3: "text-layer", 4: "empty",
                                   5: "text-layer", 6: "text-layer", 7: "text-layer"})
        self.assertNotIn("needs-transcription", origins.values())

    def test_mostly_low_text_document_is_a_scan(self):
        origins = self.extract(["", "", "Footer line of a scanned page here", "", self.BODY], image_pages=(1, 2, 3, 4))
        self.assertEqual(origins, {1: "needs-transcription", 2: "needs-transcription", 3: "needs-transcription",
                                   4: "needs-transcription", 5: "text-layer"})

    def test_ratio_boundary(self):
        picture_heavy = ["", "", "", "", "", ""] + [self.BODY] * 4   # 60%: a deck of photos (real case: 63%)
        almost_all = ["", "", "", "", "", "", "", ""] + [self.BODY] * 2  # 80%: a scan
        self.assertNotIn("needs-transcription", self.extract(picture_heavy, image_pages=range(1, 7)).values())
        self.assertIn("needs-transcription", self.extract(almost_all, image_pages=range(1, 9)).values())

    def test_force_scanned_overrides_the_ratio(self):
        pages = [self.BODY, self.BODY, self.BODY, self.BODY, ""]
        self.assertEqual(self.extract(pages, image_pages=(5,))[5], "empty")
        self.assertEqual(self.extract(pages, image_pages=(5,), force=True)[5], "needs-transcription")


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class ScanFlagAndFigurePages(KbCase):
    def test_scanned_flag_reprocesses_an_unchanged_deck(self):
        pdf = self.put("deck.pdf", fixture_build.build_text_pdf(
            [["A slide with enough text to count as text."]] * 4 + [["x"]]))
        first = self.ingest(pdf)["files"][0]
        self.assertEqual((first["needs_transcription"], first["empty_units"]), ([], []))
        forced = self.ok("ingest", "--kb", "t", "--scanned", str(pdf))["files"][0]
        self.assertEqual(forced["status"], "updated")
        self.assertEqual(forced["needs_transcription"], [5])

    def test_a_figure_page_can_be_described_on_request(self):
        self.ingest(FIX / "lecture.pdf")
        # lecture.pdf has no empty pages, so make one: ingest a deck where page 2 has no text
        pdf = self.put("fig.pdf", fixture_build.build_text_pdf(
            [["A slide with enough text to count as text."], [""], ["Another slide with enough text to count."]]))
        info = self.ingest(pdf)["files"][0]
        self.assertEqual(info["empty_units"], [2])
        reg = self.ok("register", "--kb", "t", "--source", "fig.pdf", "--page", "2",
                      stdin="[figure: photo of a Roman theatre stage wall with three levels of columns]")
        self.assertEqual(reg["origin"], "ai-transcription")


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class ScanCheckAndTranscriptionSafety(KbCase):
    def test_report_shows_the_scan_check_for_pdfs_only(self):
        info = self.ingest(FIX / "scanned.pdf", self.put("n.md", "A markdown note with enough text in it."))["files"]
        by_name = {f["display"]: f for f in info}
        self.assertEqual(by_name["scanned.pdf"]["scan_check"],
                         {"low_text_pages": [1, 2], "low_text_ratio": 1.0, "treated_as_scan": True})
        self.assertNotIn("scan_check", by_name["n.md"])

    def test_borderline_ratio_is_visible_in_the_report(self):
        pdf = self.put("mixed.pdf", fixture_build.build_text_pdf(
            [["A slide with enough text to count as text."]] * 3 + [[""]] * 2 + [["x"]] * 0))
        check = self.ingest(pdf)["files"][0]["scan_check"]
        self.assertEqual((check["low_text_ratio"], check["treated_as_scan"]), (0.4, False))
        self.assertEqual(check["low_text_pages"], [4, 5])

    def test_scanned_flag_keeps_transcriptions_of_unchanged_content(self):
        self.ingest(FIX / "scanned.pdf")
        self.ok("render", "--kb", "t", "--source", "scanned.pdf", "--pages", "1")
        self.ok("register", "--kb", "t", "--source", "scanned.pdf", "--page", "1",
                stdin="Mitochondria produce ATP through oxidative phosphorylation.")
        again = self.ok("ingest", "--kb", "t", "--scanned", str(FIX / "scanned.pdf"))["files"][0]
        self.assertEqual(again["ai_transcribed"], [1])
        self.assertEqual(again["needs_transcription"], [2])
        self.assertNotIn("dropped_transcriptions", again)
        self.assertEqual(len(self.ok("search", "--kb", "t", "oxidative")["hits"]), 1)

    def test_changed_content_still_drops_stale_transcriptions(self):
        pdf = self.put("s.pdf", b"")
        shutil.copy(FIX / "scanned.pdf", pdf)
        self.ingest(pdf)
        self.ok("register", "--kb", "t", "--source", "s.pdf", "--page", "1", stdin="Some transcribed page text here.")
        shutil.copy(FIX / "lecture.pdf", pdf)
        again = self.ingest(pdf)["files"][0]
        self.assertEqual((again["status"], again["dropped_transcriptions"]), ("updated", 1))


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class TwoColumnVerify(KbCase):
    """Real slide decks put text in two columns; layout mode interleaves them and used to fail true quotes."""

    LEFT = ["Immersion is a pre-requisite of", "presence, so build immersion first.", "Plain closing line on the left."]
    RIGHT = ["Presence is the sense of being", "there in the virtual environment.", "Embodiment is a contributor."]
    QUOTE = "Immersion is a pre-requisite of presence, so build immersion first."

    def setUp(self):
        super().setUp()
        self.pdf = self.put("cols.pdf", fixture_build.build_two_column_pdf(self.LEFT, self.RIGHT))

    def check(self, quote):
        return self.verify([{"quote": quote, "source": "cols.pdf", "page": 1}])[0]["status"]

    def test_the_fixture_really_interleaves_in_layout_text(self):
        self.ingest(self.pdf)
        text = self.ok("get", "--kb", "t", "--source", "cols.pdf", "--page", "1")["text"]
        self.assertFalse(kb.match_in_order(kb.page_variants(text), kb.segments(self.QUOTE), False)[0])

    def test_true_quote_is_verified_through_the_reading_order_text(self):
        self.ingest(self.pdf)
        self.assertEqual(self.check(self.QUOTE), "verified")
        self.assertEqual(self.check("Presence is the sense of being there in the virtual environment."), "verified")

    def test_a_made_up_quote_is_still_rejected_and_columns_are_not_blended(self):
        self.ingest(self.pdf)
        self.assertEqual(self.check("Immersion is a pre-requisite of Presence is the sense of being"), "not_found")
        self.assertEqual(self.check("Immersion is a pre-requisite of absence, so build immersion first."), "not_found")
        # the end of one column and the start of the next are separate blocks in reading order
        self.assertEqual(self.check("Plain closing line on the left. Presence is the sense of being"), "not_found")
        self.assertEqual(self.check("presence, so build immersion first. Plain closing line on the left."), "verified")

    def test_pdf_indexed_before_the_reading_order_text_existed_is_refreshed_once(self):
        self.ingest(self.pdf)
        with kb.connect(self.root, "t") as conn:
            conn.execute("UPDATE pages SET alt_text = NULL")
        self.assertEqual(self.check(self.QUOTE), "not_found")  # old index: layout text only
        old = self.verify([{"quote": self.QUOTE, "source": "cols.pdf", "page": 1}])[0]
        self.assertIn("run ingest", old["detail"])
        self.assertEqual(self.ingest(self.pdf)["files"][0]["status"], "updated")
        self.assertEqual(self.check(self.QUOTE), "verified")
        self.assertEqual(self.ingest(self.pdf)["files"][0]["status"], "unchanged")

    def test_old_database_without_the_column_is_migrated(self):
        import sqlite3 as sq
        db = Path(self.root) / ".tutor" / "kb" / "old"
        db.mkdir(parents=True)
        conn = sq.connect(db / "index.sqlite")
        conn.executescript("""
            CREATE TABLE sources (source_id TEXT PRIMARY KEY, display TEXT, path TEXT, sha256 TEXT, kind TEXT,
                                  unit TEXT, units INTEGER, ingested_at REAL);
            CREATE TABLE pages (source_id TEXT NOT NULL, num INTEGER NOT NULL, origin TEXT NOT NULL,
                                text TEXT NOT NULL, PRIMARY KEY (source_id, num));""")
        conn.commit()
        conn.close()
        self.assertEqual(self.ok("status", "--kb", "old")["sources"], [])
        with kb.connect(self.root, "old") as c2:
            self.assertIn("alt_text", {r[1] for r in c2.execute("PRAGMA table_info(pages)")})

    def test_mismatched_page_counts_store_empty_alt_text_and_are_not_retried(self):
        with mock.patch.object(kb, "run_tool") as run:
            def fake(cmd, hint, timeout=300):
                if cmd[0] == "pdfimages":
                    return "page num type width height\n----\n"
                return "one\fTwo pages here with enough text to count.\f" if "-layout" in cmd else "only one page\f"
            run.side_effect = fake
            kind, units, notes = kb.extract_pdf(Path("x.pdf"))
        self.assertEqual(notes["_alt_texts"], {1: "", 2: ""})  # '' = not possible, never NULL
        self.assertTrue(notes["reading_order_unavailable"])


class VerifyGapRuleScope(KbCase):
    def test_wide_spacing_in_text_that_has_no_columns_does_not_block_a_true_quote(self):
        self.put("t.txt", "Result:    significant   at p < 0.05 in the second trial.")
        self.ingest(self.work / "t.txt")
        self.assertEqual(self.verify([{"quote": "Result: significant at p < 0.05", "source": "t.txt", "page": 1}])[0]["status"],
                         "verified")

    def test_a_quote_cannot_carry_the_boundary_marker(self):
        self.assertEqual(kb.normalize_quote("a\x00b"), "ab")

    @unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
    def test_wide_gap_still_blocks_a_pdf_quote_that_crosses_two_columns(self):
        self.ingest(self.put("c.pdf", fixture_build.build_two_column_pdf(
            ["Left column sentence one here", "left column sentence two here", "left three"],
            ["Right column sentence one here", "right column sentence two", "right three"])))
        q = "Left column sentence one here Right column sentence one here"
        self.assertEqual(self.verify([{"quote": q, "source": "c.pdf", "page": 1}])[0]["status"], "not_found")


class SectionStubs(KbCase):
    def test_heading_only_chunks_join_the_next_section(self):
        self.put("n.md", "# Title\n## Subtitle\nThe body of the first real section lives here.\n\n"
                         "## Second\nThe body of the second section is here too.\n")
        info = self.ingest(self.work / "n.md")["files"][0]
        self.assertEqual((info["units"], info["empty_units"]), (2, []))
        self.assertIn("# Title", self.ok("get", "--kb", "t", "--source", "n.md", "--page", "1")["text"])


if __name__ == "__main__":
    unittest.main()
