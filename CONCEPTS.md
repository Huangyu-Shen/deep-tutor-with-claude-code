# Concepts

> Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Knowledge base

A searchable index built from the learner's own source material and its pages. It supplies the citations that lessons, quizzes and learning paths rely on. Pages can be re-ingested, re-extracted or emptied, so a citation is checked against the index at read time rather than assumed. One knowledge base can back several learning paths.

### Learning path

A named, ordered plan of study over one knowledge base: it groups modules, orders them with prerequisites, and cites the pages each knowledge point rests on. A path is configuration, not progress — it defines what should be learned, not what has been learned. A knowledge base may hold several paths, and at most one is active for a learner at a time.

### Module

A stage of a learning path that groups knowledge points. A module unlocks only when every prerequisite module is mastered, and it becomes mastered when every knowledge point inside it passes its gate and its cited evidence is intact. Mastery is monotonic within a path — a later failure does not take it back — but points that no longer pass are reported as regressed rather than silently dropped.

### Knowledge point

The smallest unit of learning the project teaches, quizzes, tracks and reviews. Each knowledge point has a knowledge type, an objective, optional prerequisites, and citations to specific pages. The knowledge type decides which gate family judges it: memory and procedure points need repeated unaided recall, while concept and design points need a correct explanation.

### Evidence

The page-level citations a knowledge point rests on. Evidence is what lets a claim be checked against the learner's own material, so it is an input to mastery rather than decoration: if a cited page disappears or becomes empty, the knowledge point's evidence is invalid, its gate cannot pass, and any module built on it cannot be mastered or newly attempted until the citation is restored.

### Gate

The rule that decides whether a knowledge point is passed. There are two families: a numeric family for memory and procedure points, requiring both a mastery level and a run of recent unaided correct answers; and a qualitative family for concept and design points, requiring the latest unaided explanation to be correct. Hints disqualify an attempt from either family, and a failed explanation blocks a numeric gate on the same point.

### Mastery

A recency-weighted estimate of how well a learner knows a point, computed from their recorded answers and deliberately capped while few answers exist, so one or two correct answers cannot look like full mastery. Mastery is displayed for every point and counts every recorded answer, including hinted and diagnostic ones; on its own it is never sufficient to pass a memory or procedure gate.

### Attempt

A single recorded answer to one question about one knowledge point. An attempt carries where the question came from (teaching, quiz, review, diagnostic or exemption), whether it was a quiz answer or an explanation, whether hints were used, and its result. Attempts are the only evidence gates read: the same question counts once, and an unaided answer is never erased by a later hinted retry.

### Learner log

The append-only record of a learner's defined knowledge points and attempts. It is the only durable record of learning: displayed mastery, review scheduling and path gates are all derived from it, and its recorded order is part of the evidence.

### Replay

Deriving the current state by reading the learner log in recorded order instead of storing derived state. Replay is deterministic and read-only — the same log always yields the same status — so a stored cache that diverges from the log is a bug, not an optimisation to reconcile.

### Diagnostic

A whole-path starting assessment whose only purpose is to locate where the learner should begin. Diagnostic attempts count toward displayed mastery but never open a gate and never mark a module as started.

### Exemption

An optional assessment of an available module that can open its gates without teaching. It is requested by the learner, announced with its workload first, and judged on fresh answers and explanations. A failed exemption leaves the module available and does not start a cooldown.

### Question bank

The per-knowledge-base store of every question that `/tutor:quiz` or `/tutor:review` has asked since 0.3.0: ordinary quiz questions and mimic questions alike. Path diagnostic and exemption questions are recorded in the ledger only and are not added to the bank. Each entry keeps its knowledge point, type, difficulty, source page and answer status, so quiz and review can draw questions by those conditions without regenerating them. A reused question keeps its `question_id`.

### Reference question

An example or exercise taken from the learner's own materials, confirmed by the learner and cited to a page, that serves as the template for a mimic question. It is a source of format and difficulty, not an answer key.

### Mimic question

A question Claude writes in the style of a confirmed reference question. It keeps that reference's format and difficulty, points back to it, and is labelled as an AI variation whether or not its answer key is sourced.

### Answer status

Whether a question's answer key rests on the source (`sourced`: the supporting passage is cited and verified) or on Claude's reasoning alone (`unverified`). The status is decisive only for gates: a missing status (older logs) or `sourced` may open one, while `unverified` and any unknown or malformed value never does. Every attempt still counts for displayed mastery, the mistake log and review scheduling, so a bad status costs the gate, never the record.

### Teaching profile

The per-knowledge-base, per-learner record of how the learner has said they want to be taught: their explicit preferences and the observations of which explanation strategies helped or did not for a knowledge point. Every entry carries a verbatim quote from the learner. It changes how `/tutor:learn` explains and never affects mastery, gates or review scheduling.

### Teaching preference

An explicit request about how the learner wants to be taught, such as "use more examples", recorded with the learner's own words. A newer explicit request from the learner overrides it.

### Memory

The per-learner store under `.tutor/memory/<learner>/` that follows DeepTutor's three-layer memory: raw events (L1), per-source documents (L2) and four synthesis documents (L3). In 0.5.0 it holds the data layer only; nothing is summarised into the documents automatically. It personalises explanations and never affects mastery, gates, the question bank or the review schedule.

### L1 event

One append-only record of something that happened on a source (`chat`, `quiz`, `kb`): an id of the form `<surface>:<ULID>`, a UTC timestamp, a kind and a payload. A knowledge-base question is a `query` event with the learner's own words; a stated preference is a `preference_stated` event. Failing to save one never interrupts the command that produced it.

### Snapshot entity

One current thing on a source: a lesson summary (`chat`), an answered quiz or review attempt (`quiz`), or a knowledge base (`kb`). Each has an id, label, timestamp, content and a fingerprint. A snapshot refresh compares the current entities with the saved state and records what was added, removed or modified.

### Memory document (L2 / L3)

A Markdown file of bullet entries, each with a stable `m_` id and footnote citations pointing at events or entities. L2 documents are per source; L3 has four slots: `recent`, `profile`, `scope` and `preferences`. Entries are changed only through atomic add, edit and delete batches or whole-document overwrite.

### Memory preference

A way the learner asked to be treated, saved to the L3 `preferences` document through `/tutor:memory` with the learner's own words. It is separate from the teaching profile, which `/tutor:learn` writes per knowledge base; both are read when explaining.

### Teaching observation

A note, tied to one registered knowledge point, that an explanation strategy `helped` or `not_helped`, recorded with the learner's own words. A `not_helped` strategy is not repeated for that point; an observation says nothing about whether the point is understood.
