# Changelog

## 0.3.0

- New question bank, one versioned JSON file per knowledge base at `.tutor/question_bank/<kb>.json`. Every question `/tutor:quiz` asks is archived with its fixed answer key before it is shown, so later quizzes and reviews can reuse the same question and `question_id` instead of writing a near-duplicate. A missing file is an empty bank; an unknown `schema_version`, a damaged file or a mismatched knowledge base is an error, not an empty bank.
- New `scripts/bank.py`: `add`, `add-reference`, `list`, `get`, `pick`, `invalidate`, `revalidate` and `references`, JSON in and JSON out. `list`, `pick` and a plain `get` omit `answer`, `explanation` and `checklist`; only `get --with-answers` returns the key, for grading after the learner has answered. A short-answer question may carry a checklist instead of a model answer; multiple choice and fill-in-the-blank still need an answer. A stored entry missing a required field, whose id disagrees with its key, or whose `citation_ok` is not a boolean is reported as a damaged bank at read time instead of being returned or crashing a later command. `add-reference` is idempotent even when the same reference is re-submitted after `confirmed_at` has changed. The knowledge point id must be spelled exactly as the ledger spells it, and a mimic question must keep its reference's format and difficulty.
- New reference questions and mimic mode in `/tutor:quiz`. Reference questions are extracted from example/exercise pages already in the knowledge base, checked against the page, and saved only after the learner confirms them. A mimic question keeps the reference's format and difficulty, points back to its `reference_id`, and is labelled as an AI variation. Its key is `sourced` when the cited passage verifies and supports it, otherwise `unverified`; an unverified key is presented as an AI proposal, not as the materials' answer.
- Duplicate protection: the same `question_id` with the same content is a no-op, the same id with different content is an error, an exact normalized prompt under another id is rejected, and a character-bigram Jaccard similarity of 0.85 or more is rejected as a near-duplicate unless the caller passes `allow_near_duplicate: true` (a string such as `"false"` is an error, not a way to bypass the check). Prompt normalization folds case, punctuation and every kind of whitespace, including newlines and tabs, so such variants cannot enter as two questions. The closest existing `question_id` is returned. A batch is not lost to one bad prompt: clean questions are stored, duplicates and near-duplicates come back in `rejected` / `near_duplicates`, and only a structural error (an invalid field, or one id with two different contents in the same batch) fails the whole batch before anything is written.
- Attempt events gain an optional `answer_status` (`sourced` / `unverified`). `path.py` filters `unverified` attempts out of gate evidence, but mastery, the error log and review scheduling still count them. Events without the field, including all 0.1.x and 0.2.0 logs, keep their old gate results; an unknown value is likewise treated as not sourced and never opens a gate, and a malformed value on read no longer drops the whole attempt from mastery, the error log and the schedule. `SCHEMA_VERSION` stays 1 and no migration is needed.
- `/tutor:review` now prefers a bank question on the queued knowledge point, keeps its `question_id`, verifies the cited quote before asking, marks a question `citation_ok: false` when the page no longer supports it, and revalidates it when the citation returns. A question generated during review is archived as `source_type: "quiz"` before it is asked.
- Tests: `tests/test_bank.py` covers storage, duplicate rules, filters, answer hiding, citation invalidation and reference validation; `tests/test_ledger.py` and `tests/test_path.py` cover `answer_status` validation, gate exclusion, and the unchanged legacy-log behavior.

## 0.2.0

- New `/tutor:path`: create named learning paths from a goal and the materials in one knowledge base, confirm them module by module, switch the active path, and delete or rename definitions. A base can hold several paths; one is active at a time; progress is replayed per learner.
- Module states (`locked`, `available`, `in_progress`, `mastered`) are derived by `scripts/path.py` from the path definition plus the ledger. Nothing is stored: `status` replays the log in file order and always returns the same answer for the same log.
- Two-track gates. `memory`/`procedure` points pass at mastery ≥ 0.9 with the last three unaided attempts all correct (at least three); `concept`/`design` points need one Feynman explanation judged `correct`, with no hints. A module becomes mastered once every point passes, and only then do the modules that depend on it unlock. A later failed attempt does not take a mastered module back; points that slip are listed under `regressed`, and only a path edit or a lost citation recomputes it.
- `/tutor:learn` and `/tutor:quiz` read the active path, work only in `available`/`in_progress` modules, refuse locked, mastered or out-of-path topics with the reason and the next action, and report the remaining gaps after each answer. `/tutor:review` stays outside module locks. Without an active path the four older skills behave as in 0.1.4.
- Starting diagnostic: 5–10 cited questions across the path, recorded with `origin: diagnostic`. It only locates a starting point: these attempts appear in mastery but never open a gate and never mark a module in progress.
- Module exemption is a separate action: 3–5 cited questions per `memory`/`procedure` point plus one Feynman explanation per `concept`/`design` point, with `origin: exemption`. Failure keeps the module available with its gaps; a retry uses new questions and has no cooldown.
- Saving a path validates it before writing: real non-empty pages from the knowledge base, unique ids and names, no unknown, self or cyclic prerequisites, and no conflicting knowledge types. The file is written atomically; a damaged file or a dangling active pointer is an error, not an empty path list.
- Editing a path recomputes from the new definition: new points reopen a mastered module and relock its dependants. A citation that no longer resolves marks the point `evidence_invalid`, blocks its gate and propagates `invalidated_by` downstream until the citation is fixed or the base is rebuilt.
- New `scripts/learner_log.py` (shared event contract, `origin` field, one `question_id` per attempt in path gates) and `scripts/path.py`. `ledger.py` now imports the shared library; its CLI and stored schema are unchanged (`SCHEMA_VERSION` stays 1, `origin` is optional), so 0.1.x `.tutor` data needs no migration. On read, an origin this version does not know is kept as mastery evidence, never opens a gate, and is reported as a warning; a line that is not UTF-8 is reported and skipped instead of failing the whole command.
- Fixes found in live runs: skills no longer inspect `index.sqlite` with `sqlite3` or pipe a script into another program (both are refused by the pre-approved permissions); a re-recorded `question_id` counts once in gates; the output language now follows the user's latest message even when the skill file or earlier sessions are in another language.
- Review hardening: a hinted retry of the same `question_id` no longer erases the unaided answer; an attempt with no `question_id` shows in mastery but never opens a gate; a hinted Feynman explanation cannot pass a `concept`/`design` gate; a qualitative `incorrect`/`partial` on a `memory`/`procedure` point blocks its gate; a failed exemption leaves the module `available` with `exemption_available` still true; `resolve` refuses a point that already passed, a module or point with broken evidence, and an unknown module id; `deactivate` is the repair for a dangling active pointer and every store command now requires an ingested knowledge base; `status --all` reports every path; `validate` and `save` report `affected_modules`, and a failed save names the definitions it already wrote.

