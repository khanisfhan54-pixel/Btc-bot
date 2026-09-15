"""Causal alignment based on each venue's local receive clock."""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable

def causally_align(events: Iterable[object], observation_ts: int) -> dict[str, object]:
    """Return each stream's latest event available no later than observation_ts."""
    latest = {}
    for event in sorted(events, key=lambda item: item.local_receive_ts):
        if event.local_receive_ts > observation_ts:
            break
        latest[f"{event.exchange}:{event.stream}"] = event
    return latest
