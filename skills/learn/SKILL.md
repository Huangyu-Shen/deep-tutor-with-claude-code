---
name: learn
description: Explain a concept or answer a study question using the user's own course materials, with page citations whose quotes are checked against the source text. Use when the user asks to explain, clarify, summarise or understand something from their lectures, slides, papers or textbook, or wants to check their own understanding.
argument-hint: "[kb name] <question or topic>"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Read
---

# Learn from the materials

Adapted in part from DeepTutor's explain and Feynman prompts (Apache-2.0); see NOTICE.

Teach from the user's knowledge base, not from memory. Write in **English** unless the user asks for another language; keep wording clear because the user is also practising English.

Run commands from the user's project folder. If no knowledge base exists (`kb.py list` is empty), tell the user to run `/tutor:kb` first.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON or transcribed text on the command line or in a heredoc. Instead write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `event-1.json`, `event-2.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

## 1. Find the sources

Rewrite the question into 2 to 4 short search queries (synonyms, the technical term and a plain-language version), then:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" search --kb NAME "first query" --query "second query" --limit 6
```

Read the returned page texts. If a hit has `text_truncated: true`, read the whole page with `kb.py get --kb NAME --source SOURCE_ID --page N` before explaining from it. If the hits do not cover the question, search again with different terms before concluding anything. If you still find nothing, say plainly that the materials do not cover it. Use `get` for a neighbouring page when an explanation continues over a page break.

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

## 5. Optional understanding check (Feynman)

After an explanation, offer: "Want to explain it back in your own words so I can check it?" If yes, wait for their explanation, then judge it on three things: can they say it in simple language, is anything important missing, is anything wrong. Quote their own words when you point out a gap. Give specific feedback and the missing link; do not just say "good".

If the user wants this tracked, record it. Reuse an existing knowledge-point name if `ledger.py state` lists one for the same idea; otherwise pick a short, specific name and a type (`memory`, `concept`, `procedure`, `design`):

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "kp_define", "kb": "NAME", "name": "Short specific name", "knowledge_type": "concept", "sources": ["lecture.pdf page 3"]}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
```

The reply contains `recorded.kp`; use it as `kp` below. Get a stamp for the question id from `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" now` (its `stamp` field), then record the check as a qualitative attempt:

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "attempt", "kp": "<recorded.kp>", "question_id": "<stamp>-feynman", "result": "partial", "quality": 0.6,
 "assessment_type": "qualitative", "source": "learn", "learner_answer": "their explanation",
 "question": "Explain ... in your own words", "feedback": "the gap in one sentence"}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
```

`result` is `correct` at quality 0.8 or above, `partial` from 0.5 up to 0.8, otherwise `incorrect` (then add `error_type`, `ai_confirmation`, see the quiz skill). A new `question_id` per check keeps separate checks from being merged into one error.

## Rules

- Do not claim the user has mastered something. Only `ledger.py state` reports mastery, and only from recorded, unaided attempts.
- Never edit files under `.tutor/` directly.
