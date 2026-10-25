---
title: Learning-path gate state must be replayed from the append-only attempt log, never stored
date: 2026-10-25
category: architecture-patterns
module: scripts/path.py gate replay
problem_type: architecture_pattern
component: tooling
severity: high
applies_when:
  - "A learning path needs per-module unlock state on top of an append-only attempt log"
  - "A gate can be satisfied by duplicated, hinted, diagnostic-only, or unidentified attempts"
  - "A saved path cites evidence that can become stale or wrong after the knowledge base changes"
tags:
  - learning-paths
  - gate-replay
  - append-only-log
  - derived-state
  - evidence-quality
  - mastery
  - replay-determinism
  - review
---

# Learning-path gate state must be replayed from the append-only attempt log, never stored

## Context

Version 0.2.0 of the `tutor` plugin added `/tutor:path`: named learning paths over one knowledge base, module-level unlocking, dual-track mastery gates, a whole-path starting diagnostic, and per-module exemption. Before it, the plugin had exactly one durable record of learner behaviour, the append-only event log at `.tutor/learner/<learner>/events.jsonl` (`scripts/learner_log.py:10`), whose attempts already drive review scheduling and displayed mastery. The new feature needed a different answer from the same evidence: "may the learner attempt module M or point K right now, and when did M become mastered?"

The tempting implementation is a progress cache: write `status: "mastered"` onto the module definition when its last gate passes. That cache is also the implementation that quietly breaks. Module status depends on three inputs that all move independently: the attempt log grows, the path definition changes when the user reorders or edits modules, and the cited page text can be re-ingested or emptied. A stored flag would have to be invalidated on all three, and each missed invalidation makes the plugin assert progress the learner has not earned — the one thing the plugin promises. The same cache would additionally need special cases for `diagnostic` and `exemption` attempts, which are evidence but not the same kind of evidence.

The design chosen instead: a path is configuration and never stores state (`scripts/path.py:3-6`). `status` and `resolve` re-derive module states from `read_events` on every call. With the replay as the only source of truth, the feature's correctness reduces almost entirely to the correctness of the replay filter — which is where the real work, and most of the review findings, went.

## Guidance

- **Derive, don't persist.** `_status_payload` (`scripts/path.py:632`) reads the learner log at line 635 on every call; `cmd_status` (`:922`) and `cmd_resolve` (`:943`) both go through it. The store holds only definitions: schema, kb, `active_path_id`, paths. `mastered` is monotonic within a path. `_mastered_at` (`:590`) walks the log forward and records the first `event["ts"]` at which a module's prerequisites are already delivered and every gate passes. A later failure then appears as `regressed` (`:682-683`) while `missing` stays empty (`:680`) and `mastered_at_iso` (`:686`) preserves the original time. Only editing the path or losing cited evidence changes that verdict.

- **Evidence identity: one question, counted once.** `_unique_attempts` (`:462`) collapses attempts by `question_id`, and the preference rule at `:475` is the whole point:

  ```python
  if current is None or not attempt.get("hints_used") or attempts[current].get("hints_used"):
      best[qid] = index
  ```

  A later attempt wins if it is unaided, or if the one it replaces was itself hinted. So a retry can update a question's result but cannot erase the unaided answer that came before it. Attempts with no `question_id` are passed through unchanged and then dropped from gates by `_gate_attempts` (`:488-495`): two attempts without ids cannot be told apart from a duplicate, so they count for displayed mastery only. The live runs made this non-optional — the course log held 10 diagnostic events for 5 question ids (`docs/verification/0.2.0.md:60`).

- **Two gate families, different thresholds.** `NUMERIC_TYPES` (`memory`, `procedure`), `GATE_THRESHOLD = 0.9` and `GATE_UNAIDED = 3` sit at `scripts/path.py:47-49`. The numeric branch of `_gate` (`:501-526`) passes only when all of the following hold:

  ```python
  passed = bool(mastery is not None and mastery >= GATE_THRESHOLD
                and len(scored) >= GATE_UNAIDED and all(r == "correct" for r in recent)
                and not failed_qualitative)
  ```

  where `recent` is the last three unaided results. The qualitative branch (`concept`, `design`, `:527-536`) passes only if the latest unaided qualitative attempt is `correct`; a hinted Feynman retelling cannot pass it, because `unaided` is filtered at `:501`.

