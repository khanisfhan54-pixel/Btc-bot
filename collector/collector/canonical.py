"""Venue-neutral market-data events; absent venue fields remain ``None``."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from decimal import Decimal

class OISource(str, Enum): REST_POLL = "REST_POLL"; WS_PUSH = "WS_PUSH"

@dataclass(frozen=True)
class CanonicalEvent:
    exchange: str; stream: str; exchange_event_ts: Optional[int]; exchange_transaction_ts: Optional[int]
    local_receive_ts: int; local_process_ts: Optional[int] = None; market_type: str = "linear_perpetual"; quality_state: str = "VALID"

@dataclass(frozen=True)
class CanonicalOrderBookEvent(CanonicalEvent):
    bids: tuple[tuple[Decimal, Decimal], ...] = (); asks: tuple[tuple[Decimal, Decimal], ...] = ()
    update_id: Optional[int] = None; first_update_id: Optional[int] = None; previous_update_id: Optional[int] = None
    sequence: Optional[int] = None; is_snapshot: bool = False; book_source: str = "DIFF_DEPTH_RECONSTRUCTED"

@dataclass(frozen=True)
class CanonicalTradeEvent(CanonicalEvent):
    trade_id: Optional[str] = None; price: float = 0.0; quantity: float = 0.0; side: Optional[str] = None; nq: Optional[float] = None
    venue_sequence: Optional[int] = None; block_trade: Optional[bool] = None; rpi: Optional[bool] = None

@dataclass(frozen=True)
class CanonicalOIEvent(CanonicalEvent): open_interest: Optional[float] = None; source: OISource = OISource.WS_PUSH
@dataclass(frozen=True)
class CanonicalMarkPriceEvent(CanonicalEvent):
    mark_price: Optional[float] = None; index_price: Optional[float] = None; funding_rate: Optional[float] = None; next_funding_time: Optional[int] = None
@dataclass(frozen=True)
class CanonicalLiquidationEvent(CanonicalEvent): side: Optional[str] = None; price: Optional[float] = None; quantity: Optional[float] = None
