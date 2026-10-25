#!/usr/bin/env bash
# End-to-end walkthrough of the scripts, in the order the skills use them.
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
PAT="python3 $HERE/scripts/path.py"
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

echo; echo "== 9. path: save a two-module path, then advance it"
cat > outline.json <<'JSON'
{"name": "Midterm plan", "modules": [
  {"id": "m1", "title": "Light reactions", "prerequisites": [], "knowledge_points": [
    {"name": "Light reactions", "knowledge_type": "memory",
     "objective": "Recall where light energy is captured.",
     "evidence": [{"source_id": "lecture-pdf", "page": 1}]}]},
  {"id": "m2", "title": "Carbon fixation", "prerequisites": ["m1"], "knowledge_points": [
    {"name": "Carbon fixation", "knowledge_type": "memory",
     "objective": "Recall what Rubisco fixes.",
     "evidence": [{"source_id": "lecture-pdf", "page": 2}]}]}
]}
JSON
echo; echo "\$ path.py validate --kb bio < outline.json"
$PAT validate --kb bio < outline.json | cut -c1-400
echo; echo "\$ path.py save --kb bio --activate < outline.json"
$PAT save --kb bio --activate < outline.json | cut -c1-700
echo; echo "\$ path.py status --kb bio  (m2 is locked, m1 needs evidence)"
show $PAT status --kb bio
echo; echo "\$ path.py resolve --kb bio --module m2"
show $PAT resolve --kb bio --module m2
rec "{\"type\":\"attempt\",\"kp\":\"bio::light reactions\",\"question_id\":\"lr-1\",\"result\":\"correct\",\"origin\":\"learn\",\"ts\":$((T0 + 50*DAY))}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::light reactions\",\"question_id\":\"lr-2\",\"result\":\"correct\",\"origin\":\"learn\",\"ts\":$((T0 + 50*DAY + 60))}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::light reactions\",\"question_id\":\"lr-3\",\"result\":\"correct\",\"origin\":\"learn\",\"ts\":$((T0 + 50*DAY + 120))}"
echo; echo "\$ path.py status --kb bio  (m1 mastered, m2 unlocked)"
show $PAT status --kb bio
$PAT status --kb bio | python3 -c "import json,sys; d=json.load(sys.stdin); m={x['id']:x for x in d['modules']}; assert m['m1']['status']=='mastered' and m['m2']['status']=='available', d; print('assert ok: m1 mastered, m2 unlocked')"
echo; echo "\$ path.py resolve --kb bio --module m2"
show $PAT resolve --kb bio --module m2
echo; echo "== 10. path: a short diagnostic is not an exemption"
rec "{\"type\":\"attempt\",\"kp\":\"bio::carbon fixation\",\"question_id\":\"cf-diag-1\",\"result\":\"correct\",\"quality\":1.0,\"origin\":\"diagnostic\",\"assessment_type\":\"quiz\",\"ts\":$((T0 + 51*DAY))}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::carbon fixation\",\"question_id\":\"cf-diag-2\",\"result\":\"correct\",\"quality\":1.0,\"origin\":\"diagnostic\",\"assessment_type\":\"quiz\",\"ts\":$((T0 + 51*DAY + 60))}"
echo; echo "\$ path.py status --kb bio  (mastery is capped at 0.8; m2 stays available)"
show $PAT status --kb bio
$PAT status --kb bio | python3 -c "import json,sys; d=json.load(sys.stdin); m={x['id']:x for x in d['modules']}; k=m['m2']['knowledge_points'][0]; assert m['m2']['status']=='available' and k['mastery']==0.8 and not k['gate']['passed'], d; print('assert ok: diagnostic left m2 available at mastery 0.8')"
echo; echo "== 11. path: an exemption check completes m2"
rec "{\"type\":\"attempt\",\"kp\":\"bio::carbon fixation\",\"question_id\":\"cf-ex-1\",\"result\":\"correct\",\"quality\":1.0,\"origin\":\"exemption\",\"assessment_type\":\"quiz\",\"ts\":$((T0 + 52*DAY))}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::carbon fixation\",\"question_id\":\"cf-ex-2\",\"result\":\"correct\",\"quality\":1.0,\"origin\":\"exemption\",\"assessment_type\":\"quiz\",\"ts\":$((T0 + 52*DAY + 60))}"
rec "{\"type\":\"attempt\",\"kp\":\"bio::carbon fixation\",\"question_id\":\"cf-ex-3\",\"result\":\"correct\",\"quality\":1.0,\"origin\":\"exemption\",\"assessment_type\":\"quiz\",\"ts\":$((T0 + 52*DAY + 120))}"
echo; echo "\$ path.py status --kb bio  (m2 mastered, path complete)"
show $PAT status --kb bio
$PAT status --kb bio | python3 -c "import json,sys; d=json.load(sys.stdin); m={x['id']:x for x in d['modules']}; assert m['m2']['status']=='mastered' and d['path_complete'], d; print('assert ok: exemption completed m2 and the path')"
