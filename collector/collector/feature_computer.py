import time
from typing import Dict, Any, Optional

def compute_orderbook_features(msg: Dict[str, Any]) -> Dict[str, Any]:
    bids = msg.get("b", [])
    asks = msg.get("a", [])

    if len(bids) != 10 or len(asks) != 10:
        return {}

    try:
        bids_price = [float(b[0]) for b in bids]
        bids_qty = [float(b[1]) for b in bids]
        asks_price = [float(a[0]) for a in asks]
        asks_qty = [float(a[1]) for a in asks]
    except (ValueError, TypeError):
        return {}

    best_bid = bids_price[0]
    best_ask = asks_price[0]
    mid_price = (best_bid + best_ask) / 2.0

    bid_qty_0 = bids_qty[0]
    ask_qty_0 = asks_qty[0]

    if bid_qty_0 + ask_qty_0 == 0:
        return {}

    micro_price = (best_bid * ask_qty_0 + best_ask * bid_qty_0) / (bid_qty_0 + ask_qty_0)
    spread = best_ask - best_bid
    if mid_price == 0:
        return {}
    spread_bps = (spread / mid_price) * 10000.0

    total_bid_qty = sum(bids_qty)
    total_ask_qty = sum(asks_qty)

    if total_bid_qty + total_ask_qty == 0:
        return {}
    obi = (total_bid_qty - total_ask_qty) / (total_bid_qty + total_ask_qty)

    tbq_1 = bids_qty[0]
    taq_1 = asks_qty[0]
    obi_level_1 = (tbq_1 - taq_1) / (tbq_1 + taq_1) if tbq_1 + taq_1 > 0 else 0.0

    tbq_3 = sum(bids_qty[:3])
    taq_3 = sum(asks_qty[:3])
    obi_level_3 = (tbq_3 - taq_3) / (tbq_3 + taq_3) if tbq_3 + taq_3 > 0 else 0.0

    tbq_5 = sum(bids_qty[:5])
    taq_5 = sum(asks_qty[:5])
    obi_level_5 = (tbq_5 - taq_5) / (tbq_5 + taq_5) if tbq_5 + taq_5 > 0 else 0.0

    timestamp = int(time.time() * 1000)
    exchange_timestamp = msg.get("E", timestamp)

    return {
        "timestamp": timestamp,
        "exchange_timestamp": exchange_timestamp,
        "local_timestamp": timestamp,
        "bids_price": bids_price,
        "bids_qty": bids_qty,
        "asks_price": asks_price,
        "asks_qty": asks_qty,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid_price,
        "micro_price": micro_price,
        "spread": spread,
        "spread_bps": spread_bps,
        "total_bid_qty": total_bid_qty,
        "total_ask_qty": total_ask_qty,
        "obi": obi,
        "obi_level_1": obi_level_1,
        "obi_level_3": obi_level_3,
        "obi_level_5": obi_level_5
    }

def compute_trades_features(msg: Dict[str, Any]) -> Dict[str, Any]:
    try:
        trade_id = int(msg.get("a", -1))
        price = float(msg.get("p", 0.0))
        quantity = float(msg.get("q", 0.0))
        is_buyer_maker = bool(msg.get("m", False))
    except (ValueError, TypeError):
        return {}

    side_sign = -1 if is_buyer_maker else 1
    signed_qty = quantity * side_sign

    timestamp = int(time.time() * 1000)
    exchange_timestamp = msg.get("E", timestamp)

    return {
        "timestamp": timestamp,
        "exchange_timestamp": exchange_timestamp,
        "local_timestamp": timestamp,
        "trade_id": trade_id,
        "price": price,
        "quantity": quantity,
        "is_buyer_maker": is_buyer_maker,
        "side_sign": side_sign,
        "signed_qty": signed_qty
    }

def compute_markprice_features(msg: Dict[str, Any]) -> Dict[str, Any]:
    try:
        mark_price = float(msg.get("p", 0.0))
        funding_rate = float(msg.get("r", 0.0))
        next_funding_time = int(msg.get("T", 0))
    except (ValueError, TypeError):
        return {}

    funding_rate_bps = funding_rate * 10000.0
    exchange_timestamp = int(msg.get("E", 0))

    hours_to_funding = (next_funding_time - exchange_timestamp) / 3600000.0

    timestamp = int(time.time() * 1000)

    return {
        "timestamp": timestamp,
        "exchange_timestamp": exchange_timestamp,
        "local_timestamp": timestamp,
        "mark_price": mark_price,
        "funding_rate": funding_rate,
        "next_funding_time": next_funding_time,
        "funding_rate_bps": funding_rate_bps,
        "hours_to_funding": hours_to_funding
    }
