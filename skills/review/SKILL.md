---
name: review
description: Run a spaced-repetition review of what the user has studied, ordered by forgetting risk, and update the schedule from their answers. Use when the user asks what to review, what is due, wants a revision session, or asks how well they are retaining material.
argument-hint: "[kb name] [how many items]"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Read
---

# Review

Adapted in part from DeepTutor's review prompt and scheduler (Apache-2.0); see NOTICE.

Reviews are chosen by a scheduler, not by your impression. The scheduler is DeepTutor's exponential-forgetting baseline: a reasonable estimate, not a calibrated memory model, so say "estimated" when you talk about retention. Write in the language of the user's latest message and keep it for explanations, questions, feedback and summaries: an English request gets English output, a Chinese request gets Chinese output, even when the skill file or the materials are in another language. Do not switch languages on your own. Quote the materials verbatim in their own language and never translate a quote. Run commands from the user's project folder.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON or transcribed text on the command line or in a heredoc. Instead write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `event-1.json`, `event-2.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

Run **one script command per Bash call**: no `for` loops, no `;`, `&&` or `|` chains, no `cd` prefix. The pre-approved permissions match a single `python3 .../scripts/...` command, so a chained or piped command is refused and the step silently does not happen (for example, knowledge points that were never recorded). To summarise JSON, read the printed output; do not pipe it into another program. Use one payload file and one call for each event, and record each answer exactly once: if a record's outcome is unclear, run `ledger.py state` instead of recording again. A re-recorded `question_id` counts once in path gates.

## 1. Get the queue

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" due --limit 5
```

Add `--kb NAME` when the user named a knowledge base. Default 5 items unless they ask for more.

- If `tracked_count` is 0, nothing has been quizzed yet: suggest `/tutor:quiz`.
- If `due_count` is 0, say nothing is due. Run `ledger.py state` and give the earliest `schedule.next_review_date` (already a readable UTC date), then stop. Do not invent review work.
- Otherwise show the list: name, type, `due_date` and the `reason` text (it explains why the item is due, in the scheduler's words). Items come highest forgetting risk first. An open mistake adds to an item's risk and gives it the top tie-break priority, but it does not guarantee first place.

## 2. Review each item

Use the item's `kp` value from the queue exactly as given when you record anything.

1. Find the material: `kb.py search --kb NAME "<name>"` using the item's name, or read the pages in its `sources` with `kb.py get`. If the materials cannot be found any more, tell the user and skip the item without recording anything.
2. Check `ledger.py errors` for an open mistake (`active`, `retrying`, `review`) on this knowledge point.
   - **If there is one**: re-ask that original question (its `question` text, same or reworded) and record the attempt with its **original `question_id`**. This is how a mistake graduates (two correct answers).
   - **Otherwise**: ask one fresh question that tests recall of the idea, not recognition of old wording. Prefer short answer or fill-in-the-blank for `memory` and `procedure` items, and explain-in-your-own-words for `concept` and `design` items. Get a stamp from `ledger.py now` and use the question id `<stamp>-rev1`, `<stamp>-rev2`, ...
3. Write your answer key or checklist first and show only the question. Grade as in the quiz skill: key comparison, or the checklist with the user's own words as evidence; `quality` from the checklist; `correct` at 0.8 or above, `partial` at 0.5 or above, otherwise `incorrect`.
4. Record it with `"assessment_type": "review"`:

Contents of `.tutor/tmp/event-1.json`:

```json
{"type": "attempt", "kp": "<queue kp>", "question_id": "<stamp>-rev1", "result": "correct",
 "quality": 0.9, "hints_used": 0, "attempt_count": 1, "learner_answer": "...", "question": "...",
 "feedback": "...", "source": "deep_question", "assessment_type": "review"}
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/event-1.json
```

For an incorrect answer add `error_type` and `ai_confirmation` and explain the mistake with its source page, as in the quiz skill. If the command returns an error, fix the event and record again.

5. Show the `kp_summary` that `record` returns, in words: the new mastery estimate and `next_review_date`.

## 3. Close

Summarise: items reviewed, how many were correct, partial, incorrect, and the next due dates from a fresh `ledger.py due`. If a mistake is still open, say it needs two correct answers to the same question to graduate. Do not call anything "mastered"; report the ledger's `mastery` numbers and say they are capped until enough unaided attempts exist.

## Rules

- **Never open files in `.tutor/quiz/`** (they hold answer keys for unanswered quizzes), and never reveal or hint at an answer to a question the user has not answered. If a quiz is still unanswered, say so and tell the user to answer it first; when you show how to reply, use a placeholder such as `Q1: <your answer>`, never a real answer.
- Do not change the schedule by hand; only recording attempts changes it. Repeating an item again very soon after a review (minutes to a few hours, depending on how well it is known) counts as practice but does not push the next review further out; tell the user so if they ask why.
- Never edit files under `.tutor/` directly.
