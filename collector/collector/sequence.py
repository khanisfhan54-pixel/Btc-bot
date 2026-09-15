"""Exchange-specific order-book continuity comparators."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

@dataclass(frozen=True)
class SequenceResult: is_gap: bool = False; is_resync_signal: bool = False; reason: str = ""
class SequenceComparator:
    def check(self, current: Any, previous: Optional[Any]) -> SequenceResult: raise NotImplementedError
class BinanceSequenceComparator(SequenceComparator):
    def check(self, current, previous):
        if previous is None: return SequenceResult()
        if current.previous_update_id != previous.update_id: return SequenceResult(True, False, "pu_mismatch")
        return SequenceResult()
class BybitSequenceComparator(SequenceComparator):
    def check(self, current, previous):
        if previous is None: return SequenceResult()
        if current.update_id is not None and previous.update_id is not None and current.update_id < previous.update_id:
            return SequenceResult(False, True, "update_id_decrease_or_reset")
        if current.update_id == previous.update_id:
            return SequenceResult(False, False, "duplicate_update")
        return SequenceResult()
class OKXSequenceComparator(SequenceComparator):
    def check(self, current, previous):
        if current.previous_update_id == -1: return SequenceResult()
        if previous is None: return SequenceResult(True, False, "missing_snapshot")
        if current.previous_update_id != previous.update_id: return SequenceResult(True, False, "prev_seq_id_mismatch")
        return SequenceResult()

def binance_snapshot_bridge(event: Any, last_update_id: int) -> bool:
    """USD-M futures invariant: deliberately no Spot-style +1."""
    return event.first_update_id <= last_update_id <= event.update_id
