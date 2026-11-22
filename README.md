<div align="center">

# tutor — a Claude Code study plugin

> Study from your own course materials with Claude.

[![Release](https://img.shields.io/github/v/release/Huangyu-Shen/deep-tutor-with-claude-code?style=for-the-badge)](https://github.com/Huangyu-Shen/deep-tutor-with-claude-code/releases)
[![License](https://img.shields.io/github/license/Huangyu-Shen/deep-tutor-with-claude-code?style=for-the-badge)](LICENSE)
[![GitHub Repo](https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/Huangyu-Shen/deep-tutor-with-claude-code)

<br />

[✨ Features](#-features) · [🚀 Quick Start](#-quick-start) · [📦 Installation](#-installation) · [📖 Usage](#-usage) · [📄 License](#-license)

</div>

---

## ✨ Features

- `/tutor:kb` — build a local, page-indexed knowledge base from your PDFs, slides and notes (scanned PDFs too)
- `/tutor:path` — turn a goal into a checked learning path: modules, knowledge points and citations, confirmed module by module; gates decide when the next module unlocks, with a starting diagnostic and optional exemption checks
- `/tutor:learn` — explanations grounded in your materials, with page citations; quoted text is checked against the stored page text; it remembers how you want to be taught and which explanations did not work for you, and changes its approach
- `/tutor:quiz` — questions archived in a per-base bank, with an error log, error-cause diagnosis and re-testing; find worked examples and get labelled AI variations whose keys are either checked against the page or marked unverified
- `/tutor:review` — spaced review ordered by estimated forgetting risk
- `/tutor:memory` — see, edit and delete what the plugin remembers about you: saved preferences, recent activity, the memory documents and snapshots (the data layer of DeepTutor's memory; automatic consolidation comes later)

Explanations, questions and feedback follow the language you write in; quotes from your materials stay in their own language. All reasoning is done by Claude; no other model API is called. Your data stays in `.tutor/` inside each project.

## 🚀 Quick Start

Install once (needs Claude Code 2.1.275 or later):

```bash
claude plugin marketplace add Huangyu-Shen/deep-tutor-with-claude-code
claude plugin install tutor@deep-tutor-with-claude-code
```

Then work in **your own study folder**, not inside this repository: copy your course files there, start `claude`, and run `/tutor:kb` with your files. Then either keep the free flow (`/tutor:learn` with a question, `/tutor:quiz`, `/tutor:review`) or run `/tutor:path` to build a learning path from your goal; `learn` and `quiz` will follow the active path. Your data goes to `.tutor/` in that folder. (Claude Code refuses writes into a plugin's own directory, so a study folder inside the plugin repository does not work.)

To try an unreleased change without installing, start Claude Code from your study folder with `claude --plugin-dir /path/to/deep-tutor-with-claude-code`.

## 📦 Installation

Requirements:

- Claude Code
- Python 3.9 or newer (`python3`), no extra Python packages
- [poppler](https://poppler.freedesktop.org/) for PDFs (`brew install poppler`): `pdftotext`, `pdftoppm`, `pdfimages`
- [markitdown](https://github.com/microsoft/markitdown) for pptx, docx and xlsx (`uv tool install 'markitdown[all]'` or `pip install 'markitdown[all]'`)

Missing tools are reported by the scripts with the install hint; nothing fails silently.

Each skill pre-approves the commands and file writes it needs, but Claude Code applies that only while the skill's own turn runs. In a longer conversation you may be asked again; choose "always allow", or add these to your settings once:

```json
{"permissions": {"allow": [
  "Bash(python3 *scripts/kb.py*)", "Bash(python3 *scripts/ledger.py*)",
  "Bash(python3 *scripts/path.py*)", "Bash(python3 *scripts/bank.py*)",
  "Bash(python3 *scripts/teaching.py*)", "Bash(python3 *scripts/memory.py*)",
  "Edit(**/.tutor/tmp/*)", "Edit(**/.tutor/quiz/*)"
]}}
```

If a write or command is declined, the skill says so and does not record the result.

## 📖 Usage

What is checked, and what is not:

- **Quotes are verified, paraphrases are not.** `learn` checks every quoted string against the page it cites (word boundaries, in order; `...` is allowed but the skipped words are shown). A paraphrase with a page number is not checked, and the check only runs because the skill instructs Claude to run it.
- **Pages Claude transcribed from scans** are marked `ai-transcription`. A quote verified against such a page only proves it matches Claude's own transcription, so it is flagged low-confidence.
- **Grading is done by Claude** against an answer key (multiple choice, fill-in-the-blank) or a fixed checklist (short answer). The ledger records the results; it does not grade.
- **Path gates are replayed by a script.** A module becomes mastered once every knowledge point has passed its gate: `memory`/`procedure` at mastery ≥ 0.9 with the last three unaided answers correct, `concept`/`design` with one Feynman explanation judged correct. A mastered module is not taken back by a later failed attempt (points that slip show under `regressed`); only editing the path or losing a cited page recomputes it. Claude never declares a module mastered, and a starting diagnostic never exempts anything; exemption is a separate check.
- **The review scheduler** is DeepTutor's exponential-forgetting baseline, not a calibrated FSRS fit. Mastery numbers are capped until enough unaided attempts exist.
- **Teaching notes are quotes, not guesses.** `learn` records a teaching preference or a "this explanation did not help" note only when your current message contains words it can quote, and tells you what it saved. You can ask what is recorded or ask it to forget an entry. The script checks that each quote appears in the message it is given, but that message is passed in by Claude, so this enforces the quoting format and cannot prove you wrote it. The notes change how a point is explained; they never change mastery, gates or the review schedule, and `quiz` and `review` do not read them.
- **Memory files are plain data, and the summaries are Claude’s.** Memory lives in `.tutor/memory/<learner>/` (events, documents, snapshots, lesson summaries). `/tutor:learn` saves one short summary per lesson (your question in your own words, plus the points it planned to explain with their pages) before it explains, so a lesson that ends early still leaves a record; the summary is written by Claude and does not claim to cover every point. Preferences are saved to memory only when you ask `/tutor:memory` to, and only with words you said. The quiz snapshot contains the correct answers of questions you have answered (never of unanswered ones). Nothing is summarised into the memory documents automatically in this version. Deleting an entry in `/tutor:memory` removes it from the document only: the original words can remain in the event log and the lesson summaries, which you can delete by removing files under `.tutor/memory/`. If an event cannot be saved the skill tells you and carries on. Memory never changes mastery, gates, the question bank or the review schedule.
- **AI variations are labelled.** A normal quiz question is `sourced`: its key rests on the cited page. A variation of a confirmed reference question is `sourced` only when its key is checked against the page, otherwise `unverified`. Unverified answers count for mastery, the error log and review, but never open a learning-path gate; the question, feedback and bank listing all show the status.
- Verified on macOS with the test suite and a 16-PDF course walkthrough, and on Python 3.9 and 3.13; see the notes in [docs/verification/](docs/verification/). Other platforms and very large libraries remain untested.

## 🛠 Tech Stack

| Layer | Choice |
|-------|--------|
| Interface | Claude Code skills (`/tutor:kb`, `/tutor:path`, `/tutor:learn`, `/tutor:quiz`, `/tutor:review`, `/tutor:memory`) |
| Scripts | Python 3.9+ stdlib only, JSON in / JSON out |
| Storage | `.tutor/` per project (ledger, SQLite knowledge base) |
| Reasoning | Claude only; no other model API |

## 📄 License

See [LICENSE](LICENSE).

This project adapts teaching methods and algorithms from [DeepTutor](https://github.com/HKUDS/DeepTutor) (HKUDS, Apache License 2.0). It is not affiliated with or endorsed by the DeepTutor authors. See [NOTICE](NOTICE) for what was adapted and what changed.

---

## Development

```bash
python3 -I -W error::ResourceWarning -m unittest discover -s tests
python3 -I tests/golden/mutation_check.py     # do the golden vectors notice scheduler changes?
bash tests/walkthrough.sh                     # end-to-end run of the scripts
```

Golden vectors in `tests/golden/` were generated by running DeepTutor's original code (`tests/golden/generate.py`, needs a DeepTutor checkout); the ported scripts must reproduce them exactly. The memory vectors (`tests/golden/generate_memory.py`, needs Python 3.10 or newer) work the same way and also record the SHA-256 of each upstream file they used.

Project workflow and roadmap: [docs/DESIGN.md](docs/DESIGN.md), [CLAUDE.md](CLAUDE.md).