---
title: A store that rewrites a file from its parsed model must round-trip every accepted value or refuse the write
date: 2026-11-22
category: logic-errors
module: scripts/memory_doc.py and scripts/memory.py (memory documents)
problem_type: logic_error
component: tooling
symptoms:
  - "A preference containing a line break was reported as added, then missing on the next read, and a heading inside the text appeared as a phantom section"
  - "One accepted preference write erased a hand-written note, an anchor-less bullet and a whole section from the same file"
  - "A refused batch looked safe in tests but would have rewritten a file the learner had formatted by hand"
  - "A helper named fail in a test base class replaced unittest's fail, so failing string assertions silently passed"
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags:
  - round-trip
  - serialize-parse
  - rewrite-from-model
  - data-loss
  - markdown-injection
  - atomic-writes
  - mutation-testing
  - test-helper-shadowing
---

## Problem

`memory.py` keeps memory as Markdown (`- text [^1] <!--m_ID-->`). Every write — `ops`, `preference`, `entry-delete` — reads the file, parses it into a `Document`, changes the model, and writes `serialize(doc)` back. The ported upstream code (DeepTutor `services/memory/{document,ops}.py`) checks the length of entry text and nothing else, and its parser silently skips any line it does not recognise.

Two things follow from that shape, and neither shows up in a unit test of `parse`/`serialize` on well-formed input:

1. **Accepted values that do not survive the next parse.** Text such as `line1\n## Injected\nline2` passed validation, was written, and the command returned `added`. The next read saw a section called `Injected` and lost the entry. A text ending in a marker-looking `[^1]`, or containing `<!--`, has the same family of problems.
2. **Rewrites that drop what the parser skipped.** The model only contains what `parse` understood. A file with a hand note, a bullet without an id anchor, or a section of free prose lost all of it on the first accepted write. Because the damage happens on a *successful* write, no error is ever shown.

The code review found both (correctness, security and adversarial reviewers each reproduced them) after 345 passing tests, golden vectors taken from upstream, a 57-mutant check and seven live sessions. All of that exercised inputs the model could already represent.

## Solution

Treat the parsed model as a lossy view and make the write path prove it is not losing anything.

- **Validate at the boundary that the value fits the format.** `memory_doc._one_line` rejects text and section names with any line break (including `\x85`, ` `, ` `) or `<!--` / `-->`; `preference` collapses whitespace to one line first because a preference never needs a break. The id and ref patterns use `\Z` instead of `$`, since `$` accepts a trailing newline. This is a deliberate difference from upstream and is listed in `NOTICE` and `docs/upstream-behavior/memory.md`.
- **Refuse to rewrite a file the parser cannot fully account for.** `memory_doc.unparsed_lines(text)` returns the non-blank lines `parse` would drop; `memory.load_doc(..., for_write=True)` raises, naming the first such line, and points at `doc-put` (whole-document overwrite, which is the learner's explicit choice). Reads (`doc-get`, `read-l3`) are not affected.
- **A rejected batch must not touch the file at all.** A test that checks "bytes unchanged after a refused batch" only means something if the starting file is not already in canonical form; otherwise `write(serialize(parse(x)))` is a no-op and a mutant that writes anyway survives. The test uses a file that parses fully but would be renumbered by a rewrite (`[^2]` where `serialize` writes `[^1]`).
- **Do not let a test helper shadow the framework.** The CLI test base class defined `fail(...)` to run a command and assert exit code 1. `unittest` calls `self.fail(msg)` when a string `assertEqual` fails, so every failing string comparison ran a harmless CLI call instead and the test passed. It was found because one mutant that should have died survived. The helper is now `refused`.

## Prevention

- For any store that parses a file, edits a model and writes it back, add three tests before writing the feature: *every accepted value round-trips* (`parse(serialize(doc))` keeps every entry), *a document with content the parser skips is refused on write and left byte-identical*, and *a rejected write leaves a non-canonical file byte-identical*.
- Property to check in review: for each validator, ask "can an accepted value change how the *next* parse reads the file?" Line breaks, comment markers, heading markers and anchors in free text are the usual suspects.
- Golden vectors from upstream prove you match upstream on inputs upstream tests; they say nothing about inputs upstream accepts and then mishandles. Take the extra validation as a recorded deviation, not as drift.
- Run a mutation check on the test base classes' helper names too: never name a helper `fail`, `run`, `id`, `skipTest` or another `TestCase` attribute.
- Judged against the learnings in `docs/solutions/logic-errors/answer-status-fail-closed-gates-tolerant-records.md`: reads stay tolerant (a document with extra lines can still be shown), writes are strict. The new part is that "strict write" has to include *what the write would silently delete*, not only what it would add.