- **A qualitative failure blocks a numeric gate.** `incorrect`/`partial` qualitative attempts on a `memory`/`procedure` point are collected at `:504-505` and surfaced as the `qualitative_failure` gap (`:523-525`), even when the three recent quiz answers are all correct. Otherwise an `incorrect` explanation could be hidden by recording it under the other assessment type.

- **Origin semantics.** `_eligible` (`:452-454`) admits `GATE_ORIGINS = (None, "learn", "quiz", "review", "exemption")` (`:51`); `diagnostic` is deliberately absent, so it never opens a gate, though mastery still displays it. `exemption` can open a gate. A failed exemption must leave the module `available`: the `in_progress` state at `:675-676` requires an eligible attempt whose origin is not `exemption`, so an exemption-only history never marks the module as started, and `next_action.exemption_available` stays true with no cooldown.

- **Lock order comes before mastery.** In `_status_payload` (`:670-679`) `locked_by` is computed first and `locked` wins over `mastered`, `in_progress` and `available`. `cmd_resolve` mirrors that order (`:964` before `:972`), so a module whose prerequisite has been re-locked refuses with `locked`, not `mastered`.

- **Invalid evidence invalidates a module and its descendants.** `_evidence_status` (`:539`) reports `unknown_source`, `unknown_page` or `empty_page`; `_kp_status` (`:563-566`) turns those into an `evidence_invalid` gap, and `pass` requires both the gate and intact evidence (`:574`). `effective` (`:666`) is `mastered_at` minus any module with invalid entries — its own or any ancestor's, propagated at `:661-665`. `resolve` then refuses at `:968-971` (module scope) and `:978-981` (point scope) with the concrete `invalidated_by` list.

- **`resolve` is an ordered refusal list, not a boolean.** At `:947-987`: `no_active_path` returns allowed, then `out_of_path`, `unknown_module`, `locked`, `evidence_invalid` (module), `mastered`, `evidence_invalid` (point), `already_passed`; anything else is allowed with `reason` equal to the module status. The ordering is the contract: a locked module that also lost evidence reports `locked`, because that is the first obstacle the learner can act on.

- **Store integrity on load and on save.** `load_store` (`:106`) checks schema, kb, path ids and runs `_check_structure` (`:150`) per definition; a damaged file is an error, never "no path". A dangling `active_path_id` is also an error, except for `cmd_deactivate` (`:887`, `tolerate_dangling=True` at `:889`), which is the documented repair. `cmd_save` runs the same `_check_structure` before writing (`:821`), stages the file and commits atomically (`:847-856`). Every mutating command and both replay commands call `require_kb`, and `list`/`show`/`validate`/`save` open the index read-only through `connect_index` (`:83`), which refuses a missing or unreadable knowledge base — a mistyped kb cannot create an empty store.

- **The skill contract is part of the mechanism.** `skills/path/SKILL.md:18-22` requires one script command per call, a new payload file per event, a `<` redirect, no heredoc and no `;`/`&&`/`|`; `:139` takes the stamp from `ledger.py now`; `:188-189` forbids writing any module status by hand or opening `index.sqlite`. The model never computes status; it renders what `path.py` returns.

## Why This Matters

The plugin's promise is that progress is trustworthy. A persisted cache diverges from the log; repeated questions inflate evidence; a hinted pass looks like mastery; a stale citation asserts knowledge from a page that no longer exists. Each failure is invisible to the learner, who only sees a module declared mastered. The project rule is that code supporting the trust claim defaults to strict, and every loosening needs a regression test; `_unique_attempts`, `_gate_attempts` and the qualitative cross-block are all examples of tightening rather than relaxing.

