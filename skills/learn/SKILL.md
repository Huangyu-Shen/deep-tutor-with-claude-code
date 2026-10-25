---
name: learn
description: Explain a concept or answer a study question using the user's own course materials, with page citations whose quotes are checked against the source text. Use when the user asks to explain, clarify, summarise or understand something from their lectures, slides, papers or textbook, or wants to check their own understanding.
argument-hint: "[kb name] <question or topic>"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Bash(python3 *scripts/path.py*), Read
---

# Learn from the materials

Adapted in part from DeepTutor's explain and Feynman prompts (Apache-2.0); see NOTICE.

Teach from the user's knowledge base, not from memory. Write in the language the user's latest message is written in, and keep that language for explanations, questions, feedback and summaries alike: an English request gets an English lesson, a Chinese request gets a Chinese one, even when the skill file, the materials or earlier sessions are in another language. Do not switch languages on your own. Quote the materials verbatim in their own language and never translate a quote.

Run commands from the user's project folder. If no knowledge base exists (`kb.py list` is empty), tell the user to run `/tutor:kb` first.

## 0. Active path

If the knowledge base has a learning path, it decides what to teach. Check it first:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" status --kb NAME
```

If the command returns `ok: false`, show the error and stop. If `active_path_id` is null, or the command is unavailable because none of the path commands apply, continue with sections 1–5 exactly as before: the path is optional and the free flow is unchanged.

When a path is active:

- Pick the target from `next_action` (module, knowledge point, `learn`/`quiz`). If the user names a topic instead, call `path.py resolve --kb NAME --kp "their topic"` (or `--module M`).
- `allowed: true` with `reason: no_active_path`: no path is active, so continue with sections 1–5 exactly as before.
- `allowed: false` with `reason: locked`: do not explain it. Say which modules in `locked_by` are unfinished and show `next_action`.
- `allowed: false` with `reason: mastered`: the module is complete; do not re-teach it, point to `/tutor:review`.
- `allowed: false` with `reason: already_passed`: that knowledge point already passed its gate but its module is still open. Do not re-teach it; pick the module's next unfinished point from `status` or point to `/tutor:review`.
- `allowed: false` with `reason: out_of_path`: say it is not in the active path, and offer to switch or deactivate the path with `/tutor:path`. Never silently ignore the path.
- `allowed: false` with `reason: unknown_module`: no such module id in the active path; show the module ids from `status` and ask which one.
- `allowed: false` with `reason: evidence_invalid`: the citation is broken; say so, skip the point and report `invalidated_by`; no teaching can fix it.
- `allowed: true`: teach that knowledge point. Use the name, `knowledge_type`, `objective` and cited pages from the path definition; never rename or redefine it here. Only `available` and `in_progress` modules are teachable.
- After recording an answer or a Feynman check, run `status` again and show the module's remaining `gaps` and the new `next_action`. Render gap codes as the path skill does (`mastery_below`, `unaided_below`, `recent_failure`, `qualitative_missing`, `qualitative_not_correct`, `qualitative_failure`, `evidence_invalid`).
- If a knowledge point's `evidence` is invalid, say the citation is broken and skip it; do not teach from an unverifiable page.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON or transcribed text on the command line or in a heredoc. Instead write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `event-1.json`, `event-2.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

Run **one script command per Bash call**: no `for` loops, no `;`, `&&` or `|` chains, no `cd` prefix. The pre-approved permissions match a single `python3 .../scripts/...` command, so a chained or piped command is refused and the step silently does not happen (for example, knowledge points that were never recorded). To summarise JSON, read the printed output; do not pipe it into another program. Use one payload file and one call for each event, and record each answer exactly once: if a record's outcome is unclear, run `ledger.py state` instead of recording again. A re-recorded `question_id` counts once in path gates.

## 1. Find the sources

