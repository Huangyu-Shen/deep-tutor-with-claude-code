---
name: path
description: Create, confirm and switch a learning path over a knowledge base, show module status and gaps, and run a starting diagnostic or a module exemption check. Use when the user wants a study plan, asks what to learn next, wants to change plans, or wants to test out of material they already know.
argument-hint: "[kb name] [action]"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/path.py*), Bash(python3 *scripts/kb.py*), Bash(python3 *scripts/ledger.py*), Read
---

# Learning paths

Adapted in part from DeepTutor's learning-path and diagnostic prompts (Apache-2.0); see NOTICE.

A path is configuration: modules, knowledge points and their order. Answers stay in the learner ledger. Module status, gates and the next action are computed by `path.py` from those two inputs; never calculate or announce them yourself. Write in the language of the user's latest message and keep it for questions, tables, feedback and summaries: an English request gets English output, a Chinese request gets Chinese output, even when the skill file or the materials are in another language. Do not switch languages on your own. Quote the materials verbatim in their own language and never translate a quote. Run commands from the user's project folder.

This skill owns paths, diagnostics and exemptions only. It does not teach day-to-day material and does not run ordinary quizzes; point the user to `/tutor:learn`, `/tutor:quiz` or `/tutor:review` for those.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON on the command line or in a heredoc. Write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `outline-1.json`, `event-1.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

Run **one script command per Bash call**: no `for` loops, no `;`, `&&` or `|` chains, no `cd` prefix. The pre-approved permissions match a single `python3 .../scripts/...` command, so a chained or piped command is refused and the step silently does not happen (for example, a path that was never saved). To summarise JSON, read the printed output; do not pipe it into another program. Use one payload file and one call for each event, and record each answer exactly once: if a record's outcome is unclear, run `status` instead of recording again. A re-recorded `question_id` counts once in gates.

If a command returns `{"ok": false, "error": ...}`, show the error to the user and stop. A damaged path file is not "no path": report it and ask the user to restore the file; never edit it yourself. A dangling active pointer blocks every command except `deactivate`: run `deactivate --kb NAME`, say which pointer you cleared, then `list` and offer to `activate` a real path.

## 1. Start here

Check which knowledge bases exist, one command per Bash call:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" list
```

If the list is empty, tell the user to run `/tutor:kb` first. If there are several, ask which one the path is for; every command below needs `--kb NAME`.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" list --kb NAME
```

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" status --kb NAME
```

Use `status --kb NAME --all` when the user wants the state of every path in the knowledge base; omit `--all` for the active path.

If the knowledge base has no paths, say so and offer to create one. If it has paths but `active_path_id` is null, show them and offer to activate one.

Render `status` in the user's language: per module `status`, `mastered_at_iso`, `locked_by` and `invalidated_by`; per knowledge point `mastery`, `gate.mastery`, `unaided_attempts`, `qualitative`, `evidence` problems and `gaps`. `mastery` is what `ledger.py state` shows (all attempts, diagnostic included); `gate.mastery` is the gate input and excludes diagnostic and hinted attempts. A module is `mastered` once every point passed, and a later failed attempt does not take that back (KTD3): such points appear in `regressed` with `missing` empty. Only editing the path or losing cited evidence recomputes a mastered module. Translate gap codes, do not change them:

- `mastery_below`: gate mastery is below the `needed` value;
- `unaided_below`: fewer unaided attempts than `needed`;
- `recent_failure`: the most recent unaided results are not all `correct`;
- `qualitative_missing`: no Feynman explanation recorded yet;
- `qualitative_not_correct`: the latest Feynman explanation was not `correct`;
- `qualitative_failure`: a Feynman judgement of `incorrect`/`partial` was recorded for a memory/procedure point and blocks its numeric gate;
- `evidence_invalid`: the cited page is missing or empty; the knowledge point cannot pass until it is fixed.

If `blocked_by_invalid_evidence` is true, say which citations are broken and that no teaching can fix it; the path must be edited or the knowledge base rebuilt. If `path_complete` is true, say the path is complete and offer review. Otherwise show `next_action` (module, knowledge point, `learn` or `quiz`, and `exemption_available`) as the next step.