The specific replay bugs were found by an independent review pass, not by the implementing context. Eight reviewers read the 0.2.0 diff (`docs/verification/0.2.0.md:69`), and the findings were replay-correctness findings: a failed exemption left the module `in_progress` with `exemption_available: false`; a hinted retry of the same `question_id` replaced the unaided answer; a hinted Feynman retelling passed a `concept` gate; an `incorrect` qualitative record on a `memory` point could be hidden by its label; attempts without a `question_id` could satisfy the three-unaided rule; and `resolve` allowed an already-passed point while ignoring broken evidence (`:71-76`). Each fix landed with a named regression test, and the store-integrity work (`:77-79`) came from the same pass. The duplicate-evidence defect came from live skill sessions instead: unit tests recorded each attempt once, so four fixtures never saw it.

## When to Apply

- Adding any progress, gate or unlock feature on top of an append-only log: derive state in one replay function and route every caller through it.
- Any skill or script that reports "mastered", "complete", or "you may now": confirm the number comes from the replay payload, not from the model's reading of earlier output.
- Adding a new attempt `origin` or `assessment_type`: decide explicitly whether it is gate-eligible (`GATE_ORIGINS`), whether it marks a module started, and whether it can pass either gate family.
- Path evidence that cites mutable source text: treat page text as an input to mastery, and invalidate downstream on change.
- Migrating or hand-editing `.tutor/path/*/paths.json`: keep one structural checker for both stored files and incoming outlines.

## Examples

**1. Replaying instead of caching.** `path.py status --kb comsm0126` run twice over a 61-event log produced byte-identical JSON (`docs/verification/0.2.0.md:18`); no file stores a module state, and `test_status_and_resolve_are_read_only` pins that `status`/`resolve` write nothing. A cache would have to be correct at the moment the log, the definition, or the page text changed — three invalidation paths instead of zero.

**2. Dedup and hint preference.** Recording `q1` twice leaves `unaided_attempts == 1` and `gate.unaided == 1` (`tests/test_path.py:599-607`). With unaided `q1`, unaided `q2` and a hinted `incorrect` retry of `q1`, the gate still reports `unaided: 2`, `recent: ["correct", "correct"]`, `mastery: 0.8` (`:711-720`) — the retry does not count and does not erase.

**3. Hinted Feynman retelling.** A `correct` qualitative attempt with `hints_used: 1` on a `concept` point yields `qualitative: null`, gap `qualitative_missing`, `pass: false` (`tests/test_path.py:722-729`). An unaided `correct` explanation later moves the module to `mastered` (`:633-636`).

**4. Failed exemption.** Two `incorrect` attempts with `origin: exemption` leave the module `available`, the point not passing, and `next_action.exemption_available` true (`tests/test_path.py:760-767`). Nothing cooldowns, and the retry path is the one the skill promised.

**5. Evidence changed after saving.** Emptying the cited page returns `progress: {"modules": 3, "mastered": 0}`, `invalidated_by: [{"module": "m1", "kp": "bio::alpha"}]` on all descendants, and `blocked_by_invalid_evidence: true` (`tests/test_path.py:813-828`). `resolve` then refuses in the same shape:

```json
{"ok": true, "allowed": false, "reason": "evidence_invalid",
 "module": "m1", "invalidated_by": [{"module": "m1", "kp": "bio::alpha"}]}
```

Restoring the page text returns `path_complete: true` with no new events (`:831-835`) — the evidence was never the attempt log's problem.

## Related

- [Quote verifier: column boundaries vs reading order](../logic-errors/quote-verifier-column-boundaries-layout-vs-reading-order-2026-10-24.md) — the same trust theme, enforced by a different mechanism.
- [PDF scan detection: per-page vs document-level](../logic-errors/pdf-scan-detection-per-page-vs-document-level-2026-10-24.md) — synthetic fixtures passing while real data fails, as with the duplicated live attempts.
- `docs/verification/0.2.0.md` — test counts, live skill runs and the review findings cited above.
- `docs/plans/2026-10-25-1135-feat-learning-path-plan.md` — the plan whose KTD3 (monotonic mastery), KTD7 (JSON usage errors) and KTD8 (`affected_modules`) this code implements.
