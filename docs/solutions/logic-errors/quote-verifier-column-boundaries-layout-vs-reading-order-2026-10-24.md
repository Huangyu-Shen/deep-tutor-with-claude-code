---
title: A PDF quote verifier needs both extractions and a hard boundary at every column break
date: 2026-10-24
category: logic-errors
module: scripts/kb.py quote verification
problem_type: logic_error
component: tooling
symptoms:
  - "True quotes from a two-column slide (Lecture 2, page 22) were rejected as not found"
  - "After the first fix, a quote made of the last line of one column plus the first line of the next verified"
  - "Layout text already let a quote run across side-by-side columns on one line"
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [pdf, pdftotext, quote-verification, columns, trust, fixtures, review]
---

# A PDF quote verifier needs both extractions and a hard boundary at every column break

## Problem

`kb.py verify` backs the plugin's main promise: a quote shown to the learner exists verbatim, in order, on the cited page. On the first real run of the installed plugin, two true quotes from a two-column slide failed. Fixing that opened a hole in the opposite direction, and the independent review found it, not the author's tests.

## Symptoms

- `pdftotext -layout` puts the two columns side by side line by line, so a sentence that wraps inside the left column comes out interleaved with the right column. "Immersion is a pre-requisite of presence" (Lecture 2, page 22) was `not_found`, although it is on the page.
- Storing the page in reading order too (`pdftotext` without `-layout`, column `alt_text`) fixed that. Reading order separates the columns with a blank line, and a quote such as "Plain closing line on the left. Presence is the sense of being" crossed that blank line and verified.
- In layout text the same blending was already possible when two columns sat on one line (`"... of       Presence is ..."`).

## What Didn't Work

- Verifying against reading-order text alone. Tables and some slides read better in layout, so the layout text stays a second haystack.
- Treating the false rejection and the false acceptance as one problem. Every loosening that makes true quotes pass also lets a blended quote pass unless the boundary between columns is marked.
- Relying on a fixture with only interleaved columns. It proved the false rejection was fixed and said nothing about blending.

## Solution

In `scripts/kb.py`:

- `extract_pdf` stores both texts: `text` (layout) and `alt_text` (reading order). `NULL` means never extracted; `''` means it could not be lined up page by page and is not retried. Old indexes re-read the PDF once on the next `ingest`; AI transcriptions are kept.
- `page_haystacks` returns the layout variants plus the reading-order variants, and `check_quote` accepts a match in any of them.
- Hard boundaries a quote cannot cross: a run of 3 or more spaces inside one line in PDF layout text (`_GAP`), and a blank line in reading-order text (`_BLOCK`). Both insert a `\x00` marker; `normalize_quote` strips `\x00` from quotes so a quote cannot carry the marker.
- The gap rule applies only to PDF text-layer pages. In Markdown, Office text and AI transcriptions a run of spaces is just spacing.
- A failed check on an index that predates `alt_text` says so (`detail`: run `ingest` once), and the ingest report flags a PDF whose reading order could not be built (`reading_order_unavailable`).

## Why This Works

A quote is a contiguous stretch of one author's text. In a PDF, contiguous means within one column and one block, so the verifier has to know where blocks end. Layout text marks that with wide gaps, reading-order text with blank lines; marking both lets two imperfect extractions each cover what the other gets wrong without letting either blend two columns.

## Prevention

- Model both extractions in the fixture. `build_two_column_pdf` in `tests/fixtures/build.py` produces a page where layout text interleaves and reading-order text separates blocks, and `TwoColumnVerify` asserts the fixture really behaves that way before testing the verifier.
- For code that backs a trust claim, write the test that tries to fool it (blended, reversed, cross-column), not only the test that shows true quotes pass.
- Treat an independent review as part of finishing such code. The blend hole was reproduced by the reviewer in minutes and had passed 99 tests.
- Two same-release defects came from the same pattern, a safeguard that depended on the model behaving: the review skill printed real quiz answers as a "reply format example" (skills now use placeholders and never open the quiz answer-key folder in the learner's study folder), and a `for` loop or `;` chain in a Bash call did not match the pre-approved permission, so steps silently did not run (one script command per call). Test skills in real sessions and read `permission_denials`.

## Related Issues

- `docs/solutions/logic-errors/pdf-scan-detection-per-page-vs-document-level-2026-10-24.md`: same pattern, a heuristic validated only on synthetic fixtures.
- `CHANGELOG.md` 0.1.3; tests `TwoColumnVerify` and `VerifyGapRuleScope` in `tests/test_kb.py`.
