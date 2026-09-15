from __future__ import annotations
import time
from typing import Optional
from .base import ExchangeAdapter
from ..canonical import CanonicalLiquidationEvent, CanonicalMarkPriceEvent, CanonicalOIEvent, CanonicalOrderBookEvent, CanonicalTradeEvent, OISource
from ..sequence import BybitSequenceComparator
class BybitAdapter(ExchangeAdapter):
    channel_event_types={"orderbook.{depth}.BTCUSDT":("CanonicalOrderBookEvent",),"publicTrade.BTCUSDT":("CanonicalTradeEvent",),"tickers.BTCUSDT":("CanonicalMarkPriceEvent","CanonicalOIEvent"),"allLiquidation.BTCUSDT":("CanonicalLiquidationEvent",)}
    sequence_comparator=BybitSequenceComparator()
    def __init__(self): self._ticker_state = {}
    def connect(self): return None
    def subscribe_message(self,streams): return {"op":"subscribe","args":list(streams)}
    def route_message(self,raw):
        t=raw.get("topic",""); return "orderbook" if t.startswith("orderbook.") else "trades" if t.startswith("publicTrade.") else "ticker" if t.startswith("tickers.") else "liquidation" if t.startswith("allLiquidation.") else None
    def normalize(self,raw,*,local_receive_ts:Optional[int]=None):
        now=int(time.time()*1000) if local_receive_ts is None else local_receive_ts; d=raw.get("data",{}); route=self.route_message(raw); ts=raw.get("ts")
        if route=="orderbook": return [CanonicalOrderBookEvent("BYBIT","orderbook",ts,d.get("cts"),now,bids=tuple((float(p),float(q)) for p,q in d.get("b",[])),asks=tuple((float(p),float(q)) for p,q in d.get("a",[])),update_id=d.get("u"),sequence=d.get("seq"),is_snapshot=raw.get("type")=="snapshot")]
        if route=="trades": return [CanonicalTradeEvent("BYBIT","trades",ts,x.get("T"),now,trade_id=str(x["i"]) if x.get("i") is not None else None,price=float(x["p"]),quantity=float(x["v"]),side=x.get("S"),venue_sequence=x.get("seq"),block_trade=x.get("BT"),rpi=x.get("RPI")) for x in d]
        if route=="ticker":
            # Delta topics may omit values; emit only information actually carried.
            self._ticker_state.update(d); d = self._ticker_state; events=[]
            if any(k in d for k in ("markPrice","indexPrice","fundingRate","nextFundingTime")): events.append(CanonicalMarkPriceEvent("BYBIT","markprice",ts,None,now,mark_price=float(d["markPrice"]) if d.get("markPrice") is not None else None,index_price=float(d["indexPrice"]) if d.get("indexPrice") is not None else None,funding_rate=float(d["fundingRate"]) if d.get("fundingRate") is not None else None,next_funding_time=int(d["nextFundingTime"]) if d.get("nextFundingTime") is not None else None))
            if d.get("openInterest") is not None: events.append(CanonicalOIEvent("BYBIT","openinterest",ts,None,now,open_interest=float(d["openInterest"]),source=OISource.WS_PUSH))
            return events
        if route=="liquidation": return [CanonicalLiquidationEvent("BYBIT","liquidation",x.get("T",ts),None,now,side=x.get("S"),price=float(x["p"]),quantity=float(x["v"])) for x in d]
        return []