Rewrite the question into 2 to 4 short search queries (synonyms, the technical term and a plain-language version), then:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" search --kb NAME "first query" --query "second query" --limit 6
```

Read the returned page texts. If a hit has `text_truncated: true`, read the whole page with `kb.py get --kb NAME --source SOURCE_ID --page N` before explaining from it. If the hits do not cover the question, search again with different terms before concluding anything. If you still find nothing, say plainly that the materials do not cover it. Use `get` for a neighbouring page when an explanation continues over a page break.

When a path is active, search with the target knowledge point's name and `objective`, and read the pages in its `evidence` list first: those are the pages the path is built on.

## 2. Explain

1. Open with an accessible analogy.
2. Give the precise definition, in the materials' own terms.
3. Give one or two concrete examples, preferably the materials' own.
4. Name the common misconception or confusion.
5. Keep it approachable; do not pad.

Write formulas as LaTeX between dollar signs.

## 3. Cite, then verify

Every claim that comes from the materials gets a citation in the form `(lecture.pdf, page 3)` using the `citation` field from the search. When you quote, quote exactly: a short phrase or sentence copied from the page text.

**Before you send the answer**, check every quote you plan to show. Use the hit's `source_id` as `source` (display names can be ambiguous):

Contents of `.tutor/tmp/quotes-1.json`:

```json
{"quotes": [{"quote": "exact words", "source": "lecture-pdf", "page": 3}]}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" verify --kb NAME < .tutor/tmp/quotes-1.json
```

- Show a quote only if its status is `verified`, `verified_case_insensitive` or `verified_elided`. Fix or drop anything else; for `wrong_page` use the page in `found_on`.
- `verified_elided` means your quote used `...`. Read `skipped_text`: if the skipped words change the meaning (a "not", a condition, a number), do not use the ellipsis; quote the full sentence instead. Keep the `...` visible in what you show.
- Quotes that are too short are refused: paraphrase and cite instead.
- If a result has `low_confidence`, the page is an AI transcription of a scan: say the citation comes from a transcribed scan and may contain reading errors.

Verification covers quoted strings only. A paraphrase with a page number is not checked, so keep paraphrases close to the source and never attach a page number to something the page does not say.

## 4. Mark what the materials do not say

Anything you add from general knowledge goes in a clearly labelled part, "Beyond your materials:", with no citation. If two sources disagree, show both.

## 5. Understanding check (Feynman)

**When a path is active and the target knowledge point is `concept` or `design`, this check is required**: that point cannot pass its gate without a qualitative attempt, so do not end the explanation without asking for a restatement. Ask: "Explain it back in your own words so I can check it." If the user declines, stop there: say the point stays incomplete and write nothing. Never write a pass for an explanation the user did not give.

For `memory`/`procedure` points, and when no path is active, the check stays optional: offer it after the explanation.

Wait for their explanation, then judge it on three things: can they say it in simple language, is anything important missing, is anything wrong. Quote their own words when you point out a gap. Give specific feedback and the missing link; do not just say "good". A pass requires their own words, no important omission and no real misunderstanding.

If the user wants this tracked (or the path requires it), record it. Reuse the path's knowledge point when a path is active; otherwise reuse an existing name if `ledger.py state` lists one for the same idea, or pick a short, specific name and a type (`memory`, `concept`, `procedure`, `design`):

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "kp_define", "kb": "NAME", "name": "Short specific name", "knowledge_type": "concept", "sources": ["lecture.pdf page 3"]}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
```

The reply contains `recorded.kp`; use it as `kp` below. Get a stamp for the question id from `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" now` (its `stamp` field), then record the check as a qualitative attempt:

Contents of `.tutor/tmp/event-2.json`:

```json
{"type": "attempt", "kp": "<recorded.kp>", "question_id": "<stamp>-feynman", "result": "partial", "quality": 0.6,
 "assessment_type": "qualitative", "origin": "learn", "learner_answer": "their explanation",
 "question": "Explain ... in your own words", "feedback": "the gap in one sentence"}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-2.json
```

`result` is `correct` at quality 0.8 or above, `partial` from 0.5 up to 0.8, otherwise `incorrect` (then add `error_type`, `ai_confirmation`, see the quiz skill). A new `question_id` per check keeps separate checks from being merged into one error.

If the check does not pass and a path is active, do not move on: explain the same point a different way, then ask for another restatement. The retry count is the number of qualitative events for that point, which `path.py status` shows; the point stays incomplete until a check passes.

## Rules

- Never open files in `.tutor/quiz/`; they hold answer keys for quizzes the user may not have answered.
- Do not claim the user has mastered something. With a path active, `path.py status` is the only source for gates, module states and the numbers to report (`mastery`, `gate.mastery`, `unaided_attempts`); without one, `ledger.py state` is. Never compute either yourself.
- Never edit files under `.tutor/` directly. Inspect the knowledge base only through `kb.py`; never run `sqlite3` or open `index.sqlite`, which permissions refuse.
