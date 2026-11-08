---
title: An evidence field must be fail-closed for gates and tolerant for records
date: 2026-11-09
category: logic-errors
module: scripts/path.py gate evidence and scripts/learner_log.py validation
problem_type: logic_error
component: tooling
symptoms:
  - "An attempt carrying an unrecognized answer_status string still counted toward a module gate, because the gate filter excluded only the literal \"unverified\""
  - "An attempt whose answer_status was not a string (for example 5) was rejected by validate_event on read, so read_events skipped it and the attempt vanished from mastery, the error log and review scheduling"
  - "Both behaviours passed the first implementation's tests, which only covered the two known status values"
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags:
  - answer-status
  - gate-evidence
  - fail-closed
  - fail-open
  - validation-boundary
  - ai-generated-answer-keys
  - learner-log
  - regression-tests
---

# An evidence field must be fail-closed for gates and tolerant for records

## Problem

Tutor 0.3.0 added an optional `answer_status` to attempt events (`sourced` / `unverified`) so an AI-proposed answer key can move a learner's mastery number without unlocking a learning-path module. The gate is the promise that matters: a module only unlocks on evidence a human can trust.

The first implementation said what it wanted in the negative: gate evidence was "everything except `unverified`". It also listed `answer_status` in `TEXT_FIELDS`, the tuple `validate_event` checks for string type on read. A clean-context correctness review reproduced two defects that together break the promise in both directions — one lets untrusted evidence through the gate, the other destroys trustworthy evidence everywhere else.

## Symptoms

- `scripts/path.py` filtered with `!= "unverified"`, so any unrecognised string (`"from-the-future"`, `""`, `"UNVERIFIED"`, a typo) counted as gate evidence. A learner could unlock a module with an answer nobody had checked, and a future `answer_status` value this version does not know would silently gain gate power.
- `validate_event` rejected a non-string `answer_status` (`5`, `true`, a list falsehood) as a damaged line, so `read_events` skipped the whole attempt (`scripts/learner_log.py:104-107`): it disappeared from mastery, from `errors`, and from `due`. One malformed optional field deleted a real learning event.
- The write-side comment promised the opposite of the read behaviour — "a bad value costs the gate, never the mastery record" — while `read_events` was doing exactly the opposite.
- Both defects were invisible to the happy-path tests, which only used `sourced`, `unverified` and no field at all.

## What Didn't Work

**Excluding the one bad value that was known.** The filter had to enumerate every untrusted value instead of every trusted one. That is a blacklist over an open set: `answer_status` is written by skills and by any third-party log, so the set of values that are *not* `unverified` grows without bound and every new one silently defaults to trusted. The review's reproduction — three correct attempts tagged `"from-the-future"` — passed the numeric gate with `unaided: 3`.

**Treating a malformed optional field as a damaged line.** Adding `answer_status` to `TEXT_FIELDS` looked like consistency with `question_id` and `feedback`, but `TEXT_FIELDS` is applied in `validate_event`, which runs on the read path (`scripts/learner_log.py:163-165`). The event log is an append-only ledger that must survive being read by a version that knows less than the writer. Rejecting a line for one unrecognised optional field trades a cosmetic validation win for data loss: the attempt is not "invalid", it is unreadable, and `read_events` cannot repair it later.

## Solution

Split the two responsibilities: strict when we write, tolerant when we read, fail-closed when we gate. `origin` had already established this split in 0.2.0 (`scripts/learner_log.py:172-174`); `answer_status` now follows it.

**1. Whitelist the gate, in `scripts/path.py:488-499`.**

Before:

```python
verified = [a for a in attempts if a.get("answer_status") != "unverified"]
```

After:

```python
verified = [a for a in attempts if a.get("answer_status", "sourced") == "sourced"]
return [a for a in _unique_attempts(verified)
        if _eligible(a) and _question_id(a) is not None]
```

Only a missing field (every 0.1.x/0.2.0 event, and the default the skills are told to write) or the literal `sourced` is gate evidence. `unverified`, unknown strings, empty strings and non-strings all fall out, because none of them equals `"sourced"`. The default argument is what keeps legacy logs replaying to their 0.2.0 results.

**2. Move validation to the write path.** `answer_status` leaves `TEXT_FIELDS`, so read-time validation tolerates any value (`scripts/learner_log.py:35-37`):

Before:

```python
TEXT_FIELDS = ("question_id", "learner_answer", "feedback", "question", "source",
               "ai_confirmation", "self_attribution", "answer_status")
```

After:

```python
ANSWER_STATUSES = ("sourced", "unverified")
TEXT_FIELDS = ("question_id", "learner_answer", "feedback", "question", "source",
               "ai_confirmation", "self_attribution")
```

and `prepare_event` rejects a bad value before it is ever appended (`scripts/learner_log.py:205-211`):

