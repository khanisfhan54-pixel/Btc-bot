#!/usr/bin/env python3
"""Build L1-only BTCUSDT feature parquet bars from Binance BookTicker + AggTrades CSV.

This pipeline intentionally treats Binance BookTicker as top-of-book (L1) data
only.  The column named ``l1_order_flow_proxy`` is a deterministic L1 proxy based
on changes in best bid/ask price and size; it is not real multi-level L2 OFI.
Compatibility aliases (``ofi_zscore``/``ofi_norm``) expose that same L1 proxy to
legacy bot code without routing BookTicker through the true-L2 loader.

All features are available only at ``bar_end_ts_ms`` and bars use half-open
intervals [bar_start_ts_ms, bar_end_ts_ms), so no future book/trade update is
included in the row.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

REQUIRED_BACKTEST_FIELDS = [
    "log_ret", "ofi_zscore", "vol_z", "spread_bps", "liquidity_score",
    "fill_prob", "impact_cost_bps",
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
    if x > 1e15:  # ns
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


@dataclass
class TradeAgg:
    open: Optional[float] = None
    high: float = -math.inf
    low: float = math.inf
    close: Optional[float] = None
    volume: float = 0.0
    quote_volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_quote_volume: float = 0.0
    sell_quote_volume: float = 0.0
    trade_count: int = 0
    buy_trade_count: int = 0
    sell_trade_count: int = 0
    signed_volume: float = 0.0
    signed_quote_volume: float = 0.0
    first_trade_ts_ms: Optional[int] = None
    last_trade_ts_ms: Optional[int] = None

    def add(self, ts_ms: int, price: float, qty: float, buyer_maker: bool) -> None:
        quote = price * qty
        if self.open is None:
            self.open = price
            self.first_trade_ts_ms = ts_ms
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += qty
        self.quote_volume += quote
        self.trade_count += 1
        self.last_trade_ts_ms = ts_ms
        # Binance is_buyer_maker=True means the buyer was passive; aggressive side is sell.
        if buyer_maker:
            self.sell_volume += qty
            self.sell_quote_volume += quote
            self.sell_trade_count += 1
            self.signed_volume -= qty
            self.signed_quote_volume -= quote
        else:
            self.buy_volume += qty
            self.buy_quote_volume += quote
            self.buy_trade_count += 1
            self.signed_volume += qty
            self.signed_quote_volume += quote


@dataclass
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
        return (self.spread / self.mid) * 10000.0 if self.mid > 0 else 0.0

    @property
    def imbalance(self) -> float:
        den = self.bid_qty + self.ask_qty
        return (self.bid_qty - self.ask_qty) / den if den > 0 else 0.0

    @property
    def microprice(self) -> float:
        den = self.bid_qty + self.ask_qty
        return (self.ask * self.bid_qty + self.bid * self.ask_qty) / den if den > 0 else self.mid


def _l1_order_flow_proxy(prev: Optional[BookEvent], cur: BookEvent) -> float:
    """L1 top-of-book order-flow proxy, not real L2 OFI."""
    if prev is None:
        return 0.0
    bid_part = cur.bid_qty if cur.bid > prev.bid else (-prev.bid_qty if cur.bid < prev.bid else cur.bid_qty - prev.bid_qty)
    ask_part = -cur.ask_qty if cur.ask < prev.ask else (prev.ask_qty if cur.ask > prev.ask else prev.ask_qty - cur.ask_qty)
    return bid_part + ask_part


def read_trades(path: str, intervals: Dict[str, int]) -> Tuple[Dict[str, Dict[int, TradeAgg]], Dict[str, int]]:
    counts = {"raw": 0, "invalid": 0}
    bars: Dict[str, Dict[int, TradeAgg]] = {k: defaultdict(TradeAgg) for k in intervals}
    with open(path, "r", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            counts["raw"] += 1
            ts = _to_int_ms(_first(row, ["transact_time", "T", "time", "timestamp", "timestamp_ms"]))
            price = _to_float(_first(row, ["price", "p"]))
            qty = _to_float(_first(row, ["quantity", "qty", "q"]))
            if ts is None or price <= 0 or qty < 0:
                counts["invalid"] += 1
                continue
            buyer_maker = _truthy(_first(row, ["is_buyer_maker", "m", "buyer_maker"]))
            for name, ms in intervals.items():
                bars[name][_bar_start(ts, ms)].add(ts, price, qty, buyer_maker)
    return bars, counts


def read_books(path: str) -> Tuple[List[BookEvent], Dict[str, int]]:
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
            if ts is None or bid <= 0 or ask <= 0 or bid_qty < 0 or ask_qty < 0:
                counts["invalid"] += 1
                continue
            if ask < bid:
                counts["crossed"] += 1
                continue
            if ask == bid:
                counts["locked"] += 1
            out.append(BookEvent(ts, bid, bid_qty, ask, ask_qty))
    out.sort(key=lambda e: e.ts_ms)
    return out, counts


def _stats(values: Deque[float]) -> Tuple[float, float]:
    n = len(values)
    if n < 2:
        return (sum(values) / n if n else 0.0, 0.0)
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(max(0.0, var))


def _z(cur: float, hist: Deque[float]) -> float:
    mean, sd = _stats(hist)
    return (cur - mean) / sd if sd > 1e-12 else 0.0


def _flags(parts: Iterable[str]) -> str:
    uniq = []
    for p in parts:
        if p and p not in uniq:
            uniq.append(p)
    return "|".join(uniq) if uniq else "OK"


def build_interval(
    symbol: str,
    interval_name: str,
    interval_ms: int,
    trades: Dict[int, TradeAgg],
    books: List[BookEvent],
    stale_limit_ms: int,
    rolling_window: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    if not trades and not books:
        return [], {"warmup": 0, "lookahead_violations": 0}
    min_ts = min([min(trades) if trades else books[0].ts_ms, books[0].ts_ms if books else min(trades)])
    max_ts = max([max(trades) + interval_ms if trades else books[-1].ts_ms + 1, books[-1].ts_ms + 1 if books else max(trades) + interval_ms])
    start = _bar_start(min_ts, interval_ms)
    end = _bar_start(max_ts + interval_ms - 1, interval_ms)

    rows: List[Dict[str, Any]] = []
    stats = {"warmup": 0, "lookahead_violations": 0}
    j = 0
    cur: Optional[BookEvent] = None
    prev_close: Optional[float] = None
    cumulative_cvd = 0.0
    vol_hist: Deque[float] = deque(maxlen=rolling_window)
    proxy_hist: Deque[float] = deque(maxlen=rolling_window)
    vpin_num_hist: Deque[float] = deque(maxlen=rolling_window)
    vpin_den_hist: Deque[float] = deque(maxlen=rolling_window)

    bar = start
    while bar < end:
        bar_end = bar + interval_ms
        flags: List[str] = []
        while j < len(books) and books[j].ts_ms < bar:
            cur = books[j]
            j += 1

        # time-weighted book stats over [bar, bar_end) with stale expiry.
        seg_t = bar
        tw = {"mid": 0.0, "spread_bps": 0.0, "imbalance": 0.0, "microprice": 0.0}
        total_ms = 0
        bar_proxy = 0.0
        k = j
        local_cur = cur
        while k < len(books) and books[k].ts_ms < bar_end:
            ev = books[k]
            if local_cur is not None and ev.ts_ms > seg_t:
                expiry = local_cur.ts_ms + stale_limit_ms
                usable_until = min(ev.ts_ms, expiry)
                if usable_until > seg_t:
                    dt = usable_until - seg_t
                    tw["mid"] += local_cur.mid * dt
                    tw["spread_bps"] += local_cur.spread_bps * dt
                    tw["imbalance"] += local_cur.imbalance * dt
                    tw["microprice"] += local_cur.microprice * dt
                    total_ms += dt
                if expiry < ev.ts_ms:
                    flags.append("BOOK_STALE")
            bar_proxy += _l1_order_flow_proxy(local_cur, ev)
            local_cur = ev
            seg_t = ev.ts_ms
            k += 1
        if local_cur is not None and bar_end > seg_t:
            expiry = local_cur.ts_ms + stale_limit_ms
            usable_until = min(bar_end, expiry)
            if usable_until > seg_t:
                dt = usable_until - seg_t
                tw["mid"] += local_cur.mid * dt
                tw["spread_bps"] += local_cur.spread_bps * dt
                tw["imbalance"] += local_cur.imbalance * dt
                tw["microprice"] += local_cur.microprice * dt
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
        stale = snap is None or (bar_end - snap.ts_ms) > stale_limit_ms
        if stale:
            flags.append("BOOK_STALE" if snap is not None else "BOOK_MISSING")
        if snap is not None and snap.ask == snap.bid:
            flags.append("LOCKED_BOOK")

        tr = trades.get(bar)
        if tr is None or tr.trade_count == 0:
            if prev_close is None:
                bar = bar_end
                continue
            flags.append("NO_TRADES")
            o = h = l = c = prev_close
            volume = quote_volume = buy_volume = sell_volume = 0.0
            buy_qv = sell_qv = signed_vol = signed_qv = 0.0
            trade_count = buy_tc = sell_tc = 0
            first_trade_ts = last_trade_ts = None
        else:
            o = float(tr.open)
            h = float(tr.high)
            l = float(tr.low)
            c = float(tr.close)
            volume = tr.volume
            quote_volume = tr.quote_volume
            buy_volume = tr.buy_volume
            sell_volume = tr.sell_volume
            buy_qv = tr.buy_quote_volume
            sell_qv = tr.sell_quote_volume
            signed_vol = tr.signed_volume
            signed_qv = tr.signed_quote_volume
            trade_count = tr.trade_count
            buy_tc = tr.buy_trade_count
            sell_tc = tr.sell_trade_count
            first_trade_ts = tr.first_trade_ts_ms
            last_trade_ts = tr.last_trade_ts_ms

        cumulative_cvd += signed_vol
        vol_hist.append(volume)
        proxy_hist.append(bar_proxy)
        vpin_num_hist.append(abs(signed_vol))
        vpin_den_hist.append(volume)
        if len(vol_hist) < min(rolling_window, 20):
            flags.append("WARMUP")
            stats["warmup"] += 1
        vol_z = _z(volume, vol_hist)
        proxy_z = _z(bar_proxy, proxy_hist)
        last_spread_bps = snap.spread_bps if snap is not None else 0.0
        best_bid = snap.bid if snap is not None else 0.0
        best_ask = snap.ask if snap is not None else 0.0
        best_bid_qty = snap.bid_qty if snap is not None else 0.0
        best_ask_qty = snap.ask_qty if snap is not None else 0.0
        depth = best_bid_qty + best_ask_qty
        book_imbalance = snap.imbalance if snap is not None else 0.0
        mid = snap.mid if snap is not None else c
        microprice = snap.microprice if snap is not None else c
        tw_mid = tw["mid"] / total_ms if total_ms else mid
        tw_spread_bps = tw["spread_bps"] / total_ms if total_ms else last_spread_bps
        tw_imbalance = tw["imbalance"] / total_ms if total_ms else book_imbalance
        tw_microprice = tw["microprice"] / total_ms if total_ms else microprice
        liquidity_score = 0.0 if stale else max(0.0, min(1.0, (depth / (depth + 1.0)) * (1.0 / (1.0 + last_spread_bps / 10.0))))
        fill_prob = 0.0 if stale else max(0.0, min(1.0, liquidity_score * (1.0 - min(last_spread_bps / 100.0, 0.75))))
        impact_cost_bps = 10_000.0 if stale else max(0.0, last_spread_bps * 0.5 + (100.0 / max(depth, 1e-9)))
        vpin = (sum(vpin_num_hist) / sum(vpin_den_hist)) if sum(vpin_den_hist) > 0 else 0.0
        log_ret = math.log(c / prev_close) if prev_close and prev_close > 0 and c > 0 else 0.0
        last_book_ts = snap.ts_ms if snap is not None else None
        if (last_trade_ts is not None and last_trade_ts >= bar_end) or (last_book_ts is not None and last_book_ts >= bar_end):
            stats["lookahead_violations"] += 1

        row = {
            "symbol": symbol, "bar_interval": interval_name,
            "bar_start_ts_ms": bar, "bar_end_ts_ms": bar_end,
            "feature_available_ts_ms": bar_end, "timestamp_ms": bar,
            "open": o, "high": h, "low": l, "close": c,
            "volume": volume, "quote_volume": quote_volume,
            "buy_volume": buy_volume, "sell_volume": sell_volume,
            "buy_quote_volume": buy_qv, "sell_quote_volume": sell_qv,
            "trade_count": trade_count, "buy_trade_count": buy_tc, "sell_trade_count": sell_tc,
            "signed_volume": signed_vol, "signed_quote_volume": signed_qv,
            "cvd_delta": signed_vol, "cumulative_cvd": cumulative_cvd,
            "first_trade_ts_ms": first_trade_ts, "last_trade_ts_ms": last_trade_ts,
            "best_bid": best_bid, "best_ask": best_ask,
            "best_bid_qty": best_bid_qty, "best_ask_qty": best_ask_qty,
            "mid": mid, "spread": max(0.0, best_ask - best_bid), "last_spread_bps": last_spread_bps,
            "book_imbalance": max(-1.0, min(1.0, book_imbalance)), "microprice": microprice,
            "tw_mid": tw_mid, "tw_spread_bps": tw_spread_bps,
            "tw_book_imbalance": max(-1.0, min(1.0, tw_imbalance)), "tw_microprice": tw_microprice,
            "l1_order_flow_proxy": bar_proxy, "l1_order_flow_proxy_z": proxy_z,
            "log_ret": log_ret, "vol_z": vol_z,
            "liquidity_score": liquidity_score, "fill_prob": fill_prob,
            "impact_cost_bps": impact_cost_bps, "toxicity": min(1.0, abs(signed_vol) / max(volume, 1e-12)),
            "vpin": min(1.0, vpin), "latency_ms": 0,
            "last_book_event_ts_ms": last_book_ts,
            "book_stale": bool(stale), "data_quality_flags": _flags(flags),
            # Bot compatibility aliases. These are L1 proxy aliases, not true L2 OFI.
            "ofi_zscore": proxy_z, "ofi_norm": bar_proxy,
            "imbalance": max(-1.0, min(1.0, book_imbalance)), "spread_bps": last_spread_bps,
            "bid_price": best_bid, "ask_price": best_ask,
            "bid_qty": best_bid_qty, "ask_qty": best_ask_qty,
        }
        rows.append(row)
        prev_close = c
        bar = bar_end
    validate_rows(rows, interval_ms)
    return rows, stats


def validate_rows(rows: List[Dict[str, Any]], interval_ms: int) -> None:
    prev: Optional[int] = None
    for i, r in enumerate(rows):
        bs = int(r["bar_start_ts_ms"]); be = int(r["bar_end_ts_ms"])
        if prev is not None and bs <= prev:
            raise ValueError(f"bar_start_ts_ms not strictly increasing at row {i}")
        prev = bs
        checks = [
            (be == bs + interval_ms, "bad bar_end_ts_ms"),
            (int(r["feature_available_ts_ms"]) == be, "bad feature_available_ts_ms"),
            (r["timestamp_ms"] == bs, "bad timestamp_ms"),
            (r["open"] > 0 and r["close"] > 0, "non-positive open/close"),
            (r["high"] >= r["low"], "high < low"),
            (r["spread"] >= 0, "negative spread"),
            (r["best_ask"] >= r["best_bid"], "ask < bid"),
            (-1 <= r["book_imbalance"] <= 1, "imbalance out of range"),
            (abs(r["volume"] - (r["buy_volume"] + r["sell_volume"])) < 1e-8, "volume mismatch"),
            (abs(r["quote_volume"] - (r["buy_quote_volume"] + r["sell_quote_volume"])) < 1e-5, "quote volume mismatch"),
            (r["trade_count"] == r["buy_trade_count"] + r["sell_trade_count"], "trade count mismatch"),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(f"{msg} at row {i} bar_start={bs}")
        if r["last_trade_ts_ms"] is not None and int(r["last_trade_ts_ms"]) >= be:
            raise ValueError(f"last_trade_ts_ms lookahead at row {i}")
        if r["last_book_event_ts_ms"] is not None and int(r["last_book_event_ts_ms"]) >= be:
            raise ValueError(f"last_book_event_ts_ms lookahead at row {i}")


def write_parquet(rows: List[Dict[str, Any]], path: str) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to write parquet. Install requirements.txt on the VPS.") from exc
    os.makedirs(os.path.dirname(path), exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build L1-only BTCUSDT feature parquets from Binance BookTicker + AggTrades CSV")
    ap.add_argument("--bookticker", required=True)
    ap.add_argument("--aggtrades", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--stale-limit-ms", type=int, default=120_000)
    ap.add_argument("--rolling-window", type=int, default=50)
    args = ap.parse_args()
    for p in (args.bookticker, args.aggtrades):
        if not os.path.isabs(p):
            raise SystemExit(f"Raw input path must be absolute: {p}")
        if not os.path.exists(p):
            raise SystemExit(f"Input file not found: {p}")

    intervals = {"1m": 60_000, "5m": 300_000}
    trade_bars, trade_counts = read_trades(args.aggtrades, intervals)
    books, book_counts = read_books(args.bookticker)
    print(f"raw aggTrades rows read: {trade_counts['raw']}")
    print(f"raw BookTicker rows read: {book_counts['raw']}")
    print(f"invalid trade rows dropped: {trade_counts['invalid']}")
    print(f"invalid book rows dropped: {book_counts['invalid']}")
    print(f"crossed book rows dropped: {book_counts['crossed']}")

    outputs: Dict[str, str] = {}
    warmup_total = 0
    lookahead_total = 0
    for name, ms in intervals.items():
        rows, stats = build_interval(args.symbol, name, ms, trade_bars[name], books, args.stale_limit_ms, args.rolling_window)
        out = os.path.join(args.outdir, f"features_{name}.parquet")
        write_parquet(rows, out)
        outputs[name] = out
        warmup_total += stats["warmup"]
        lookahead_total += stats["lookahead_violations"]
        print(f"bars written for {name}: {len(rows)}")
        print(f"warmup rows for {name}: {stats['warmup']}")
        print(f"final parquet path for {name}: {out}")

    schema_sample = list(rows[0].keys()) if rows else []
    print("sample output schema:")
    print(json.dumps(schema_sample[:80], indent=2))
    print(f"total warmup rows: {warmup_total}")
    print(f"lookahead violations detected: {lookahead_total}")
    if lookahead_total:
        raise SystemExit("Lookahead validation failed")
    print("confirmation: no lookahead violation was detected")


if __name__ == "__main__":
    main()
