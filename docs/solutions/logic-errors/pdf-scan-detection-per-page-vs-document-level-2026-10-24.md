---
title: A per-page "is this page scanned?" rule flags every photo in a slide deck
date: 2026-10-24
category: logic-errors
module: scripts/kb.py PDF ingestion
problem_type: logic_error
component: tooling
symptoms:
  - "121 of 371 pages (33%) of a real 16-deck course were queued as needing transcription"
  - "The flagged pages were photos, title slides and empty video placeholders, none of them text"
  - "The same rule passed every synthetic fixture test"
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [pdf, ocr, scan-detection, heuristics, real-data, slide-decks]
---

# A per-page "is this page scanned?" rule flags every photo in a slide deck

## Problem

`kb.py ingest` decided page by page whether a PDF page was scanned text: a page with a large image and under 100 non-space characters of text layer went to `needs-transcription`. On the first real course (16 PDF decks, 371 pages) 121 pages were flagged. Transcribing them would have cost real model effort and filled the knowledge base with descriptions of photographs.

## Symptoms

- `pages_needing_transcription: 121` for decks that are plainly text-layer PDFs.
- Rendering two flagged pages showed a 3D reconstruction of a Roman theatre (Lecture 7, page 13) and a blank yellow placeholder slide (Lecture 9, page 5).
- All 88 tests were green, because the fixtures were built to match the rule (a fully image-only PDF, a one-page "footer over a big image" case).

## What Didn't Work

- Raising or lowering the per-page text threshold. A title slide, a captioned photo and a scanned page with a footer are all "big image, a line of text" on a single page; no threshold separates them.
- A first document-level ratio of 0.6. One deck of photos (Lecture 7) had 20 of 32 low-text pages (0.63) and was still classed as a scan.

## Solution

Decide at document level, in `extract_pdf` (`scripts/kb.py`):

- Mark each page low-text (no usable text layer, or a big image with under `IMAGE_PAGE_TEXT_MAX` characters).
- Treat the file as a scan only when the share of low-text pages reaches `SCAN_RATIO = 0.8`, or when the caller passes `ingest --scanned`.
- In a scan, low-text pages become `needs-transcription`. Otherwise a page keeps whatever text layer it has, and a page with no text is `empty`: reported as not indexed, and describable on request through `render` and `register`.
- The ingest report carries `scan_check` (`low_text_pages`, `low_text_ratio`, `treated_as_scan`) so a borderline file can be questioned, and re-ingesting unchanged content keeps existing AI transcriptions.

After the change the same course reports 0 pages to transcribe and 25 pages with no text.

## Why This Works

Whether a page holds scanned text or a figure is not visible without reading the image, but whether the whole file is a scan is: scans are image-only nearly throughout (the fixture scan is 1.0), while even the most picture-heavy deck seen was 0.63. The document-level share separates the two populations where a per-page signal cannot.

## Prevention

- Try any heuristic that classifies real files on a real sample before calling it done. Synthetic fixtures written to exercise a rule only prove the rule does what its author thought.
- Keep the evidence behind a threshold next to it. `SCAN_RATIO` has one data point (0.63 for the worst deck, from one course); the comment in `scripts/kb.py` says so, and `--scanned` is the escape hatch when it is wrong.
- When a rule can silently drop content, expose its inputs in the report (`scan_check`) rather than only its verdict.
- Update the skill text in the same change: `skills/kb/SKILL.md` still described the old per-page rule after the code changed, and only the review caught it.

## Related Issues

- `CHANGELOG.md` 0.1.1 and the addendum in `docs/verification/0.1.0.md`.
- Regression tests: `ScanDetection` and `ScanCheckAndTranscriptionSafety` in `tests/test_kb.py`.
