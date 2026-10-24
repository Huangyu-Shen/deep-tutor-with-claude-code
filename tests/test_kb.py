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
            kind, units = kb.extract_pdf(Path("x.pdf"))
        self.assertEqual(units[0][2], "needs-transcription")


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


if __name__ == "__main__":
    unittest.main()
