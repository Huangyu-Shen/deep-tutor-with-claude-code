"""Mastery scoring policy.

Adapted from DeepTutor (https://github.com/HKUDS/DeepTutor, Apache-2.0),
deeptutor/learning/mastery.py. Modified: docstrings trimmed, command-line
entry point added, `strict=True` dropped from zip() so it runs on Python 3.9 (the
two sequences always have equal length). The scoring logic and constants are unchanged.

``compute_mastery`` maps a knowledge point's attempt history to a 0..1 mastery
score: recency-weighted accuracy with a low-confidence cap, so a single lucky
answer cannot "master" a point.

CLI: echo '{"correctness": [true, false, 1.0]}' | python3 mastery.py
"""

from __future__ import annotations

import json
import sys

# Recency weights for the most recent attempts (oldest -> newest).
_RECENCY_WEIGHTS: tuple[float, ...] = (0.5, 0.7, 0.85, 0.95, 1.0)

# Mastery cannot exceed this until enough attempts accumulate.
_CONFIDENCE_CAP: dict[int, float] = {1: 0.5, 2: 0.8}


def compute_mastery(correctness: list[bool | float]) -> float:
    """Return a 0..1 mastery score from chronological attempt outcomes."""
    if not correctness:
        return 0.0
    recent = correctness[-len(_RECENCY_WEIGHTS) :]
    weights = _RECENCY_WEIGHTS[-len(recent) :]
    score = sum(
        w * max(0.0, min(1.0, float(c))) for c, w in zip(recent, weights)  # equal length by construction
    ) / sum(weights)
    return min(score, _CONFIDENCE_CAP.get(len(recent), 1.0))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = {"ok": True, "mastery": compute_mastery(payload["correctness"])}
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
