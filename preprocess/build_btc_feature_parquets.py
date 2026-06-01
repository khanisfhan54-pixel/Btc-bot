#!/usr/bin/env python3
"""Build L1-only BTCUSDT feature parquets from Binance BookTicker + AggTrades CSV.

VPS preprocessing example:
    python3 preprocess/build_btc_feature_parquets.py \
      --bookticker /home/ubuntu/btc_bot_data/raw/BTCUSDT_240329-bookTicker-2024-01.csv \
      --aggtrades /home/ubuntu/btc_bot_data/raw/BTCUSDT_240329-aggTrades-2024-01.csv \
      --outdir /home/ubuntu/btc_bot_data/processed \
      --symbol BTCUSDT

Feature validation example:
    python3 preprocess/build_btc_feature_parquets.py \
      --validate-parquet /home/ubuntu/btc_bot_data/processed/features_1m.parquet

Important semantics:
- Binance BookTicker is L1/top-of-book only. It is not true multi-level L2 depth.
- ``l1_order_flow_proxy`` is a deterministic L1 proxy from top-of-book changes
  plus signed aggressive trade flow. It is NOT true multi-level L2 OFI.
- Compatibility fields ``ofi_zscore`` and ``ofi_norm`` are aliases for this L1
  proxy only, so downstream code can consume the rows without pretending the
  data is OFI-capable L2.
- Bars are half-open [bar_start_ts_ms, bar_end_ts_ms), and every feature is only
  available at ``feature_available_ts_ms == bar_end_ts_ms``.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

INTERVALS_MS = {"1m": 60_000, "5m": 300_000}

CORE_SCHEMA = [
    "symbol", "bar_interval", "timestamp_ms", "bar_start_ts_ms", "bar_end_ts_ms",
    "feature_available_ts_ms", "bar_date_utc", "source_trade_count", "source_book_update_count",
    "is_complete_bar", "has_trade_data", "has_book_data", "book_stale_ms", "data_quality_flags",
    "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
    "avg_trade_size", "vwap", "twap_trade", "log_ret_close", "return_bps_close",
    "range", "range_bps", "body", "body_bps", "upper_wick", "lower_wick",
    "buy_volume", "sell_volume", "buy_quote_volume", "sell_quote_volume",
    "signed_volume", "signed_quote_volume", "buy_trade_count", "sell_trade_count",
    "volume_imbalance", "quote_volume_imbalance", "taker_buy_ratio", "taker_sell_ratio",
    "bar_cvd_delta_qty", "bar_cvd_delta_quote", "cvd_qty", "cvd_quote",
    "cvd_qty_reset_daily", "cvd_quote_reset_daily",
    "cvd_qty_change_5", "cvd_qty_change_15", "cvd_quote_change_5", "cvd_quote_change_15",
    "best_bid", "best_ask", "best_bid_qty", "best_ask_qty",
    "bid_price", "ask_price", "bid_qty", "ask_qty",
    "mid", "spread", "spread_bps", "tw_mid", "tw_spread", "tw_spread_bps",
    "min_spread_bps", "max_spread_bps", "last_spread_bps",
    "book_imbalance", "imbalance", "last_book_imbalance", "tw_book_imbalance",
    "min_book_imbalance", "max_book_imbalance",
    "microprice", "last_microprice", "tw_microprice",
    "microprice_bps_from_mid", "last_microprice_bps_from_mid", "tw_microprice_bps_from_mid",
    "l1_order_flow_proxy", "l1_order_flow_proxy_z", "ofi_zscore", "ofi_norm", "vol_z",
    "liquidity_score", "fill_prob", "fill_probability", "impact_cost_bps",
    "toxicity", "vpin", "latency_ms", "gap_proxy_bps", "largest_gap_bps",
    "volatility", "atr_pct", "avg_volume", "baseline_volume", "regime",
    "first_trade_ts_ms", "last_trade_ts_ms", "last_book_event_ts_ms", "book_stale",
    # Existing compatibility used by older code/tests.
    "log_ret", "cvd_delta", "cumulative_cvd",
]


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(str(v).strip())
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _to_int_ms(v: Any) -> Optional[int]:
    try:
        x = float(str(v).strip())
    except Exception:
        return None
    if not math.isfinite(x) or x <= 0:
        return None
    if x > 1e15:  # nanoseconds
        x /= 1_000_000.0
    elif x < 1e11:  # seconds
        x *= 1000.0
    return int(x)


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"true", "1", "t", "yes", "y"}


def _first(row: Dict[str, Any], names: Iterable[str]) -> Any:
    lower = {k.lower(): v for k, v in row.items()}
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
        v = lower.get(name.lower())
        if v not in (None, ""):
            return v
    return None


def _bar_start(ts_ms: int, interval_ms: int) -> int:
    return (ts_ms // interval_ms) * interval_ms


def _date_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date().isoformat()


def _flags(parts: Iterable[str]) -> str:
    uniq: List[str] = []
    for p in parts:
        if p and p not in uniq:
            uniq.append(p)
    return "|".join(uniq) if uniq else "OK"


def _stats(values: Iterable[float]) -> Tuple[float, float]:
    vals = list(values)
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    mean = sum(vals) / n
    if n < 2:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in vals) / (n - 1)
    return mean, math.sqrt(max(0.0, var))


def _z(cur: float, hist: Iterable[float]) -> float:
    mean, sd = _stats(hist)
    return (cur - mean) / sd if sd > 1e-12 else 0.0


@dataclass(frozen=True)
class TradeEvent:
    ts_ms: int
    agg_trade_id: Optional[str]
    price: float
    qty: float
    is_buyer_maker: bool

    @property
    def quote_qty(self) -> float:
        return self.price * self.qty

    @property
    def signed_qty(self) -> float:
        # Binance is_buyer_maker=True means buyer was passive, so seller was aggressive.
        return -self.qty if self.is_buyer_maker else self.qty

    @property
    def signed_quote(self) -> float:
        return -self.quote_qty if self.is_buyer_maker else self.quote_qty


@dataclass(frozen=True)
class BookEvent:
    ts_ms: int
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) * 0.5

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid) * 10_000.0 if self.mid > 0 else 0.0

    @property
    def imbalance(self) -> float:
        den = self.bid_qty + self.ask_qty
        return (self.bid_qty - self.ask_qty) / den if den > 0 else 0.0

    @property
    def microprice(self) -> float:
        den = self.bid_qty + self.ask_qty
        return (self.ask * self.bid_qty + self.bid * self.ask_qty) / den if den > 0 else self.mid


def l1_order_flow_proxy(prev: Optional[BookEvent], cur: BookEvent, signed_trade_qty: float = 0.0) -> float:
    """Return a deterministic L1 proxy; this is NOT true multi-level L2 OFI."""
    if prev is None:
        book_part = 0.0
    else:
        bid_part = cur.bid_qty if cur.bid > prev.bid else (-prev.bid_qty if cur.bid < prev.bid else cur.bid_qty - prev.bid_qty)
        ask_part = -cur.ask_qty if cur.ask < prev.ask else (prev.ask_qty if cur.ask > prev.ask else prev.ask_qty - cur.ask_qty)
        book_part = bid_part + ask_part
    return book_part + signed_trade_qty


@dataclass
class TradeAgg:
    source_trade_count: int = 0
    open: Optional[float] = None
    high: float = -math.inf
    low: float = math.inf
    close: Optional[float] = None
    volume: float = 0.0
    quote_volume: float = 0.0
    price_sum: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_quote_volume: float = 0.0
    sell_quote_volume: float = 0.0
    buy_trade_count: int = 0
    sell_trade_count: int = 0
    signed_volume: float = 0.0
    signed_quote_volume: float = 0.0
    first_trade_ts_ms: Optional[int] = None
    last_trade_ts_ms: Optional[int] = None

    def add(self, ev: TradeEvent) -> None:
        if self.open is None:
            self.open = ev.price
            self.first_trade_ts_ms = ev.ts_ms
        self.high = max(self.high, ev.price)
        self.low = min(self.low, ev.price)
        self.close = ev.price
        self.volume += ev.qty
        self.quote_volume += ev.quote_qty
        self.price_sum += ev.price
        self.source_trade_count += 1
        self.last_trade_ts_ms = ev.ts_ms
        if ev.is_buyer_maker:
            self.sell_volume += ev.qty
            self.sell_quote_volume += ev.quote_qty
            self.sell_trade_count += 1
        else:
            self.buy_volume += ev.qty
            self.buy_quote_volume += ev.quote_qty
            self.buy_trade_count += 1
        self.signed_volume += ev.signed_qty
        self.signed_quote_volume += ev.signed_quote


def read_trades(path: str, intervals: Dict[str, int]) -> Tuple[Dict[str, Dict[int, TradeAgg]], Dict[str, int], Tuple[Optional[int], Optional[int]]]:
    counts = {"raw": 0, "invalid": 0, "duplicates_dropped": 0}
    events: List[TradeEvent] = []
    by_id: Dict[str, TradeEvent] = {}
    with open(path, "r", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        for line_no, row in enumerate(rd, start=2):
            counts["raw"] += 1
            ts = _to_int_ms(_first(row, ["transact_time", "T", "time", "timestamp", "timestamp_ms"]))
            price = _to_float(_first(row, ["price", "p"]))
            qty = _to_float(_first(row, ["quantity", "qty", "q"]))
            agg_id_raw = _first(row, ["agg_trade_id", "aggregate_trade_id", "a", "id"])
            agg_id = str(agg_id_raw).strip() if agg_id_raw not in (None, "") else None
            if ts is None or price <= 0.0 or qty < 0.0:
                counts["invalid"] += 1
                continue
            ev = TradeEvent(ts, agg_id, price, qty, _truthy(_first(row, ["is_buyer_maker", "m", "buyer_maker"])))
            if agg_id is not None:
                prior = by_id.get(agg_id)
                if prior is not None:
                    if prior != ev:
                        raise ValueError(f"Conflicting duplicate agg_trade_id={agg_id!r} at {path}:{line_no}")
                    counts["duplicates_dropped"] += 1
                    continue
                by_id[agg_id] = ev
            events.append(ev)
    events.sort(key=lambda e: (e.ts_ms, int(e.agg_trade_id) if e.agg_trade_id and e.agg_trade_id.isdigit() else e.agg_trade_id or ""))
    bars: Dict[str, Dict[int, TradeAgg]] = {k: defaultdict(TradeAgg) for k in intervals}
    for ev in events:
        for name, ms in intervals.items():
            bars[name][_bar_start(ev.ts_ms, ms)].add(ev)
    bounds = (events[0].ts_ms if events else None, events[-1].ts_ms if events else None)
    return bars, counts, bounds


def read_books(path: str) -> Tuple[List[BookEvent], Dict[str, int], Tuple[Optional[int], Optional[int]]]:
    counts = {"raw": 0, "invalid": 0, "crossed": 0, "locked": 0}
    out: List[BookEvent] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            counts["raw"] += 1
            ts = _to_int_ms(_first(row, ["event_time", "E", "transaction_time", "T", "timestamp", "timestamp_ms"]))
            bid = _to_float(_first(row, ["best_bid_price", "bid_price", "bidPrice", "b"]))
            bid_qty = _to_float(_first(row, ["best_bid_qty", "bid_qty", "bidQty", "B"]))
            ask = _to_float(_first(row, ["best_ask_price", "ask_price", "askPrice", "a"]))
            ask_qty = _to_float(_first(row, ["best_ask_qty", "ask_qty", "askQty", "A"]))
            if ts is None or bid <= 0.0 or ask <= 0.0 or bid_qty < 0.0 or ask_qty < 0.0:
                counts["invalid"] += 1
                continue
            if ask < bid:
                counts["crossed"] += 1
                continue
            if ask == bid:
                counts["locked"] += 1
            out.append(BookEvent(ts, bid, bid_qty, ask, ask_qty))
    out.sort(key=lambda e: e.ts_ms)
    bounds = (out[0].ts_ms if out else None, out[-1].ts_ms if out else None)
    return out, counts, bounds


def _classify_regime(volatility: float, ret_bps: float) -> str:
    if volatility > 0.01:
        return "VOLATILE"
    if ret_bps > 5.0:
        return "TREND_UP"
    if ret_bps < -5.0:
        return "TREND_DOWN"
    return "RANGE"


def build_interval(
    symbol: str,
    interval_name: str,
    interval_ms: int,
    trades: Dict[int, TradeAgg],
    books: List[BookEvent],
    stale_limit_ms: int,
    rolling_window: int,
    source_min_ts: Optional[int],
    source_max_ts: Optional[int],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    if source_min_ts is None or source_max_ts is None:
        return [], {"warmup": 0, "lookahead_violations": 0}
    start = _bar_start(source_min_ts, interval_ms)
    end = _bar_start(source_max_ts + interval_ms, interval_ms)

    rows: List[Dict[str, Any]] = []
    stats = {"warmup": 0, "lookahead_violations": 0}
    j = 0
    cur: Optional[BookEvent] = None
    prev_close: Optional[float] = None
    cvd_qty = 0.0
    cvd_quote = 0.0
    daily_cvd_qty = 0.0
    daily_cvd_quote = 0.0
    current_day: Optional[str] = None
    vol_hist: Deque[float] = deque(maxlen=rolling_window)
    proxy_hist: Deque[float] = deque(maxlen=rolling_window)
    close_ret_hist: Deque[float] = deque(maxlen=rolling_window)
    true_range_hist: Deque[float] = deque(maxlen=14)
    cvd_qty_hist: Deque[float] = deque(maxlen=16)
    cvd_quote_hist: Deque[float] = deque(maxlen=16)
    vpin_num_hist: Deque[float] = deque(maxlen=rolling_window)
    vpin_den_hist: Deque[float] = deque(maxlen=rolling_window)

    bar = start
    while bar < end:
        bar_end = bar + interval_ms
        day = _date_utc(bar)
        if current_day != day:
            daily_cvd_qty = 0.0
            daily_cvd_quote = 0.0
            current_day = day
        flags: List[str] = []
        if bar == start or bar_end > source_max_ts:
            flags.append("PARTIAL_BAR")
        while j < len(books) and books[j].ts_ms < bar:
            cur = books[j]
            j += 1

        seg_t = bar
        total_ms = 0
        tw_mid_num = tw_spread_num = tw_spread_bps_num = 0.0
        tw_imb_num = tw_micro_num = 0.0
        spreads: List[float] = []
        imbalances: List[float] = []
        bar_proxy_book = 0.0
        source_book_update_count = 0
        k = j
        local_cur = cur
        while k < len(books) and books[k].ts_ms < bar_end:
            ev = books[k]
            if local_cur is not None and ev.ts_ms > seg_t:
                expiry = local_cur.ts_ms + stale_limit_ms
                usable_until = min(ev.ts_ms, expiry)
                if usable_until > seg_t:
                    dt = usable_until - seg_t
                    tw_mid_num += local_cur.mid * dt
                    tw_spread_num += local_cur.spread * dt
                    tw_spread_bps_num += local_cur.spread_bps * dt
                    tw_imb_num += local_cur.imbalance * dt
                    tw_micro_num += local_cur.microprice * dt
                    total_ms += dt
                if expiry < ev.ts_ms:
                    flags.append("BOOK_STALE")
            bar_proxy_book += l1_order_flow_proxy(local_cur, ev, 0.0)
            local_cur = ev
            spreads.append(ev.spread_bps)
            imbalances.append(ev.imbalance)
            source_book_update_count += 1
            seg_t = ev.ts_ms
            k += 1
        if local_cur is not None and bar_end > seg_t:
            expiry = local_cur.ts_ms + stale_limit_ms
            usable_until = min(bar_end, expiry)
            if usable_until > seg_t:
                dt = usable_until - seg_t
                tw_mid_num += local_cur.mid * dt
                tw_spread_num += local_cur.spread * dt
                tw_spread_bps_num += local_cur.spread_bps * dt
                tw_imb_num += local_cur.imbalance * dt
                tw_micro_num += local_cur.microprice * dt
                total_ms += dt
            if expiry < bar_end:
                flags.append("BOOK_STALE")
        cur = local_cur
        j = k
        snap = cur
        if snap is None:
            flags.append("BOOK_MISSING")
        elif snap.ts_ms < bar:
            flags.append("BOOK_FFILLED")
        book_stale_ms = (bar_end - snap.ts_ms) if snap is not None else None
        stale = snap is None or (book_stale_ms is not None and book_stale_ms > stale_limit_ms)
        if stale:
            flags.append("BOOK_STALE" if snap is not None else "BOOK_MISSING")
        if snap is not None and snap.ask == snap.bid:
            flags.append("LOCKED_BOOK")

        tr = trades.get(bar)
        has_trade_data = tr is not None and tr.source_trade_count > 0
        if not has_trade_data:
            if prev_close is None:
                bar = bar_end
                continue
            flags.append("NO_TRADES")
            o = h = l = c = prev_close
            volume = quote_volume = price_sum = 0.0
            buy_volume = sell_volume = buy_qv = sell_qv = 0.0
            signed_volume = signed_quote = 0.0
            buy_count = sell_count = trade_count = source_trade_count = 0
            first_trade_ts = last_trade_ts = None
        else:
            assert tr is not None
            o = float(tr.open)
            h = float(tr.high)
            l = float(tr.low)
            c = float(tr.close)
            volume = tr.volume
            quote_volume = tr.quote_volume
            price_sum = tr.price_sum
            buy_volume = tr.buy_volume
            sell_volume = tr.sell_volume
            buy_qv = tr.buy_quote_volume
            sell_qv = tr.sell_quote_volume
            signed_volume = tr.signed_volume
            signed_quote = tr.signed_quote_volume
            buy_count = tr.buy_trade_count
            sell_count = tr.sell_trade_count
            trade_count = tr.source_trade_count
            source_trade_count = tr.source_trade_count
            first_trade_ts = tr.first_trade_ts_ms
            last_trade_ts = tr.last_trade_ts_ms

        cvd_qty += signed_volume
        cvd_quote += signed_quote
        daily_cvd_qty += signed_volume
        daily_cvd_quote += signed_quote
        cvd_qty_hist.append(cvd_qty)
        cvd_quote_hist.append(cvd_quote)

        avg_trade_size = volume / trade_count if trade_count else 0.0
        vwap = quote_volume / volume if volume > 0 else c
        twap_trade = price_sum / trade_count if trade_count else c
        log_ret_close = math.log(c / prev_close) if prev_close and prev_close > 0 and c > 0 else 0.0
        return_bps_close = log_ret_close * 10_000.0
        bar_range = h - l
        range_bps = (bar_range / c) * 10_000.0 if c > 0 else 0.0
        body = c - o
        body_bps = (body / o) * 10_000.0 if o > 0 else 0.0
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        volume_imbalance = signed_volume / volume if volume > 0 else 0.0
        quote_volume_imbalance = signed_quote / quote_volume if quote_volume > 0 else 0.0
        taker_buy_ratio = buy_volume / volume if volume > 0 else 0.0
        taker_sell_ratio = sell_volume / volume if volume > 0 else 0.0

        mid = snap.mid if snap is not None else c
        spread = snap.spread if snap is not None else 0.0
        spread_bps = snap.spread_bps if snap is not None else 0.0
        book_imb = snap.imbalance if snap is not None else 0.0
        micro = snap.microprice if snap is not None else c
        tw_mid = tw_mid_num / total_ms if total_ms else mid
        tw_spread = tw_spread_num / total_ms if total_ms else spread
        tw_spread_bps = tw_spread_bps_num / total_ms if total_ms else spread_bps
        tw_book_imb = tw_imb_num / total_ms if total_ms else book_imb
        tw_micro = tw_micro_num / total_ms if total_ms else micro
        min_spread_bps = min(spreads) if spreads else spread_bps
        max_spread_bps = max(spreads) if spreads else spread_bps
        min_book_imb = min(imbalances) if imbalances else book_imb
        max_book_imb = max(imbalances) if imbalances else book_imb
        depth = (snap.bid_qty + snap.ask_qty) if snap is not None else 0.0
        m_bps = ((micro - mid) / mid) * 10_000.0 if mid > 0 else 0.0
        tw_m_bps = ((tw_micro - tw_mid) / tw_mid) * 10_000.0 if tw_mid > 0 else 0.0
        gap_proxy_bps = max(0.0, abs(twap_trade - mid) / mid * 10_000.0) if mid > 0 else 0.0
        largest_gap_bps = max_spread_bps

        # The proxy intentionally combines only L1 top-of-book changes and current-bar signed flow.
        # It is not true multi-level L2 OFI.
        proxy = bar_proxy_book + signed_volume
        proxy_hist.append(proxy)
        l1_proxy_z = _z(proxy, proxy_hist)
        vol_hist.append(volume)
        vol_z = _z(volume, vol_hist)
        close_ret_hist.append(log_ret_close)
        volatility = _stats(close_ret_hist)[1]
        prev_for_tr = prev_close if prev_close and prev_close > 0 else o
        true_range = max(h - l, abs(h - prev_for_tr), abs(l - prev_for_tr))
        true_range_hist.append(true_range / c if c > 0 else 0.0)
        atr_pct = sum(true_range_hist) / len(true_range_hist) if true_range_hist else 0.0
        avg_volume, _ = _stats(vol_hist)
        baseline_volume = avg_volume
        if len(vol_hist) < min(rolling_window, 20):
            flags.append("WARMUP")
            stats["warmup"] += 1
        vpin_num_hist.append(abs(signed_volume))
        vpin_den_hist.append(volume)
        vpin_den = sum(vpin_den_hist)
        vpin = sum(vpin_num_hist) / vpin_den if vpin_den > 0 else 0.0
        toxicity = min(1.0, abs(volume_imbalance))
        liquidity_score = 0.0 if stale else max(0.0, min(1.0, (depth / (depth + 1.0)) * (1.0 / (1.0 + spread_bps / 10.0))))
        fill_prob = 0.0 if stale else max(0.0, min(1.0, liquidity_score * (1.0 - min(spread_bps / 100.0, 0.75))))
        impact_cost_bps = 10_000.0 if stale else max(0.0, spread_bps * 0.5 + (100.0 / max(depth, 1e-9)))
        regime = _classify_regime(volatility, return_bps_close)
        has_book_data = snap is not None and not stale
        is_complete_bar = "PARTIAL_BAR" not in flags
        last_book_ts = snap.ts_ms if snap is not None else None
        if (last_trade_ts is not None and last_trade_ts >= bar_end) or (last_book_ts is not None and last_book_ts >= bar_end):
            stats["lookahead_violations"] += 1

        row = {
            "symbol": symbol,
            "bar_interval": interval_name,
            "timestamp_ms": bar,
            "bar_start_ts_ms": bar,
            "bar_end_ts_ms": bar_end,
            "feature_available_ts_ms": bar_end,
            "bar_date_utc": day,
            "source_trade_count": source_trade_count,
            "source_book_update_count": source_book_update_count,
            "is_complete_bar": bool(is_complete_bar),
            "has_trade_data": bool(has_trade_data),
            "has_book_data": bool(has_book_data),
            "book_stale_ms": book_stale_ms,
            "data_quality_flags": _flags(flags),
            "open": o, "high": h, "low": l, "close": c,
            "volume": volume, "quote_volume": quote_volume, "trade_count": trade_count,
            "avg_trade_size": avg_trade_size, "vwap": vwap, "twap_trade": twap_trade,
            "log_ret_close": log_ret_close, "return_bps_close": return_bps_close,
            "range": bar_range, "range_bps": range_bps, "body": body, "body_bps": body_bps,
            "upper_wick": upper_wick, "lower_wick": lower_wick,
            "buy_volume": buy_volume, "sell_volume": sell_volume,
            "buy_quote_volume": buy_qv, "sell_quote_volume": sell_qv,
            "signed_volume": signed_volume, "signed_quote_volume": signed_quote,
            "buy_trade_count": buy_count, "sell_trade_count": sell_count,
            "volume_imbalance": volume_imbalance, "quote_volume_imbalance": quote_volume_imbalance,
            "taker_buy_ratio": taker_buy_ratio, "taker_sell_ratio": taker_sell_ratio,
            "bar_cvd_delta_qty": signed_volume, "bar_cvd_delta_quote": signed_quote,
            "cvd_qty": cvd_qty, "cvd_quote": cvd_quote,
            "cvd_qty_reset_daily": daily_cvd_qty, "cvd_quote_reset_daily": daily_cvd_quote,
            "cvd_qty_change_5": cvd_qty - (list(cvd_qty_hist)[-6] if len(cvd_qty_hist) >= 6 else 0.0),
            "cvd_qty_change_15": cvd_qty - (list(cvd_qty_hist)[-16] if len(cvd_qty_hist) >= 16 else 0.0),
            "cvd_quote_change_5": cvd_quote - (list(cvd_quote_hist)[-6] if len(cvd_quote_hist) >= 6 else 0.0),
            "cvd_quote_change_15": cvd_quote - (list(cvd_quote_hist)[-16] if len(cvd_quote_hist) >= 16 else 0.0),
            "best_bid": snap.bid if snap is not None else 0.0,
            "best_ask": snap.ask if snap is not None else 0.0,
            "best_bid_qty": snap.bid_qty if snap is not None else 0.0,
            "best_ask_qty": snap.ask_qty if snap is not None else 0.0,
            "bid_price": snap.bid if snap is not None else 0.0,
            "ask_price": snap.ask if snap is not None else 0.0,
            "bid_qty": snap.bid_qty if snap is not None else 0.0,
            "ask_qty": snap.ask_qty if snap is not None else 0.0,
            "mid": mid,
            "spread": spread,
            "spread_bps": spread_bps,
            "tw_mid": tw_mid,
            "tw_spread": tw_spread,
            "tw_spread_bps": tw_spread_bps,
            "min_spread_bps": min_spread_bps,
            "max_spread_bps": max_spread_bps,
            "last_spread_bps": spread_bps,
            "book_imbalance": max(-1.0, min(1.0, book_imb)),
            "imbalance": max(-1.0, min(1.0, book_imb)),
            "last_book_imbalance": max(-1.0, min(1.0, book_imb)),
            "tw_book_imbalance": max(-1.0, min(1.0, tw_book_imb)),
            "min_book_imbalance": max(-1.0, min(1.0, min_book_imb)),
            "max_book_imbalance": max(-1.0, min(1.0, max_book_imb)),
            "microprice": micro,
            "last_microprice": micro,
            "tw_microprice": tw_micro,
            "microprice_bps_from_mid": m_bps,
            "last_microprice_bps_from_mid": m_bps,
            "tw_microprice_bps_from_mid": tw_m_bps,
            "l1_order_flow_proxy": proxy,
            "l1_order_flow_proxy_z": l1_proxy_z,
            # L1-only compatibility aliases. These are not true multi-level L2 OFI.
            "ofi_zscore": l1_proxy_z,
            "ofi_norm": proxy,
            "vol_z": vol_z,
            "liquidity_score": liquidity_score,
            "fill_prob": fill_prob,
            "fill_probability": fill_prob,
            "impact_cost_bps": impact_cost_bps,
            "toxicity": toxicity,
            "vpin": min(1.0, vpin),
            "latency_ms": 0,
            "gap_proxy_bps": gap_proxy_bps,
            "largest_gap_bps": largest_gap_bps,
            "volatility": volatility,
            "atr_pct": atr_pct,
            "avg_volume": avg_volume,
            "baseline_volume": baseline_volume,
            "regime": regime,
            "first_trade_ts_ms": first_trade_ts,
            "last_trade_ts_ms": last_trade_ts,
            "last_book_event_ts_ms": last_book_ts,
            "book_stale": bool(stale),
            # Backward-compatible names from the first patch.
            "log_ret": log_ret_close,
            "cvd_delta": signed_volume,
            "cumulative_cvd": cvd_qty,
        }
        rows.append(row)
        prev_close = c
        bar = bar_end
    validate_rows(rows, interval_ms)
    return rows, stats


def validate_rows(rows: List[Dict[str, Any]], interval_ms: Optional[int] = None) -> Dict[str, int]:
    missing_schema = [c for c in CORE_SCHEMA if rows and c not in rows[0]]
    if missing_schema:
        raise ValueError(f"Missing required schema columns: {missing_schema}")
    prev: Optional[int] = None
    warmup = 0
    for i, r in enumerate(rows):
        bs = int(r["bar_start_ts_ms"])
        be = int(r["bar_end_ts_ms"])
        if prev is not None and bs <= prev:
            raise ValueError(f"bar_start_ts_ms not strictly increasing at row {i}")
        prev = bs
        if interval_ms is None:
            interval_ms = be - bs
        checks = [
            (be == bs + interval_ms, "bad bar_end_ts_ms"),
            (int(r["feature_available_ts_ms"]) == be, "bad feature_available_ts_ms"),
            (int(r["timestamp_ms"]) == bs, "bad timestamp_ms"),
            (float(r["open"]) > 0 and float(r["close"]) > 0, "non-positive open/close"),
            (float(r["high"]) >= float(r["low"]), "high < low"),
            (float(r["spread"]) >= 0, "negative spread"),
            (float(r["best_ask"]) >= float(r["best_bid"]), "ask < bid"),
            (-1.0 <= float(r["book_imbalance"]) <= 1.0, "book_imbalance out of range"),
            (abs(float(r["volume"]) - (float(r["buy_volume"]) + float(r["sell_volume"]))) < 1e-8, "volume mismatch"),
            (abs(float(r["quote_volume"]) - (float(r["buy_quote_volume"]) + float(r["sell_quote_volume"]))) < 1e-5, "quote volume mismatch"),
            (int(r["trade_count"]) == int(r["buy_trade_count"]) + int(r["sell_trade_count"]), "trade count mismatch"),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(f"{msg} at row {i} bar_start={bs}")
        if r.get("last_trade_ts_ms") is not None and int(r["last_trade_ts_ms"]) >= be:
            raise ValueError(f"last_trade_ts_ms lookahead at row {i}")
        if r.get("last_book_event_ts_ms") is not None and int(r["last_book_event_ts_ms"]) >= be:
            raise ValueError(f"last_book_event_ts_ms lookahead at row {i}")
        if "WARMUP" in str(r.get("data_quality_flags", "")):
            warmup += 1
    return {"rows": len(rows), "warmup_rows": warmup}


def write_parquet(rows: List[Dict[str, Any]], path: str) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to write parquet. Install requirements.txt on the VPS.") from exc
    os.makedirs(os.path.dirname(path), exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd")


def read_parquet_rows(path: str) -> List[Dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to read parquet. Install requirements.txt on the VPS.") from exc
    return pq.read_table(path).to_pylist()


def validate_parquet(path: str) -> Dict[str, int]:
    rows = read_parquet_rows(path)
    if rows:
        interval_ms = int(rows[0]["bar_end_ts_ms"]) - int(rows[0]["bar_start_ts_ms"])
    else:
        interval_ms = None
    result = validate_rows(rows, interval_ms=interval_ms)
    print(json.dumps({"path": path, **result, "schema_sample": list(rows[0].keys())[:100] if rows else []}, indent=2))
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Build or validate L1-only BTCUSDT feature parquets from Binance BookTicker + AggTrades CSV")
    ap.add_argument("--bookticker")
    ap.add_argument("--aggtrades")
    ap.add_argument("--outdir")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--stale-limit-ms", type=int, default=120_000)
    ap.add_argument("--rolling-window", type=int, default=50)
    ap.add_argument("--validate-parquet", action="append", default=[])
    args = ap.parse_args()

    if args.validate_parquet:
        for path in args.validate_parquet:
            validate_parquet(path)
        if not (args.bookticker or args.aggtrades or args.outdir):
            return

    if not (args.bookticker and args.aggtrades and args.outdir):
        raise SystemExit("--bookticker, --aggtrades, and --outdir are required unless only --validate-parquet is used")
    for p in (args.bookticker, args.aggtrades):
        if not os.path.isabs(p):
            raise SystemExit(f"Raw input path must be absolute: {p}")
        if not os.path.exists(p):
            raise SystemExit(f"Input file not found: {p}")

    trade_bars, trade_counts, trade_bounds = read_trades(args.aggtrades, INTERVALS_MS)
    books, book_counts, book_bounds = read_books(args.bookticker)
    source_min = min(x for x in [trade_bounds[0], book_bounds[0]] if x is not None) if any(x is not None for x in [trade_bounds[0], book_bounds[0]]) else None
    source_max = max(x for x in [trade_bounds[1], book_bounds[1]] if x is not None) if any(x is not None for x in [trade_bounds[1], book_bounds[1]]) else None

    print(f"raw aggTrades rows read: {trade_counts['raw']}")
    print(f"raw BookTicker rows read: {book_counts['raw']}")
    print(f"invalid trade rows dropped: {trade_counts['invalid']}")
    print(f"duplicate trade rows dropped: {trade_counts['duplicates_dropped']}")
    print(f"invalid book rows dropped: {book_counts['invalid']}")
    print(f"crossed book rows dropped: {book_counts['crossed']}")
    print(f"locked book rows kept and flagged: {book_counts['locked']}")

    warmup_total = 0
    lookahead_total = 0
    last_rows: List[Dict[str, Any]] = []
    for name, ms in INTERVALS_MS.items():
        rows, stats = build_interval(args.symbol, name, ms, trade_bars[name], books, args.stale_limit_ms, args.rolling_window, source_min, source_max)
        out = os.path.join(args.outdir, f"features_{name}.parquet")
        write_parquet(rows, out)
        warmup_total += stats["warmup"]
        lookahead_total += stats["lookahead_violations"]
        last_rows = rows
        print(f"bars written for {name}: {len(rows)}")
        print(f"warmup rows for {name}: {stats['warmup']}")
        print(f"final parquet path for {name}: {out}")
    print("sample output schema:")
    print(json.dumps(list(last_rows[0].keys())[:100] if last_rows else [], indent=2))
    print(f"total warmup rows: {warmup_total}")
    print(f"lookahead violations detected: {lookahead_total}")
    if lookahead_total:
        raise SystemExit("Lookahead validation failed")
    print("confirmation: no lookahead violation was detected")


if __name__ == "__main__":
    main()
