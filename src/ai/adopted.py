"""Adopted insights become coach-layer rules. Only status='adopted' rows have
behavioral effect — Bodhi (via Riko) approves each one explicitly."""
from __future__ import annotations

import json
import re

from ..db.models import Database

RECOVERY_MODIFIER_CAP = 1.5
RECOVERY_MODIFIER_FLOOR = 0.8
ADOPTED_KEY_PATTERN = re.compile(r"^discovery\.([a-z_0-9]+)_next_day_hrv$")


def recovery_modifiers(db: Database) -> dict[str, float]:
    """activity_type -> recovery-time multiplier, from adopted next-day-HRV
    insights. A -22% HRV hit maps to a 1.22x recovery multiplier, capped."""
    modifiers: dict[str, float] = {}
    for row in db.get_insights(status="adopted"):
        match = ADOPTED_KEY_PATTERN.match(row["key"])
        if match is None or not row["evidence_json"]:
            continue
        evidence = json.loads(row["evidence_json"])
        relative_effect = evidence.get("relative_effect")
        if relative_effect is None:
            continue
        multiplier = 1.0 - relative_effect  # -0.22 ⇒ 1.22; +0.09 ⇒ 0.91
        modifiers[match.group(1)] = round(
            min(max(multiplier, RECOVERY_MODIFIER_FLOOR), RECOVERY_MODIFIER_CAP), 2
        )
    return modifiers
