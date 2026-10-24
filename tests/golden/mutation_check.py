"""Check that the golden vectors would notice a change to the ported scheduler.

Development-time only:  python3 -I tests/golden/mutation_check.py

Each mutant is a one-edit change to scripts/scheduler.py (applied to a temporary
copy). A mutant "survives" if the golden tests still pass, meaning the vectors
do not cover that behaviour. Exit code 1 if any mutant survives.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]

MUTANTS = [
    ("spacing bonus", "spacing_ratio * 0.15", "spacing_ratio * 0.16"),
    ("same-session window", "_SAME_SESSION_DAYS = 0.25", "_SAME_SESSION_DAYS = 0.30"),
    ("same-session half factor", "DEFAULT_DESIRED_RETENTION) * 0.5,", "DEFAULT_DESIRED_RETENTION) * 0.6,"),
    ("drop scheduled_after_failure condition", "if state.scheduled_after_failure and (", "if (True) and ("),
    ("qualitative branch", '''evidence.result == "partial" or evidence.assessment_type == "qualitative"''',
     '''evidence.result == "partial"'''),
    ("partial floor", "state.retrievability = max(quality, 0.2)\n                return state",
     "state.retrievability = quality\n                return state"),
    ("fail threshold", "_FAIL_QUALITY = 0.5", "_FAIL_QUALITY = 0.51"),
    ("same-session success comparison", "if quality >= _FAIL_QUALITY:\n                state.review_count += 1",
     "if quality > _FAIL_QUALITY:\n                state.review_count += 1"),
    ("strong-recall bonus threshold", "if quality >= 0.8 and state.consecutive_correct >= 2:",
     "if quality > 0.8 and state.consecutive_correct >= 2:"),
    ("strong-recall bonus factor", "growth *= 1.15", "growth *= 1.16"),
    ("lapse retention factor", "0.55 - 0.25 * previous_retrievability", "0.55 - 0.26 * previous_retrievability"),
    ("difficulty update", "0.12 * outcome_error", "0.13 * outcome_error"),
    ("overdue cap", "min(overdue_days / 7.0, 0.25)", "min(overdue_days / 7.0, 0.26)"),
    ("error boost", "risk += 0.2", "risk += 0.21"),
    ("hint penalty", "evidence.hints_used * 0.08", "evidence.hints_used * 0.09"),
    ("attempt penalty", "max(0, evidence.attempt_count - 1) * 0.05", "max(0, evidence.attempt_count - 1) * 0.06"),
    ("partial quality", "quality = 0.4", "quality = 0.41"),
    ("clamp out-of-order time", "max(event_moment, state.last_review_at)", "event_moment"),
    ("failure shortens interval", "max(interval_days * 0.5, _MIN_STABILITY_DAYS * 0.2)",
     "max(interval_days * 0.6, _MIN_STABILITY_DAYS * 0.2)"),
]


def main() -> int:
    original = (ROOT / "scripts" / "scheduler.py").read_text(encoding="utf-8")
    survivors = []
    for name, old, new in MUTANTS:
        if original.count(old) != 1:
            print(f"BAD MUTANT (pattern found {original.count(old)}x): {name}")
            return 2
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            shutil.copytree(ROOT / "scripts", tmp_root / "scripts")
            shutil.copytree(ROOT / "tests", tmp_root / "tests", ignore=shutil.ignore_patterns("__pycache__"))
            (tmp_root / "scripts" / "scheduler.py").write_text(original.replace(old, new), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-I", "-m", "unittest", "discover", "-s", "tests", "-p", "test_golden.py"],
                cwd=tmp_root, capture_output=True, text=True,
            )
        killed = proc.returncode != 0
        print(("killed   " if killed else "SURVIVED ") + name)
        if not killed:
            survivors.append(name)
    print(f"\n{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} mutants killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