## 2. Create a path

1. Ask for the learning goal (exam, course unit, project) and the time horizon. Ask which materials it should cover if that is not obvious.
2. Search the knowledge base for each part of the goal, as the learn skill does. Read the pages you will cite.
3. Draft modules in a sensible order, each with 2 to 6 knowledge points. Use `memory` for facts and terms, `procedure` for steps and calculations, `concept` for understanding, `design` for open-ended application. Every knowledge point needs a short specific name, one objective, and at least one real citation.
4. Show the draft **one module at a time**, with its order, knowledge points and the page each point rests on. Invite renaming, reordering, merging, splitting and deletion. Do not save until the user has confirmed every module.
5. Write the confirmed outline to `.tutor/tmp/outline-1.json`:

```json
{"name": "Midterm plan", "modules": [
  {"id": "m1", "title": "Light reactions", "prerequisites": [],
   "knowledge_points": [
     {"name": "Light reactions", "knowledge_type": "memory",
      "objective": "Recall where light energy is captured.",
      "evidence": [{"source_id": "lecture-pdf", "page": 1}]},
     {"name": "Photosystem II", "knowledge_type": "concept",
      "objective": "Explain what Photosystem II does in your own words.",
      "prerequisites": ["<kb>::light reactions"],
      "evidence": [{"source_id": "lecture-pdf", "page": 2}]}]},
  {"id": "m2", "title": "Carbon fixation", "prerequisites": ["m1"],
   "knowledge_points": [
     {"name": "Carbon fixation", "knowledge_type": "memory",
      "objective": "Recall what Rubisco fixes.",
      "evidence": [{"source_id": "lecture-pdf", "page": 3}]}]}
]}
```

Rules the validator enforces: module `id` is required and unique; `title` is required; every module has at least one knowledge point; a knowledge point's id is `<kb>::<normalized name>`, so names must be unique across the whole path; `knowledge_type` must be one of the four; `evidence` uses the `source_id` from search results, never the display name (`lecture-pdf` above is only a placeholder; replace it and `<kb>`); module prerequisites name module ids; knowledge-point prerequisites name knowledge points **inside the same module** and only affect teaching order, not unlocking. Existing `kp_define` events keep their type: if the user wants a different type, the knowledge point needs a new name.

6. Validate without writing:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" validate --kb NAME < .tutor/tmp/outline-1.json
```

If `valid` is false, read every error with the user, fix the outline, write a **new** payload file and validate again. Never save an outline with errors.

7. Save and activate:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" save --kb NAME --activate < .tutor/tmp/outline-1.json
```

`kps_defined` lists the knowledge points newly registered in the ledger; `affected_modules` lists the modules whose state may change, including downstream ones. Ask whether the user wants the starting diagnostic now; it is optional and can run later.

To edit an existing path, get it with `show`, change it, keep its `"id"` in the outline, and `save` again. `validate` and `save` both report `affected_modules`: an edit to a module or to its downstream reopens those modules. New knowledge points must pass their gates before a module counts as complete again.

## 3. Switch, rename, deactivate, delete

One command per Bash call:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" list --kb NAME
```

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" activate --kb NAME --path PATH_ID
```

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" deactivate --kb NAME
```

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" rename --kb NAME --path PATH_ID --name "New name"
```

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/path.py" delete --kb NAME --path PATH_ID
```

Switching changes only the active pointer; every path keeps its progress. Deactivating returns learn and quiz to their path-free behaviour, and it is also the repair for a dangling active pointer. Deleting a path deletes the definition, not the attempts: a later path with the same knowledge points inherits that evidence. Confirm before deleting, and warn that deleting the active path clears the pointer.

## 4. Starting diagnostic

Offer this after creating a path, and whenever the user asks where to start. It is positioning only: it never exempts a module and never opens a gate.

1. Generate 5 to 10 questions across the modules, easy to hard. Every question cites a real page; verify every quote you show with `kb.py verify`, as the learn skill does.
2. Read the current time once:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" now
```

