---
name: quiz
description: Quiz the user on their own course materials, grade the answers, log mistakes with a diagnosed cause, and re-test old mistakes. Use when the user asks to be tested, wants practice questions, self-test, exam preparation, or to retry questions they got wrong.
argument-hint: "[kb name] [topic] [number of questions]"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Read, Edit(**/.tutor/quiz/*)
---

# Quiz

Adapted in part from DeepTutor's practice and error-diagnosis prompts and error taxonomy (Apache-2.0); see NOTICE.

Questions come from the user's knowledge base, every question carries its source page, and every result is written to the ledger. Write in **English** unless asked otherwise. Run commands from the user's project folder.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON or transcribed text on the command line or in a heredoc. Instead write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `event-1.json`, `event-2.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

## 1. Plan the session

1. Get a stamp: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" now`. Use its `stamp` for the quiz file name and for question ids.
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" errors`: mistakes and their status. Ignore `graduated` ones. Re-test the open ones (`active`, `retrying`, `review`) first, but not the same question twice in one sitting. Each error has the original `question_id` and `question` text.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" state`: existing knowledge points. **Reuse an existing name** when a question tests the same idea, so history stays together.
4. Search the knowledge base for the topic (`kb.py search --kb NAME "query"`), as in the learn skill. Default to 5 questions unless the user says otherwise. If the materials do not cover the topic, say so; do not invent questions from general knowledge.

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

Show one question at a time (or the whole set if the user prefers). **Show only the prompt and options. Never show answers, explanations or checklists before the user has answered.** The user may ask for a hint; count each hint in `hints_used`. Hinted answers are recorded but do not count toward the mastery estimate (only unaided attempts do, as in DeepTutor), and tell the user so.

## 4. Grade

- **Multiple choice and fill-in-the-blank**: you compare the answer to the stored key (ignore case and extra spaces; compare numbers by value, so 0.50 equals 0.5). `result` is `correct` or `incorrect`. This is your own comparison, not a script's, so be literal.
- **Short answer**: go through the stored checklist. For each point say hit or miss and, for a hit, quote the user's own words that cover it. `quality` = points hit divided by total points. `result`: `correct` if quality is at least 0.8, `partial` if at least 0.5, otherwise `incorrect`. Be strict about meaning, lenient about wording. Never award a point the user's text does not contain.

Record every answer:

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "attempt", "kp": "<recorded.kp>", "question_id": "<stamp>-q1", "result": "incorrect",
 "quality": 0.25, "hints_used": 0, "attempt_count": 1, "learner_answer": "...",
 "question": "the question text", "feedback": "...", "source": "deep_question", "assessment_type": "quiz",
 "error_type": "deviation", "ai_confirmation": "one sentence on why", "self_attribution": ""}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
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

Run `ledger.py state` and report from its output only: per knowledge point, `mastery`, `unaided_attempts` and `open_errors`. Say that mastery is capped until there are enough attempts and that one correct answer is weak evidence. Do not say a topic is "mastered" yourself. Offer `/tutor:review` for later; the next review date is in the `schedule.next_review_date` field.

## Re-testing mistakes

A mistake graduates only after two correct answers to that same `question_id` (a wrong or partial answer in between resets the count). When re-testing, ask the original question again, or the same idea in a different form, and record the attempt with the **original `question_id`** from `ledger.py errors`.

## Rules

- Never edit files under `.tutor/learner/` directly; only use `ledger.py`.
