from __future__ import annotations
import time
from typing import Optional
from .base import ExchangeAdapter
from ..canonical import CanonicalOrderBookEvent
from ..sequence import OKXSequenceComparator
class OKXAdapter(ExchangeAdapter):
    channel_event_types={"books":("CanonicalOrderBookEvent",),"trades":("CanonicalTradeEvent",),"mark-price":("CanonicalMarkPriceEvent",),"index-tickers":("CanonicalMarkPriceEvent",),"open-interest":("CanonicalOIEvent",),"funding-rate":("CanonicalMarkPriceEvent",),"liquidation-orders":("CanonicalLiquidationEvent",)}
    sequence_comparator=OKXSequenceComparator()
    def connect(self): return None
    def subscribe_message(self,streams): return {"op":"subscribe","args":[{"channel":s,"instId":"BTC-USDT-SWAP"} for s in streams]}
    def route_message(self,raw): return raw.get("arg",{}).get("channel")
    def normalize(self,raw,*,local_receive_ts:Optional[int]=None):
        now=int(time.time()*1000) if local_receive_ts is None else local_receive_ts
        if self.route_message(raw)!="books": return []
        return [CanonicalOrderBookEvent("OKX","orderbook",int(d["ts"]),None,now,bids=tuple((float(x[0]),float(x[1])) for x in d.get("bids",[])),asks=tuple((float(x[0]),float(x[1])) for x in d.get("asks",[])),update_id=d.get("seqId"),previous_update_id=d.get("prevSeqId"),is_snapshot=d.get("prevSeqId")==-1) for d in raw.get("data",[])]
