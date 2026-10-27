"""CVSS v3 base score calculation from a vector string.

Used only when an advisory carries a vector but no human-readable severity rating.
The implementation follows the CVSS v3.1 specification, including the "Roundup"
function and the scope-dependent impact formula, so a derived severity is a
computed fact rather than a guess.
"""

from __future__ import annotations

import math
import re
from typing import Any

_VECTOR_RE = re.compile(r"CVSS:3[.][01](?:/[A-Za-z]+:[A-Za-z0-9]+)+")

_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}
_PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
_PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.50}


def _roundup(value: float) -> float:
    """CVSS 3.1 Roundup: the smallest number with one decimal at or above the input."""
    scaled = int(round(value * 100_000))
    if scaled % 10_000 == 0:
        return scaled / 100_000.0
    return (math.floor(scaled / 10_000) + 1) / 10.0


def parse_vector(vector: str) -> dict[str, str] | None:
    """Split a CVSS v3 vector into its metric components."""
    match = _VECTOR_RE.search(vector or "")
    if not match:
        return None
    parts = match.group(0).split("/")
    metrics: dict[str, str] = {}
    for part in parts[1:]:
        if ":" in part:
            key, _, value = part.partition(":")
            metrics[key] = value
    if not all(key in metrics for key in ("AV", "AC", "PR", "UI", "S", "C", "I", "A")):
        return None
    return metrics


def base_score(vector: str) -> float | None:
    """Calculate the CVSS v3 base score for a vector string."""
    metrics = parse_vector(vector)
    if metrics is None:
        return None

    try:
        scope_changed = metrics["S"] == "C"
        confidentiality = _CIA[metrics["C"]]
        integrity = _CIA[metrics["I"]]
        availability = _CIA[metrics["A"]]
    except KeyError:
        return None

    impact_sub = 1 - ((1 - confidentiality) * (1 - integrity) * (1 - availability))
    if scope_changed:
        impact = 7.52 * (impact_sub - 0.029) - 3.25 * (impact_sub - 0.02) ** 15
    else:
        impact = 6.42 * impact_sub

    if impact <= 0:
        return 0.0

    privileges = _PR_CHANGED if scope_changed else _PR_UNCHANGED
    try:
        exploitability = (
            8.22
            * _AV[metrics["AV"]]
            * _AC[metrics["AC"]]
            * privileges[metrics["PR"]]
            * _UI[metrics["UI"]]
        )
    except KeyError:
        return None

    if scope_changed:
        raw = min(1.08 * (impact + exploitability), 10.0)
    else:
        raw = min(impact + exploitability, 10.0)

    return _roundup(raw)


def rating_for_score(score: float) -> str:
    """Map a CVSS base score onto its qualitative rating."""
    if score <= 0:
        return "none"
    if score < 4.0:
        return "low"
    if score < 7.0:
        return "medium"
    if score < 9.0:
        return "high"
    return "critical"


def score_from_any(source: Any) -> float | None:
    """Extract a numeric CVSS score from a variety of advisory shapes."""
    if isinstance(source, (int, float)):
        return float(source)
    if isinstance(source, str):
        return base_score(source)
    if isinstance(source, dict):
        for key in ("score", "baseScore", "base_score", "value"):
            if key in source:
                return score_from_any(source[key])
    return None
