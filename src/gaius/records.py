from __future__ import annotations

HANDOFF_END_MARKER = "<!-- gaius-handoff-end -->"
DECISION_END_MARKER = "<!-- gaius-decision-end -->"
SYNC_RECORD_MARKERS = frozenset({HANDOFF_END_MARKER, DECISION_END_MARKER})