## 0.1.4

- Fix: `verify` reported a quote as `verified_elided` with an empty `skipped_text` when the ellipses in it skipped no text at all. Such a quote is now plain `verified` (or `verified_case_insensitive`); `skipped_text` is only emitted when something was actually skipped.
- Fix: `search --mode all` had broken semantics for mixed Latin/CJK queries: Latin words only constrained the token-based hits and CJK terms only constrained the substring-fallback hits, so the result could include pages missing some query words. Now every hit must contain every query word; CJK-derived hits are scored on the same -bm25 scale so `--limit` keeps the genuinely best pages, and duplicated query terms no longer inflate the check.
- Known limit (strict side, kept on purpose): in `--mode all`, a Latin word glued to CJK inside one token (e.g. `ATP合成` vs query `ATP 合成`) is not matched by the tokenizer and the page is excluded; default `any` mode still finds it. A dropped true match is safer than an accepted fake one.

## 0.1.3

Found by the first run of the installed plugin on a real course.

- Fix: the quiz answer key could leak. The review skill showed real answers as a "reply format example". Skills now use placeholders only and the review skill never opens `.tutor/quiz/`.
- Fix: quote verification failed true quotes on two-column slides, because layout-mode text interleaves the columns. PDFs now also store the page in reading order and `verify` accepts a match in either text. A quote can no longer run across a wide gap inside one line (side-by-side columns or table cells), which also closes a gap where text from two columns could be joined into a fake sentence. Existing knowledge bases upgrade on the next `ingest` (the PDFs are re-read once; transcriptions are kept).
- Change: explanations, questions and feedback now follow the language the user writes in (before: English by default). Quotes stay in their own language.
- Fix: heading-only Markdown sections are no longer reported as pages without text.
- Hardening from review: in reading-order text a blank line (the gap between two columns) is also a boundary quotes cannot cross; the column rules apply only to PDF text, so wide spacing in Markdown or transcribed text no longer blocks a true quote; a failed check on an old index says to run `ingest` once; the report flags a PDF whose reading-order text could not be built (`reading_order_unavailable`).
- Skills: run one script command per call (loops and chains were refused by the pre-approved permissions, so some steps silently did not run).

## 0.1.2

- Distribution: the repository is now also a plugin marketplace (`.claude-plugin/marketplace.json`), so the plugin installs with `claude plugin install`. Install was tested from a clean copy of the tracked files in an isolated Claude Code config.
- Docs: README install steps; note that the plugin cannot keep its `.tutor/` data inside its own directory (Claude Code refuses those writes), so use a separate study folder.

## 0.1.1

Found by trying 0.1.0 on a real course (16 PDF slide decks, 371 pages).

- Fix: a PDF is treated as scanned only when at least 80% of its pages have almost no text. Before, any page with a big image and little text was flagged, so 121 of 371 slide pages (photos, title slides, empty video placeholders) were queued for transcription.
- Pages with a short text layer keep it; pages with no text are `empty` (reported as not indexed) and can be described on request.
- `ingest --scanned` forces scan treatment for a file; re-running it keeps existing AI transcriptions of unchanged content.
- The ingest report gains `scan_check` (low-text pages and ratio) so a borderline file can be questioned.

## 0.1.0

- `/tutor:kb`: page-indexed local knowledge base from PDF, pptx, docx, xlsx, Markdown and text; scanned PDF pages are read by Claude from rendered images and cached (marked as AI transcription).
- `/tutor:learn`: explanations from the materials with page citations; quotes are verified against the stored text before being shown.
- `/tutor:quiz`: questions with fixed answer checklists, error log with four error types, re-testing until two correct answers.
- `/tutor:review`: review queue ordered by forgetting risk.
- Mastery and scheduler ported from DeepTutor 1.6.15 and checked against golden vectors generated by the original code.