Use the returned `stamp` in file names and question ids for the whole diagnostic.
3. Write the answer key and checklist to a **new** `.tutor/tmp/diagnostic-<stamp>.json` before asking anything. Show only prompts and options.
4. Ask one question at a time, in the user's language. For each answer, write a **new** event file `.tutor/tmp/diagnostic-<stamp>-q1.json` (then `-q2`, and so on) with `origin: diagnostic`:

```json
{"type": "attempt", "kp": "<kb>::<name>", "question_id": "<stamp>-diag-q1", "result": "correct",
 "quality": 1.0, "origin": "diagnostic", "assessment_type": "quiz", "learner_answer": "...",
 "question": "the question text", "source": "path_diagnostic"}
```

Record it, one call per answer:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py" record < .tutor/tmp/diagnostic-<stamp>-q1.json
```

`record` fills `ts` and returns the stored event; never add `ts` yourself and never record the same answer twice.

Grade as the quiz skill does. For short answers write the checklist first; `result` is `correct` at quality 0.8 or above, `partial` at 0.5 or above, otherwise `incorrect`. On `incorrect` add `error_type` and `ai_confirmation`; on every answer include `question`.

Then run `status` again and report only what it says: the first unfinished module, which knowledge points need work, and the next action. Diagnostic attempts count toward the displayed mastery but not toward gates, so a short diagnostic never marks anything complete.

If a knowledge point has no citable page, do not ask about it; say the material does not support a question on it (AE7), and it cannot be treated as known.

## 5. Module exemption

Only for an `available` module the user names. Tell the user the total workload before starting: for each `memory`/`procedure` knowledge point 3 to 5 questions; for each `concept`/`design` knowledge point one Feynman explanation. A module with a concept or design point cannot be exempted by answering questions alone.

Read `ledger.py now` once and use its `stamp`; write the key to a **new** `.tutor/tmp/exemption-<stamp>.json` before asking. Then:

- Questions and grading follow the quiz skill; write each attempt to a **new** `.tutor/tmp/exemption-<stamp>-q1.json` and record it with `ledger.py record <` that file. Use `origin: exemption` and a fresh `question_id`. A retry after failure uses entirely new questions and ids; there is no cooldown.
- For a `concept`/`design` point, ask the user to explain it in their own words, then judge whether it is in their own words, misses nothing important and contains no real misunderstanding. Record the judgement as a qualitative attempt with `origin: exemption`:

```json
{"type": "attempt", "kp": "<kb>::<name>", "question_id": "<stamp>-ex-q1", "result": "partial",
 "quality": 0.6, "assessment_type": "qualitative", "origin": "exemption",
 "learner_answer": "their explanation", "question": "Explain ... in your own words",
 "feedback": "the gap in one sentence"}
```

Never record a pass the user did not earn, and never record an explanation they did not give.

Afterwards run `status` and report the outcome from it: if every knowledge point in the module passes its gate, the module is mastered and the next ones unlock; otherwise the module stays `available` with `exemption_available` still true and `gaps` shows exactly what is missing. The failed attempt does not lock the module or start a cooldown. Do not call a module exempted yourself.

## 6. Rules

- Only `path.py` decides status, gates, unlocking and `next_action`; only `ledger.py` writes events. Never edit files under `.tutor/` directly, and never write a module status anywhere.
- Inspect the knowledge base only through `kb.py` (`list`, `search`, `get`, `verify`). Never run `sqlite3` or open `index.sqlite` yourself; that command is refused, and the schemas are not your business.
- Never show answer keys, checklists or explanations before the user has answered. The key file is visible if the user expands that tool call; say so if they ask for no spoilers.
- Never open `.tutor/quiz/`; those keys belong to quizzes the user may not have answered. Never open an answer-key file another run wrote, including `.tutor/tmp/diagnostic-*` and `.tutor/tmp/exemption-*` that this run did not create.
- Quote the materials, do not invent them. A knowledge point without a real page cannot enter a path and cannot be exempted.
- Review stays outside module locks: a locked module may still be reviewed with `/tutor:review`.
