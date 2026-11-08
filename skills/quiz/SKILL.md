---
name: quiz
description: Quiz the user on their own course materials, grade the answers, log mistakes with a diagnosed cause, and re-test old mistakes. Use when the user asks to be tested, wants practice questions, self-test, exam preparation, or to retry questions they got wrong.
argument-hint: "[kb name] [topic] [number of questions]"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Bash(python3 *scripts/path.py*), Bash(python3 *scripts/bank.py*), Read, Edit(**/.tutor/quiz/*)
---

# Quiz

Adapted in part from DeepTutor's practice and error-diagnosis prompts and error taxonomy (Apache-2.0); see NOTICE.

Questions come from the user's knowledge base, every question carries its source page, and every result is written to the ledger. Write in the language the user's latest message is written in, and keep that language for questions, explanations, feedback and summaries alike: an English request gets an English quiz, a Chinese request gets a Chinese one, even when the skill file, the materials or earlier sessions are in another language. Do not switch languages on your own. Quote the materials verbatim in their own language and never translate a quote. Run commands from the user's project folder.

## 0. Active path

If the knowledge base has a learning path, it decides what to quiz. Check it first:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" status --kb NAME
```

If the command returns `ok: false`, show the error and stop. If `active_path_id` is null, continue with the rest of this skill exactly as before; the path is optional.

When a path is active:

- Only quiz knowledge points in `available` or `in_progress` modules. Pick the target from `next_action`; it already prefers an unfinished point whose in-module prerequisites are done.
- If the user names a topic, call `path.py resolve --kb NAME --kp "their topic"` (or `--module M`). `locked` means do not quiz it: show `locked_by` and `next_action`. `mastered` means point to `/tutor:review`. `already_passed` means that point passed but its module is still open: quiz the module's next unfinished point instead. `out_of_path` means it is not in the active path; offer to switch or deactivate the path with `/tutor:path`. `unknown_module` means no such module id: show the ids from `status` and ask. `evidence_invalid` means the citation is broken: do not ask about it. `allowed: true` with `reason: no_active_path` means the path is optional, so continue as before.
- Use the path's knowledge-point name and `knowledge_type`, and draw questions from its cited pages. Verify every quote you show against the page (`kb.py verify`) before asking; if the citation no longer matches, do not ask that question. Do not define or rename knowledge points; `save` already recorded them.
- Re-testing an open mistake that lives in a locked or out-of-path module belongs to `/tutor:review`, not this skill.
- After recording answers, run `path.py status` and show the module's `gaps` and the new `next_action`, rendering gap codes as the path skill does.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON or transcribed text on the command line or in a heredoc. Instead write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `event-1.json`, `event-2.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

Run **one script command per Bash call**: no `for` loops, no `;`, `&&` or `|` chains, no `cd` prefix. The pre-approved permissions match a single `python3 .../scripts/...` command, so a chained or piped command is refused and the step silently does not happen (for example, knowledge points that were never recorded). To summarise JSON, read the printed output; do not pipe it into another program. Use one payload file and one call for each event, and record each answer exactly once: if a record's outcome is unclear, run `ledger.py state` instead of recording again. A re-recorded `question_id` counts once in path gates. If a Write or a script command is refused by permissions, stop and tell the user; do not rename the file and retry, and do not skip the record.

## 1. Plan the session

1. Get a stamp: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" now`. Use its `stamp` for the quiz file name and for question ids.
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" errors`: mistakes and their status. Ignore `graduated` ones. Re-test the open ones (`active`, `retrying`, `review`) first, but not the same question twice in one sitting. Each error has the original `question_id` and `question` text.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" state`: existing knowledge points. **Reuse an existing name** when a question tests the same idea, so history stays together. With an active path, use the path's knowledge-point names instead.
4. Search the knowledge base for the topic (`kb.py search --kb NAME "query"`), as in the learn skill. With an active path, start from the target knowledge point's cited pages. Default to 5 questions unless the user says otherwise. If the materials do not cover the topic, say so; do not invent questions from general knowledge.

## 2. Write the questions

For each new question decide:
- the **knowledge point**: a short, specific name, and its type: `memory` (facts, terms), `concept` (understanding), `procedure` (steps, calculations), `design` (open-ended application);
- the **format**: `multiple_choice` (4 options, one clearly correct), `fill_in_blank`, or `short_answer`. Mix them; go from easier to harder; test understanding and application, not just wording;
- the **difficulty**: `easy`, `medium`, or `hard`;
- the **source**: the search result's `source_id`, file and page, and the passage the answer rests on;
- the **answer key**: for multiple choice and fill-in-the-blank, the answer and a one-sentence explanation; for short answer, a **checklist of 2 to 5 key points** written now, before you see the user's answer, and then fixed.
- the **origin**: `source_type` is `quiz` for a normal question and `mimic` for an AI variation of a confirmed reference question. A normal quiz question is always `answer_status: "sourced"`. A mimic question is `sourced` only when its answer is checked against the cited passage, otherwise `unverified`. `mimic` belongs to `source_type` only: the attempt's `origin` stays `quiz` (the ledger rejects `mimic`).

Give each question the id `<stamp>-q1`, `<stamp>-q2`, ... Ids must never repeat across sittings: the ledger treats the same id as the same question, so a reused id would count a new question as a retry of an old mistake.

Register each knowledge point once (skip if it already exists):

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "kp_define", "kb": "NAME", "name": "Short specific name", "knowledge_type": "concept", "sources": ["lecture.pdf page 3"]}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
```

The reply's `recorded.kp` is the id (`kb::lowercased name`) to use below. If the knowledge point already exists, take its id from `ledger.py state` instead; do not spell it by hand.

Before writing a new question, check the bank for a reusable one on the same knowledge point:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bank.py" pick --kb NAME --kp <recorded.kp> --limit 3
```

If one fits, verify its citation first: write the question's `source` object to `.tutor/tmp/quotes-<stamp>.json` as in the learn skill and run `kb.py verify --kb NAME < .tutor/tmp/quotes-<stamp>.json`. Do not show the quote or the key to the learner. If verification fails, run `bank.py invalidate --kb NAME --question-id ID`, skip that question and try the next; if it passes, reuse its prompt and `question_id` and keep the session moving. Do not read its answer until the user answers. It is already archived, so it does not go into the new quiz file.

Write the newly generated questions, including keys and checklists, to `.tutor/quiz/<stamp>.json` with the Write tool, as a list of objects with fields `question_id`, `kb`, `kp` (the recorded id), `kp_name`, `knowledge_type`, `format`, `difficulty`, `source_type`, `answer_status`, `prompt`, `options`, `answer`, `explanation`, `checklist`, `source`, `reference_id`. Use `null` for `options` on non-multiple-choice questions and for `reference_id` on normal questions. `source` is an object: `{"source_id": "...", "display": "lecture.pdf", "page": 3, "quote": "the passage the answer rests on"}`. This file is your fixed record; do not reword a key later. If your context was reset or you cannot see the key, re-read this file before grading. The key is visible if the user expands that tool call; say so if they ask for no spoilers.

Verify every new question's `source.quote` before archiving, using the same `kb.py verify` call. A question whose quote does not verify is not asked: fix the quote or the question, or drop it.

Archive the new questions before asking them:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bank.py" add --kb NAME < .tutor/quiz/<stamp>.json
```

`added` lists the new ids, `existing` means the file was already archived, and `near_duplicates` / `rejected` list questions that were not archived. For each id in those two lists, reuse the closest existing question or rewrite that prompt in the quiz file (nothing has been asked yet, so the key may still change) and run `add` again until both lists are empty. A structural error (invalid field, id conflict) fails the whole batch and writes nothing, so fix the file and re-run. Never ask a question that is not in the bank. If every question in the session is reused from the bank, there is no quiz file to write or archive.

## 2b. Reference questions and mimic questions

Use this when the user asks to find example questions in their materials or to be given questions like the ones in the book.

0. Run `ledger.py now` for `<stamp>` and register the knowledge point as in section 2 (or take its existing id from `ledger.py state`); `<recorded.kp>` below is that id.
1. Search the knowledge base for worked examples or exercises (`kb.py search --kb NAME "worked example"`, `"exercise"`, or the materials' own words). Read the promising pages with `kb.py get --kb NAME --source SOURCE_ID --page N`.
2. Extract each candidate reference question: its prompt, `format`, `difficulty`, knowledge point, and `source` object with the page and the exact passage. Verify every quote with `kb.py verify` before showing it.
3. Show the candidates and ask which ones are genuine reference questions. Only confirmed ones are stored. If the materials contain no example or exercise page, say so and stop; never invent a reference.
4. Save the confirmed ones:

Contents of `.tutor/tmp/reference-<stamp>.json`:

```json
{"references": [{"reference_id": "ref-<stamp>-1", "kp": "<recorded.kp>", "format": "short_answer",
  "difficulty": "medium", "prompt": "the original example question",
  "source": {"source_id": "lecture.pdf", "display": "lecture.pdf", "page": 3, "quote": "..."}}]}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bank.py" add-reference --kb NAME < .tutor/tmp/reference-<stamp>.json
```

5. To mimic a confirmed reference, generate a new question with the same `format` and `difficulty`. Label the prompt in the user's language as an AI variation, not the original (for example `[AI 变式,非原题]` or `[AI variation, not the original]`). Set `source_type: "mimic"`, point `reference_id` at the reference, and cite the reference's page with the passage the new answer rests on. If that passage verifies and supports the key, use `answer_status: "sourced"`; otherwise use `"unverified"` and say the key is an AI proposal that the materials do not confirm.
6. Archive the mimic questions with `bank.py add` exactly as in section 2, then ask them. The question header, the feedback and the bank listing all show the answer status.

## 2c. Browse the bank

When the user asks what is in the bank, or wants questions filtered by topic, format or difficulty:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bank.py" list --kb NAME [--kp ID] [--format multiple_choice|fill_in_blank|short_answer] [--difficulty easy|medium|hard] [--source-type quiz|mimic] [--answer-status sourced|unverified]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bank.py" references --kb NAME [--kp ID] [--format ...] [--difficulty ...]
```

`list` also shows `citation_ok`, `invalidated_at` and `source.quote`; `references` shows the confirmed reference questions. Show ids, knowledge points, formats, difficulties, answer status and whether the citation is still valid. Do not show `source.quote` or any key before the user has answered that question. To see one question, use `bank.py get --kb NAME --question-id ID` (no key); add `--with-answers` only for grading an answer already given.

## 3. Ask

Show one question at a time (or the whole set if the user prefers). **Show only the prompt and options. Never show answers, explanations, checklists or `source.quote` before the user has answered, and never hint at them.** A quote often contains the key word, so it is answer material even though `bank.py pick` returns it for verification. That includes the format examples you give: when you show how to reply, use a placeholder that is not the answer (`Q1: <your answer>`), never a real option letter or keyword from the key. The user may ask for a hint; count each hint in `hints_used`. Hinted answers are recorded but do not count toward the mastery estimate (only unaided attempts do, as in DeepTutor), and tell the user so. `bank.py list --kb NAME` and `bank.py pick` never include the key; do not call `bank.py get --kb NAME --question-id ID --with-answers` until the user has answered.

## 4. Grade

- **Multiple choice and fill-in-the-blank**: when the question is in a different language from the materials (for example a Chinese question about an English term), say in which language the blank should be answered, or accept the term in either language. You compare the answer to the stored key (ignore case and extra spaces; compare numbers by value, so 0.50 equals 0.5). `result` is `correct` or `incorrect`. This is your own comparison, not a script's, so be literal.
- **Short answer**: go through the stored checklist. For each point say hit or miss and, for a hit, quote the user's own words that cover it. `quality` = points hit divided by total points. `result`: `correct` if quality is at least 0.8, `partial` if at least 0.5, otherwise `incorrect`. Be strict about meaning, lenient about wording. Never award a point the user's text does not contain.

Record every answer:

Contents of `.tutor/tmp/event-2.json`:

```json
{"type": "attempt", "kp": "<recorded.kp>", "question_id": "<stamp>-q1", "result": "incorrect",
 "quality": 0.25, "hints_used": 0, "attempt_count": 1, "learner_answer": "...",
 "question": "the question text", "feedback": "...", "source": "deep_question", "assessment_type": "quiz",
 "origin": "quiz", "answer_status": "sourced", "error_type": "deviation",
 "ai_confirmation": "one sentence on why", "self_attribution": ""}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-2.json
```

Leave `quality` out for multiple choice and fill-in-the-blank (the scheduler derives it from `result`, `hints_used` and `attempt_count`). `error_type`, `ai_confirmation` and `self_attribution` only belong on `incorrect` answers. `attempt_count` is how many tries this question took in this sitting. Always include `question` so the error log is readable later. If the command returns an error, fix the event and record again; never skip recording.

Always include `answer_status`, copied from the question: `sourced` for a normal quiz question and for a mimic question whose key was checked against the materials, `unverified` for a mimic question with an AI-proposed key. An `unverified` answer still counts for mastery, the error log and the review schedule, but it **never opens a path gate**; say so when the user is working through a path.

## 5. Diagnose mistakes

For every `incorrect` answer choose one cause and say it in plain words:
- `structural`: a prerequisite idea or the overall structure is missing;
- `deviation`: they hold a wrong or distorted understanding of the idea;
- `application`: they understand the idea but used it wrongly (steps, calculation, wrong situation);
- `metacognitive`: they guessed, left it blank, or did not realise they did not know.

(The four names come from DeepTutor; the one-line definitions here are this plugin's own.) Then give feedback in three parts: the correct answer with its source page, why the user's answer fell short, and one concrete next step (a page to reread or an easier question to try). Optionally ask "What do you think went wrong?" and store their reply as `self_attribution`. A `partial` answer needs the same feedback on the missing points. For a mimic question, state whether the key was `sourced` or an `unverified` AI proposal, and do not present an unverified key as the materials' answer.

## 6. Close the session

Run `ledger.py state` and report from its output: per knowledge point, `open_errors` and the error records. With an active path, `mastery`, `gate.mastery`, `unaided_attempts`, the module `gaps` and `next_action` come from `path.py status`; without one, take `mastery` and `unaided_attempts` from `ledger.py state`. Say that mastery is capped until there are enough attempts and that one correct answer is weak evidence. Do not say a topic is "mastered" yourself. Offer `/tutor:review` for later; the next review date is in the `schedule.next_review_date` field.

## Re-testing mistakes

A mistake graduates only after two correct answers to that same `question_id` (a wrong or partial answer in between resets the count). When re-testing, ask the original question again, or the same idea in a different form, and record the attempt with the **original `question_id`** from `ledger.py errors`.

## Rules

- Never edit files under `.tutor/learner/` directly; only use `ledger.py`.
- Inspect the knowledge base only through `kb.py`; never run `sqlite3` or open `index.sqlite`, which permissions refuse.
- Answer keys: a new question's key is in `.tutor/quiz/<stamp>.json`; a reused bank question's key is in the bank. Read either only to grade an answer the user has already given. For a reused question, read the key with `bank.py get --kb NAME --question-id ID --with-answers` after the answer, and quote from it only then.
- Never edit `.tutor/question_bank/` by hand; only use `bank.py`. Archive every question before asking it, and reuse a bank question instead of writing a near-duplicate.
- The bank never returns answers through `list`, `pick` or a plain `get`; `get --with-answers` is for grading only.
