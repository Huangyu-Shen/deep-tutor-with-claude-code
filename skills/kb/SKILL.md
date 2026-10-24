---
name: kb
description: Build or extend the local study knowledge base from the user's course materials (PDF, pptx, docx, xlsx, md, txt), including scanned PDFs. Use when the user adds lecture notes, slides, papers or a textbook, asks what is in the knowledge base, or says some pages could not be read.
argument-hint: "[kb name] [files or folders...]"
allowed-tools: Edit(**/.tutor/tmp/*), Bash(python3 *scripts/kb.py*), Read
---

# Knowledge base

You build the index that every other tutor skill cites from. The user should never have to run OCR or convert files: you handle every file type, and you say exactly what was and was not read.

Run all commands from the user's project folder. Data goes to `.tutor/kb/<name>/` there. The scripts need Python 3.9 or newer; if `python3` fails, tell the user.

## Passing data to the scripts

Claude Code blocks shell here-documents that contain braces and quotes, so never pass JSON or transcribed text on the command line or in a heredoc. Instead write the payload to a **new** file under `.tutor/tmp/` with the Write tool (a new file name every time, for example `event-1.json`, `event-2.json`; never overwrite an existing file) and feed it to the script with `<` as shown below. Files in `.tutor/tmp/` are scratch and may be deleted any time.

## Choose the knowledge base name

Arguments look like `[kb name] [paths...]`: if there is more than one word and the first is not an existing file or folder, the first word is the knowledge base name and the rest are paths. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" list`. If the arguments name a knowledge base, use it. If exactly one exists, use it. If none exist, propose a short name from the course (letters, digits, `_ . -` only) and confirm with the user. If the command fails because `${CLAUDE_PLUGIN_ROOT}` is empty, say so and stop.

## Ingest

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" ingest --kb NAME PATH [PATH...]
```

Then report in plain English, from the JSON only:
- per file: added / updated / unchanged / duplicate / failed, and how many pages, slides or sections;
- every failed file with its reason; if a tool is missing (`poppler`, `markitdown`) say what to install and offer to retry after;
- a `duplicate` is a file whose content is already indexed from another path; say which, and offer `remove` of the old one if the user moved the file;
- files skipped because the type is unsupported;
- `dropped_transcriptions`: a changed file lost its AI transcriptions; offer to redo them;
- pages, slides or sections with no text (`empty_units`, `units_without_text`): say plainly that **these are not indexed**. In a PDF they are usually photos, diagrams, blank video placeholders or title art; the user can ask you to describe a particular one (render it, look at it, `register` a one-line description). For slides that are only pictures in a pptx, suggest exporting the deck to PDF and ingesting that, or typing the content in;
- `scan_check` (PDFs): `low_text_ratio` is the share of pages with almost no text. Below 0.8 the file is treated as a normal document whose pictures are not text. If the ratio is between about 0.3 and 0.8 and the `empty_units` could plausibly be text (the file may be partly scanned), say so and ask the user; if they say those pages are text, re-run `ingest --scanned` for that file.

Never imply a file was read if the report says it was not. If `all_ok` is false, say so first. After the first ingest in a project that is a git repository, mention that `.tutor/` holds course text and the learner's answers, and ask whether to add it to `.gitignore`; do not edit `.gitignore` without a yes.

## Scanned pages

A PDF is treated as a scan only when at least 80% of its pages have almost no text (no text layer, or just a large image with a footer); slide decks full of photos stay normal documents because transcribing a photo adds nothing. In a scan, those pages are the ones to transcribe. If `pages_needing_transcription` is above zero, tell the user the file names and page counts, explain that you will read those pages as images and type them into the knowledge base once (after that, search is instant and costs nothing more), and **ask before starting**. A large book can be many pages; offer to do it in batches or only for the pages they need now.

After they agree, for each batch of up to 5 pages:

1. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" render --kb NAME --source SOURCE --pages 1-5` gives image paths.
2. Read each image with the Read tool.
3. Transcribe it faithfully into Markdown: keep the wording, headings, lists and tables; write formulas as LaTeX; write `[illegible]` where you cannot read something. Describe a figure in one bracketed line such as `[figure: bar chart of ATP yield per pathway]` and transcribe its labels. Do not summarise, correct, translate or add anything else.
4. Store it (new `page-N.txt` file each time, as described above):

Contents of `.tutor/tmp/page-1.txt`:

```text
...transcription...
```

Then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" register --kb NAME --source SOURCE --page N < .tutor/tmp/page-1.txt
```

`register` accepts pages that still need transcription, pages with no text (to describe a figure on request), and pages you transcribed earlier (to correct them). It never overwrites a text layer. Re-running `ingest` on an unchanged file keeps your transcriptions; a changed file loses them (`dropped_transcriptions`). Tell the user these pages are marked as AI transcription, which may contain reading errors, and that citations from them carry a lower-confidence flag, because checking a quote against your own transcription cannot catch your own reading mistakes.

## Inspect and remove

- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" status --kb NAME` lists sources, unit counts and pages still waiting for transcription.
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" remove --kb NAME --source SOURCE_ID` drops a source and its cached images (ask first).

## Rules

- Do not read the original files yourself to answer questions about their content; the other skills search the knowledge base so citations can be verified.
- Never edit files under `.tutor/` directly.
