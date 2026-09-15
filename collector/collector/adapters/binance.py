from __future__ import annotations
import time
from decimal import Decimal
from typing import Optional
from .base import ExchangeAdapter
from ..canonical import CanonicalLiquidationEvent, CanonicalMarkPriceEvent, CanonicalOIEvent, CanonicalOrderBookEvent, CanonicalTradeEvent, OISource
from ..sequence import BinanceSequenceComparator, binance_snapshot_bridge

class BinanceAdapter(ExchangeAdapter):
    channel_event_types = {"<symbol>@depth@100ms": ("CanonicalOrderBookEvent",), "<symbol>@depth10@100ms": ("CanonicalOrderBookEvent",), "<symbol>@aggTrade": ("CanonicalTradeEvent",), "<symbol>@markPrice@1s": ("CanonicalMarkPriceEvent",), "<symbol>@forceOrder": ("CanonicalLiquidationEvent",)}
    sequence_comparator = BinanceSequenceComparator()
    def connect(self): return None
    def subscribe_message(self, streams): return {"method":"SUBSCRIBE", "params":list(streams), "id":1}
    def route_message(self, raw):
        stream = raw.get("stream", "").lower()
        for token, route in (("@depth", "orderbook"), ("@aggtrade", "trades"), ("@markprice", "markprice"), ("@forceorder", "liquidation")):
            if token in stream: return route
        return None
    def normalize(self, raw, *, local_receive_ts: Optional[int] = None):
        d = raw.get("data", raw); now = int(time.time()*1000) if local_receive_ts is None else local_receive_ts; route=self.route_message(raw) or d.get("e", "")
        if route == "orderbook" or d.get("e") == "depthUpdate":
            source = "PARTIAL_DEPTH" if "@depth10" in raw.get("stream", "") else "DIFF_DEPTH_RECONSTRUCTED"
            return [CanonicalOrderBookEvent("BINANCE","orderbook",d.get("E"),d.get("T"),now, bids=tuple((Decimal(p),Decimal(q)) for p,q in d.get("b",[])), asks=tuple((Decimal(p),Decimal(q)) for p,q in d.get("a",[])), update_id=d.get("u"),first_update_id=d.get("U"),previous_update_id=d.get("pu"), is_snapshot=False, book_source=source)]
        if route == "trades" or d.get("e") == "aggTrade": return [CanonicalTradeEvent("BINANCE","trades",d.get("E"),d.get("T"),now,trade_id=str(d.get("a")) if d.get("a") is not None else None,price=float(d["p"]),quantity=float(d["q"]),side="SELL" if d.get("m") else "BUY",nq=float(d["nq"]) if d.get("nq") is not None else None)]
        if route == "markprice" or d.get("e") == "markPriceUpdate": return [CanonicalMarkPriceEvent("BINANCE","markprice",d.get("E"),None,now,mark_price=float(d["p"]),index_price=float(d["i"]) if d.get("i") is not None else None,funding_rate=float(d["r"]),next_funding_time=d.get("T"))]
        if route == "liquidation" or d.get("e") == "forceOrder":
            o=d.get("o",{}); return [CanonicalLiquidationEvent("BINANCE","liquidation",d.get("E"),o.get("T"),now,side=o.get("S"),price=float(o["p"]),quantity=float(o["q"]))]
        return []
    @staticmethod
    def bridge_accepts(event, last_update_id): return binance_snapshot_bridge(event,last_update_id)
    @staticmethod
    def aggregate_trades_reseed_url(symbol="BTCUSDT"): return f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={symbol}&limit=1"
