#!/usr/bin/env bash
# End-to-end walkthrough of the 0.1.0 scripts, in the order the skills use them.
# Prints each command and its (shortened) JSON output. Uses a throwaway project.
#
#   bash tests/walkthrough.sh > docs/verification/0.1.0-walkthrough.log
#
# The scanned-page transcription comes from tests/fixtures/scanned-page1.transcription.txt,
# which Claude wrote after reading the rendered page image (the skill's step 2-3).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$(mktemp -d)"
trap 'rm -rf "$PROJ"' EXIT
cd "$PROJ"

KB="python3 $HERE/scripts/kb.py"
LG="python3 $HERE/scripts/ledger.py"
FIX="$HERE/tests/fixtures"
DAY=86400
T0=1700000000

show() { # run a command, print it and the first 700 chars of output
  echo; echo "\$ $*"; local out; out="$("$@" 2>&1 || true)"; echo "${out:0:700}"; [ ${#out} -gt 700 ] && echo "... (${#out} chars total)"; return 0
}
rec() { # record an event from stdin
  echo; echo "\$ ledger.py record  <<< $1"; echo "$1" | $LG record | cut -c1-400
}

echo "== 1. kb: ingest a PDF, a deck, notes and a scanned PDF"
show $KB ingest --kb bio "$FIX/lecture.pdf" "$FIX/slides.pptx" "$FIX/notes.md" "$FIX/scanned.pdf"

echo; echo "== 2. kb: scanned pages -> render, (Claude reads the image), register"
show $KB render --kb bio --source scanned.pdf --pages 1
echo; echo "\$ kb.py register --kb bio --source scanned.pdf --page 1 < scanned-page1.transcription.txt"
$KB register --kb bio --source scanned.pdf --page 1 < "$FIX/scanned-page1.transcription.txt" | cut -c1-300

echo; echo "== 3. learn: search, then verify the quotes that would be shown"
show $KB search --kb bio "calvin cycle" --query "carbon fixation rubisco" --limit 2 --no-text
echo; echo "\$ kb.py verify  (one real quote, one invented, one from the scan, one on the wrong page)"
$KB verify --kb bio <<'JSON' | cut -c1-1200
{"quotes": [
 {"quote": "Rubisco is the enzyme that catalyses carbon fixation.", "source": "lecture.pdf", "page": 2},
 {"quote": "Rubisco is the enzyme that makes chlorophyll green.", "source": "lecture.pdf", "page": 2},
 {"quote": "Mitochondria produce ATP through oxidative phosphorylation", "source": "scanned.pdf", "page": 1},
 {"quote": "The Calvin cycle fixes carbon dioxide into sugar.", "source": "lecture.pdf", "page": 1}
]}
JSON

echo; echo "== 4. quiz: define knowledge points, answer, get one wrong"
rec "{\"type\":\"kp_define\",\"kb\":\"bio\",\"name\":\"Calvin cycle\",\"knowledge_type\":\"concept\",\"sources\":[\"lecture.pdf page 2\"],\"ts\":$T0}"
rec "{\"type\":\"kp_define\",\"kb\":\"bio\",\"name\":\"Rubisco\",\"knowledge_type\":\"memory\",\"sources\":[\"lecture.pdf page 2\"],\"ts\":$T0}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::calvin cycle\",\"question_id\":\"q1\",\"result\":\"correct\",\"quality\":1.0,\"ts\":$T0}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::rubisco\",\"question_id\":\"q2\",\"result\":\"incorrect\",\"learner_answer\":\"it absorbs light\",\"error_type\":\"deviation\",\"ai_confirmation\":\"confuses Rubisco with chlorophyll\",\"ts\":$T0}"
show $LG errors

echo; echo "== 5. quiz: re-test the mistake on later days (graduates after 2 correct)"
rec "{\"type\":\"attempt\",\"kp\":\"bio::rubisco\",\"question_id\":\"q2\",\"result\":\"correct\",\"ts\":$((T0 + 1*DAY))}"
$LG errors | cut -c1-300
rec "{\"type\":\"attempt\",\"kp\":\"bio::rubisco\",\"question_id\":\"q2\",\"result\":\"correct\",\"ts\":$((T0 + 3*DAY))}"
$LG errors | cut -c1-300

echo; echo "== 6. review: what is due 10 and 40 days after the first session?"
show $LG --now $((T0 + 10*DAY)) due
show $LG --now $((T0 + 40*DAY)) due --limit 1

echo; echo "== 7. review: a lapse (wrong after a long gap) shortens the next interval"
rec "{\"type\":\"attempt\",\"kp\":\"bio::calvin cycle\",\"question_id\":\"rev-1\",\"result\":\"incorrect\",\"assessment_type\":\"review\",\"error_type\":\"application\",\"ts\":$((T0 + 40*DAY))}"
show $LG --now $((T0 + 40*DAY)) state

echo; echo "== 8. export"
show $LG export --format md
