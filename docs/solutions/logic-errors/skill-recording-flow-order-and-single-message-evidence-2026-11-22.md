---
title: A model-driven recording step must run before the answer, and recovery flows must fit inside one evidence message
date: 2026-11-22
category: logic-errors
module: skills/learn/SKILL.md recording flow and scripts/teaching.py quote check
problem_type: logic_error
component: tooling
symptoms:
  - "A one-turn /tutor:learn session followed the learner's stated preference in its explanation but never saved it, so the next session started with an empty profile"
  - "When a profile was full, the skill told the agent to ask which entry to remove, but the new entry could never be saved afterwards"
  - "Both defects passed 35 unit tests and the script walkthrough; only a real session and a multi-reviewer code review exposed them"
root_cause: missing_workflow_step
resolution_type: workflow_improvement
severity: medium
tags: [skills, teaching-profile, live-session-testing, evidence-binding, single-message, record-before-answer]
---

# A model-driven recording step must run before the answer, and recovery flows must fit inside one evidence message

## Problem

0.4.0 added a teaching profile. `/tutor:learn` reads it before explaining and records the learner's stated preferences and "that explanation did not help" remarks. The script, `scripts/teaching.py`, accepts a record only when its `evidence` is a verbatim part of the `learner_message` passed in the same batch (`scripts/teaching.py:264`). The code and the 30 unit tests were correct. The skill text that drives the script had two flow defects that no script test could see.

## Symptoms

- The first live run, `claude -p "/tutor:learn bio 讲讲渗透作用。能不能多用例子讲"`, used many examples (the preference was honoured) and called `teaching.py show` twice, but never called `update`. The profile file did not exist afterwards.
- The code review's agent-native reviewer traced the cap-reached branch: at 6 preferences or 12 observations the script refuses the whole batch, the skill said to ask which entry to remove, and the learner's reply is a new message that cannot contain the quote for the original new entry.

## What Didn't Work

- **Recording as the last step of the skill (section 6, after the explanation and the understanding check).** In a one-turn session the model treated the written explanation as the end of the job. Nothing in the script or the earlier sections reminded it, so the side effect silently did not happen. Adding the step to the skill file was not enough; where it sits in the order mattered.
- **"Ask which entry to remove" as the recovery for a refused batch.** It reads as complete and sounds humane, but it sends the learner into a second turn where the evidence the script needs no longer exists.

## Solution

- **Record before explaining.** `skills/learn/SKILL.md` section 0b step 4 (line 51) says: when the learner's current message already states a preference or says an explanation did not help, record it now, before you explain, because the lesson may end without another turn. A rerun of the same live session saved the preference, told the learner, and then explained with examples.
- **Make the recovery fit in one message.** Section 6 step 4 (line 183) now says that at the cap nothing from that batch was saved; ask the learner which entry to drop and to restate the new request in the same reply, then send one batch with a `remove` and an `upsert` whose quotes both come from that one reply, and say plainly that the new entry is not saved until then. The cap check runs after all items are applied (`scripts/teaching.py:322`), so the combined batch passes. A unit test pins that combined batch, and a live session at the cap confirmed the model refuses, tells the learner, and asks for the restatement.
- A related fix from the same review: section 7 (line 188) says to run `list` before forgetting an entry, so the agent uses a real key instead of guessing.

## Why This Works

- A script cannot make a model call it. A step that only the model's own discipline triggers has to sit where the turn cannot end first, which is before the long answer, not after it.
- A validator that binds evidence to one message (here "the learner's current message") makes every refusal a dead end unless the skill arranges for everything needed to retry to arrive in that same message. The refusal is correct; the recovery path must be designed around the binding.

## Prevention

- **Run each skill branch live, including refusals.** A unit test of the script proves the refusal; only a real `claude -p` session shows what the model does next. Seed the state that triggers the branch (here: fill the profile with a hand-built batch) and read both the reply and the file afterwards. This extends the existing rule in `CLAUDE.md` that a skill is not done until it has run in a real session.
- **For every refusal the script can give, write down what the model can do in the next turn with the evidence it will then have.** If the answer is "nothing", the recovery must move into the same turn.
- **Put state-changing steps before the main output** when the learner's current message already holds what they need.
- A one-turn `claude -p` run is a harsh harness for this: it ends as soon as the model decides it is done. If a step survives that harness, it survives a normal multi-turn session.

## Related Issues

- `docs/solutions/logic-errors/answer-status-fail-closed-gates-tolerant-records.md` (same problem class: a trust field that tests passed but real flows broke).
- `docs/verification/0.4.0.md` (live sessions, the code-review fix and the cap-reached run).
- `docs/plans/2026-11-22-1454-feat-teaching-feedback-plan.md` (KTD3 evidence rule, KTD8 skill discipline).
