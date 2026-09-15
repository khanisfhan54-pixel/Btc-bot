"""Deterministic local order-book reconstruction for venue diff streams."""
from __future__ import annotations
from dataclasses import replace
from typing import Iterable
from .canonical import CanonicalOrderBookEvent
from .quality_events import BookQuality, BookQualityStateMachine
from .sequence import BinanceSequenceComparator, BybitSequenceComparator, OKXSequenceComparator, binance_snapshot_bridge

class LocalBook:
    def __init__(self, venue: str):
        self.venue=venue; self.bids={}; self.asks={}; self.previous=None; self.buffer=[]; self.state=BookQualityStateMachine()
        self.comparator={"BINANCE":BinanceSequenceComparator(),"BYBIT":BybitSequenceComparator(),"OKX":OKXSequenceComparator()}[venue]
    def _apply_levels(self, levels, side):
        target=self.bids if side=="b" else self.asks
        for price, quantity in levels:
            if quantity == 0: target.pop(price, None)
            else: target[price]=quantity
    def _apply(self,event):
        self._apply_levels(event.bids,"b"); self._apply_levels(event.asks,"a"); self.previous=event
        return replace(event,bids=tuple(sorted(self.bids.items(),reverse=True)),asks=tuple(sorted(self.asks.items())),quality_state=self.state.state.value)
    def snapshot(self,event):
        self.bids.clear(); self.asks.clear(); self._apply_levels(event.bids,"b"); self._apply_levels(event.asks,"a"); self.previous=event
    def binance_snapshot(self,last_update_id,event):
        self.snapshot(event); self.state.resync()
        buffered=[x for x in self.buffer if x.update_id >= last_update_id]
        self.buffer=[]
        for diff in buffered:
            if binance_snapshot_bridge(diff,last_update_id): self._apply(diff); self.state.recovered(); return True
        return False
    def apply(self,event):
        if self.venue=="BINANCE" and self.previous is None:
            self.buffer.append(event); return None
        if event.is_snapshot: self.snapshot(event); self.state.recovered(); return self._apply(event)
        result=self.comparator.check(event,self.previous)
        if result.is_resync_signal: self.state.resync(); return None
        if result.is_gap: self.state.gap(); return None
        if result.reason=="duplicate_update": return None
        return self._apply(event)
