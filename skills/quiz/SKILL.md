---
name: quiz
description: Quiz the user on their own course materials, grade the answers, log mistakes with a diagnosed cause, and re-test old mistakes. Use when the user asks to be tested, wants practice questions, self-test, exam preparation, or to retry questions they got wrong.
argument-hint: "[kb name] [topic] [number of questions]"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Bash(python3 *scripts/path.py*), Read, Edit(**/.tutor/quiz/*)
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

Run **one script command per Bash call**: no `for` loops, no `;`, `&&` or `|` chains, no `cd` prefix. The pre-approved permissions match a single `python3 .../scripts/...` command, so a chained or piped command is refused and the step silently does not happen (for example, knowledge points that were never recorded). To summarise JSON, read the printed output; do not pipe it into another program. Use one payload file and one call for each event, and record each answer exactly once: if a record's outcome is unclear, run `ledger.py state` instead of recording again. A re-recorded `question_id` counts once in path gates.

## 1. Plan the session

1. Get a stamp: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" now`. Use its `stamp` for the quiz file name and for question ids.
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" errors`: mistakes and their status. Ignore `graduated` ones. Re-test the open ones (`active`, `retrying`, `review`) first, but not the same question twice in one sitting. Each error has the original `question_id` and `question` text.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" state`: existing knowledge points. **Reuse an existing name** when a question tests the same idea, so history stays together. With an active path, use the path's knowledge-point names instead.
4. Search the knowledge base for the topic (`kb.py search --kb NAME "query"`), as in the learn skill. With an active path, start from the target knowledge point's cited pages. Default to 5 questions unless the user says otherwise. If the materials do not cover the topic, say so; do not invent questions from general knowledge.

## 2. Write the questions

For each new question decide:
- the **knowledge point**: a short, specific name, and its type: `memory` (facts, terms), `concept` (understanding), `procedure` (steps, calculations), `design` (open-ended application);
- the **format**: multiple choice (4 options, one clearly correct), fill-in-the-blank, or short answer. Mix them; go from easier to harder; test understanding and application, not just wording;
- the **source**: file and page from the search result, and the passage the answer rests on;
- the **answer key**: for multiple choice and fill-in-the-blank, the answer and a one-sentence explanation; for short answer, a **checklist of 2 to 5 key points** written now, before you see the user's answer, and then fixed.

Give each question the id `<stamp>-q1`, `<stamp>-q2`, ... Ids must never repeat across sittings: the ledger treats the same id as the same question, so a reused id would count a new question as a retry of an old mistake.

Write the whole set, including keys and checklists, to `.tutor/quiz/<stamp>.json` with the Write tool, as a list of objects with fields `question_id`, `kb`, `kp_name`, `knowledge_type`, `format`, `prompt`, `options`, `answer`, `explanation`, `checklist`, `source`. This file is your fixed record; do not reword a key later. If your context was reset or you cannot see the key, re-read this file before grading. The key is visible if the user expands that tool call; say so if they ask for no spoilers.

Register each knowledge point once (skip if it already exists):

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "kp_define", "kb": "NAME", "name": "Short specific name", "knowledge_type": "concept", "sources": ["lecture.pdf page 3"]}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
```

The reply's `recorded.kp` is the id (`kb::lowercased name`) to use below.

## 3. Ask

Show one question at a time (or the whole set if the user prefers). **Show only the prompt and options. Never show answers, explanations or checklists before the user has answered, and never hint at them.** That includes the format examples you give: when you show how to reply, use a placeholder that is not the answer (`Q1: <your answer>`), never a real option letter or keyword from the key. The user may ask for a hint; count each hint in `hints_used`. Hinted answers are recorded but do not count toward the mastery estimate (only unaided attempts do, as in DeepTutor), and tell the user so.

## 4. Grade

- **Multiple choice and fill-in-the-blank**: when the question is in a different language from the materials (for example a Chinese question about an English term), say in which language the blank should be answered, or accept the term in either language. You compare the answer to the stored key (ignore case and extra spaces; compare numbers by value, so 0.50 equals 0.5). `result` is `correct` or `incorrect`. This is your own comparison, not a script's, so be literal.
- **Short answer**: go through the stored checklist. For each point say hit or miss and, for a hit, quote the user's own words that cover it. `quality` = points hit divided by total points. `result`: `correct` if quality is at least 0.8, `partial` if at least 0.5, otherwise `incorrect`. Be strict about meaning, lenient about wording. Never award a point the user's text does not contain.

Record every answer:

Contents of `.tutor/tmp/event-2.json`:

```json
{"type": "attempt", "kp": "<recorded.kp>", "question_id": "<stamp>-q1", "result": "incorrect",
 "quality": 0.25, "hints_used": 0, "attempt_count": 1, "learner_answer": "...",
 "question": "the question text", "feedback": "...", "source": "deep_question", "assessment_type": "quiz",
 "origin": "quiz", "error_type": "deviation", "ai_confirmation": "one sentence on why", "self_attribution": ""}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-2.json
```

Leave `quality` out for multiple choice and fill-in-the-blank (the scheduler derives it from `result`, `hints_used` and `attempt_count`). `error_type`, `ai_confirmation` and `self_attribution` only belong on `incorrect` answers. `attempt_count` is how many tries this question took in this sitting. Always include `question` so the error log is readable later. If the command returns an error, fix the event and record again; never skip recording.

## 5. Diagnose mistakes

For every `incorrect` answer choose one cause and say it in plain words:
- `structural`: a prerequisite idea or the overall structure is missing;
- `deviation`: they hold a wrong or distorted understanding of the idea;
- `application`: they understand the idea but used it wrongly (steps, calculation, wrong situation);
- `metacognitive`: they guessed, left it blank, or did not realise they did not know.

(The four names come from DeepTutor; the one-line definitions here are this plugin's own.) Then give feedback in three parts: the correct answer with its source page, why the user's answer fell short, and one concrete next step (a page to reread or an easier question to try). Optionally ask "What do you think went wrong?" and store their reply as `self_attribution`. A `partial` answer needs the same feedback on the missing points.

## 6. Close the session

Run `ledger.py state` and report from its output: per knowledge point, `open_errors` and the error records. With an active path, `mastery`, `gate.mastery`, `unaided_attempts`, the module `gaps` and `next_action` come from `path.py status`; without one, take `mastery` and `unaided_attempts` from `ledger.py state`. Say that mastery is capped until there are enough attempts and that one correct answer is weak evidence. Do not say a topic is "mastered" yourself. Offer `/tutor:review` for later; the next review date is in the `schedule.next_review_date` field.

## Re-testing mistakes

A mistake graduates only after two correct answers to that same `question_id` (a wrong or partial answer in between resets the count). When re-testing, ask the original question again, or the same idea in a different form, and record the attempt with the **original `question_id`** from `ledger.py errors`.

## Rules

- Never edit files under `.tutor/learner/` directly; only use `ledger.py`.
- Inspect the knowledge base only through `kb.py`; never run `sqlite3` or open `index.sqlite`, which permissions refuse.
- The answer key lives in `.tutor/quiz/`. Read it only to grade an answer the user has already given, and quote from it only after they have answered that question.
