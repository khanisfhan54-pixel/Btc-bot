"""Deterministic local order-book reconstruction for venue diff streams."""
from __future__ import annotations
import math
from dataclasses import replace
from .canonical import CanonicalOrderBookEvent
from .quality_events import BookQuality, BookQualityStateMachine
from .sequence import BinanceSequenceComparator, BybitSequenceComparator, OKXSequenceComparator, binance_snapshot_bridge

class LocalBook:
    """Authoritative in-memory book; Binance snapshots are bridged only by diffs."""
    def __init__(self, venue: str):
        self.venue=venue; self.bids={}; self.asks={}; self.previous=None; self.buffer=[]; self.state=BookQualityStateMachine()
        self.last_reason=""; self.duplicate_count=0
        self.comparator={"BINANCE":BinanceSequenceComparator(),"BYBIT":BybitSequenceComparator(),"OKX":OKXSequenceComparator()}[venue]

    @staticmethod
    def _valid_levels(levels):
        try:
            for price, quantity in levels:
                if not (math.isfinite(price) and math.isfinite(quantity) and price > 0 and quantity >= 0): return False
        except (TypeError, ValueError): return False
        return True
    def _validated_maps(self, event, bids=None, asks=None):
        if not self._valid_levels(event.bids) or not self._valid_levels(event.asks): return None
        bids=dict(self.bids if bids is None else bids); asks=dict(self.asks if asks is None else asks)
        for levels, target in ((event.bids,bids),(event.asks,asks)):
            for price, quantity in levels:
                if quantity == 0: target.pop(price, None)
                else: target[price]=quantity
        if not bids or not asks or max(bids) >= min(asks): return None
        return bids, asks
    def _apply(self,event):
        maps=self._validated_maps(event)
        if maps is None:
            self.last_reason="invalid_book"; self.state.gap(); return None
        self.bids, self.asks=maps; self.previous=event
        return replace(event,bids=tuple(sorted(self.bids.items(),reverse=True)),asks=tuple(sorted(self.asks.items())),quality_state=self.state.state.value)
    def snapshot(self,event):
        maps=self._validated_maps(event, {}, {})
        if maps is None: self.last_reason="invalid_snapshot"; return False
        self.bids, self.asks=maps
        self.previous=event
        return True
    def binance_snapshot(self,last_update_id,event):
        """Atomically apply a snapshot and a complete buffered Binance chain."""
        if not self.snapshot(event): self.state.gap(); return False
        # A REST update id is not an incremental event and must never be used
        # as the comparator anchor.
        self.previous=None
        self.state.resync(); original=list(self.buffer)
        # Binance says to discard only updates strictly older than snapshot.
        candidates=[x for x in original if x.update_id is not None and x.update_id >= last_update_id]
        bridge_index=None
        for i, diff in enumerate(candidates):
            if diff.first_update_id is not None and binance_snapshot_bridge(diff,last_update_id): bridge_index=i; break
        if bridge_index is None:
            self.buffer=candidates; self.last_reason="snapshot_bridge_not_found"; return False
        bridge=candidates[bridge_index]
        applied=self._apply(bridge)
        if applied is None: self.buffer=candidates[bridge_index:]; return False
        for i, subsequent in enumerate(candidates[bridge_index+1:], bridge_index+1):
            result=self.comparator.check(subsequent,self.previous)
            if result.reason == "duplicate_update": self.duplicate_count += 1; continue
            if result.is_gap or result.is_resync_signal:
                self.state.gap(); self.last_reason=result.reason
                # Keep the failed and later diffs. They have not been proven applied.
                self.buffer=candidates[i:]; return False
            if self._apply(subsequent) is None:
                self.buffer=candidates[i:]; return False
        self.buffer=[]; self.state.recovered(); self.last_reason=""
        return True
    def apply(self,event):
        # During REST I/O live events must be retained, not compared to stale state.
        if self.venue=="BINANCE" and (self.previous is None or self.state.state == BookQuality.RECOVERING):
            self.buffer.append(event); return None
        if event.is_snapshot:
            if self.snapshot(event): self.state.recovered(); return self._apply(event)
            self.state.gap(); return None
        result=self.comparator.check(event,self.previous)
        if result.is_resync_signal: self.last_reason=result.reason; self.state.resync(); self.buffer.append(event); return None
        if result.is_gap: self.last_reason=result.reason; self.state.gap(); self.buffer.append(event); return None
        if result.reason=="duplicate_update": self.duplicate_count += 1; self.last_reason=result.reason; return None
        return self._apply(event)
