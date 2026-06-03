from typing import Dict, Any

def compute_orderbook_features(raw_msg: Dict[str, Any]) -> Dict[str, Any]:
    bids = raw_msg.get('b', [])
    asks = raw_msg.get('a', [])

    bids_price = [float(p) for p, _ in bids]
    bids_qty = [float(q) for _, q in bids]
    asks_price = [float(p) for p, _ in asks]
    asks_qty = [float(q) for _, q in asks]

    best_bid = bids_price[0] if bids_price else 0
    best_ask = asks_price[0] if asks_price else 0
    mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0

    micro_price = 0
    if bids_qty and asks_qty and (bids_qty[0] + asks_qty[0]) > 0:
        micro_price = (best_bid * asks_qty[0] + best_ask * bids_qty[0]) / (bids_qty[0] + asks_qty[0])

    spread = best_ask - best_bid
    spread_bps = (spread / mid_price * 10000) if mid_price > 0 else 0

    total_bid_qty = sum(bids_qty)
    total_ask_qty = sum(asks_qty)

    obi = 0
    if total_bid_qty + total_ask_qty > 0:
        obi = (total_bid_qty - total_ask_qty) / (total_bid_qty + total_ask_qty)

    obi_level_1, obi_level_3, obi_level_5 = 0, 0, 0
    if bids_qty and asks_qty:
        b1, a1 = bids_qty[0], asks_qty[0]
        if b1 + a1 > 0: obi_level_1 = (b1 - a1) / (b1 + a1)

        b3, a3 = sum(bids_qty[:3]), sum(asks_qty[:3])
        if b3 + a3 > 0: obi_level_3 = (b3 - a3) / (b3 + a3)

        b5, a5 = sum(bids_qty[:5]), sum(asks_qty[:5])
        if b5 + a5 > 0: obi_level_5 = (b5 - a5) / (b5 + a5)

    return {
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

def compute_trade_features(raw_msg: Dict[str, Any]) -> Dict[str, Any]:
    qty = float(raw_msg.get('q', 0))
    is_buyer_maker = raw_msg.get('m', False)

    side_sign = -1 if is_buyer_maker else 1
    signed_qty = qty * side_sign

    return {
        "side_sign": side_sign,
        "signed_qty": signed_qty
    }

def compute_markprice_features(raw_msg: Dict[str, Any]) -> Dict[str, Any]:
    funding_rate = float(raw_msg.get('r', 0))
    next_funding_time = int(raw_msg.get('T', 0))
    exchange_timestamp = int(raw_msg.get('E', 0))

    funding_rate_bps = funding_rate * 10000
    hours_to_funding = (next_funding_time - exchange_timestamp) / 3_600_000 if next_funding_time else 0

    return {
        "funding_rate_bps": funding_rate_bps,
        "hours_to_funding": hours_to_funding
    }