```python
if event.get("type") == "attempt" and event.get("answer_status") is not None \
        and event["answer_status"] not in ANSWER_STATUSES:
    raise LedgerError(f"answer_status must be one of {ANSWER_STATUSES}")
```

`prepare_event` strips `None` values first (`scripts/learner_log.py:190`), so "absent" and "explicitly null" are the same thing: no field.

Everything else still sees every attempt. `_display_mastery` is called with the unfiltered list (`scripts/path.py:482-485`, `:574`), and `errors` / `due` read the whole log, so an `unverified` mistake still becomes an error record and still enters the review schedule (test at `tests/test_ledger.py:430-437`).

**3. Keep the dedupe order honest.** The filter applies *before* `_unique_attempts`, not after. That order is the fix for a subtler case: a later `unverified` retry of the same `question_id` is removed first, so it cannot displace the earlier `sourced` record during dedupe. `_unique_attempts` then does its own job on the surviving set — one record per `question_id`, with an unaided answer outranking a hinted retry — and returns attempts without a `question_id` unchanged; `_gate_attempts` is what drops them from the gate (`scripts/path.py:499`). `tests/test_path.py:605` pins this: one `sourced` correct and then one `unverified` incorrect on the same id yields `unaided: 1`, `recent: ["correct"]`.

## Why This Works

The two defects were one design mistake seen from both sides: the code treated "unknown" as a licence on the read path and as a verdict on the gate path. Inverted, the same three rules become a coherent contract.

- **Strict on write.** `prepare_event` is the only place that knows an attempt is new and can refuse it cheaply. An invalid `answer_status` there is a caller bug with a stack of context, so it is a hard error (`LedgerError`), and the log never contains a value the writer did not intend.
- **Tolerant on read.** `read_events` is a replay of history written by other versions and other tools. A field this version does not recognise is not a reason to erase an event; the log's own rule is "Damaged lines are reported, never dropped silently" (`scripts/learner_log.py:86`), meaning damage becomes a warning rather than a silent disappearance — but skipping the line is still data loss, and one optional field is not damage to the record.
- **Fail-closed on gates.** The gate is the only consumer for which "I do not understand this value" must mean "do not trust it". Equality against `"sourced"` is the smallest possible trusted set: an unknown value, a future value, an empty string and a non-string all read as untrusted by construction, with no enumeration to maintain. Trust is granted explicitly, never by default.

The asymmetry also localises the blast radius of a bug. A malformed value now costs the gate — the learner sees the point stay unpassed and can re-answer — instead of costing the record, which no retry can recover because `read_events` has already skipped the line.

Legacy behaviour is preserved by the same expression: `a.get("answer_status", "sourced")` makes "field absent" mean `sourced`, so 0.1.x and 0.2.0 logs replay exactly as they did before `answer_status` existed. `SCHEMA_VERSION` stays 1 and no migration is needed.

## Prevention

- **Write the whitelist, not the blacklist.** Any filter that decides "is this evidence trustworthy?" must enumerate what is trusted and fail closed on everything else. If the code reads as `!= bad_value`, ask what happens to `value_from_next_year` — the answer must be "rejected", not "accepted".
- **Put validation where it can refuse, not where it can only delete.** Read-time validation in an append-only log is a filter over history, and its only available action is losing records. Validate in the constructor/prepare step, and on read only check things whose absence would make replay *wrong* (ids, types of required fields, cross-references).
- **Make the implied promise a test.** The comment "a bad value costs the gate, never the mastery record" is now three tests: `tests/test_ledger.py:409` (unknown string tolerated), `tests/test_ledger.py:418` (non-string kept, still counted in mastery), `tests/test_path.py:615` (unknown value never opens a gate). A behavioural claim in a comment that no test asserts is a claim that is already false somewhere.
- **Test both directions of every filter.** The original tests proved `unverified` was excluded; nothing proved an *unknown* value was. For a predicate, add the case the author did not think of: an unrecognised value, a wrong type, and an empty string.
- **Reuse the established split instead of inventing one.** `origin` had already solved the same problem in 0.2.0 (validated on write, tolerated on read, treated as non-evidence for gates). Matching the existing pattern would have avoided both defects; when adding a field to the event contract, look for the sibling field that already made the decision.
- **Keep dedupe order deliberate and documented.** Filtering before dedupe is what prevents a later untrusted retry from hiding an earlier trusted answer. If the order is ever reversed, the same `question_id` history produces different gate results, so the ordering deserves a comment and a regression test (`tests/test_path.py:605`).

## Related

- `CHANGELOG.md` 0.3.0 (attempt events gain `answer_status`; unknown or malformed values never open a gate and never drop the attempt).
- `docs/verification/0.3.0.md` (correctness finding, whitelist deviation from the plan).
- Plan context: `docs/plans/2026-11-01-1143-feat-question-bank-mimic-plan.md` (KTD4).
- Sibling learning: `docs/solutions/architecture-patterns/derive-path-gates-by-replaying-the-attempt-log.md`.
