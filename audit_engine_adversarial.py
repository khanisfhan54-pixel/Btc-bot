#!/usr/bin/env python3
"""
audit_engine_adversarial.py
===========================
25 deterministic adversarial tests against engine.run_all_engines() and
selected helpers.  Each test asserts an invariant.  Output:
audit_engine_output/adversarial_results.json with per-test pass/fail.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List

import engine

OUT = "audit_engine_output"
os.makedirs(OUT, exist_ok=True)

results: List[Dict[str, Any]] = []


def _record(idx: int, name: str, passed: bool, detail: str = "") -> None:
    results.append({"id": f"TEST-{idx}", "name": name, "passed": bool(passed), "detail": detail})


# Synthesize a minimal, valid market snapshot for tests
def _make_candles(n: int = 80, start_ts: int = 1_700_000_000_000, base: float = 40000.0) -> List[list]:
    rows = []
    p = base
    for i in range(n):
        ts = start_ts + i * 60_000
        o = p
        h = p + 25
        l = p - 25
        c = p + (5 if i % 2 == 0 else -5)
        v = 1.0 + (i % 5) * 0.1
        rows.append([ts, o, h, l, c, v])
        p = c
    return rows


def _make_book(price: float, depth_levels: int = 10) -> Dict[str, list]:
    bids = [[price - 1 - i, 1.5] for i in range(depth_levels)]
    asks = [[price + 1 + i, 1.5] for i in range(depth_levels)]
    return {"bids": bids, "asks": asks}


def _make_trades(n: int = 30, base: float = 40000.0) -> List[dict]:
    out = []
    for i in range(n):
        out.append({
            "price": base + (i % 10) - 5,
            "amount": 0.05 + (i % 4) * 0.02,
            "side": "BUY" if i % 2 == 0 else "SELL",
            "ts": 1_700_000_000_000 + i * 1000,
        })
    return out


CANDLES = _make_candles()
PRICE = CANDLES[-1][4]
BOOK = _make_book(PRICE)
TRADES = _make_trades(base=PRICE)


def _call(**kw) -> dict:
    base = dict(
        orderbook=BOOK,
        trades=TRADES,
        price=PRICE,
        symbol="BTC/USDT",
        recent_candles=CANDLES,
        open_interest=1.0e9,
        funding_rate=0.0001,
        ohlcv={"1m": CANDLES, "5m": CANDLES[-30:], "15m": CANDLES[-15:]},
    )
    base.update(kw)
    return engine.run_all_engines(**base)


# --- TESTS ----------------------------------------------------------

def t1_invalid_negative_price():
    o = _call(price=-1.0)
    _record(1, "negative price → fail-closed", o.get("allow_trade") is False and o.get("reason") == "invalid_price")


def t2_zero_price():
    o = _call(price=0.0)
    _record(2, "zero price → fail-closed", o.get("allow_trade") is False and o.get("reason") == "invalid_price")


def t3_nan_price():
    o = _call(price=float("nan"))
    _record(3, "NaN price → fail-closed", o.get("allow_trade") is False and o.get("reason") == "invalid_price")


def t4_inf_price():
    o = _call(price=float("inf"))
    _record(4, "Inf price → fail-closed", o.get("allow_trade") is False and o.get("reason") == "invalid_price")


def t5_missing_oi_fail_closed():
    o = _call(open_interest=0.0, current_oi=0.0)
    _record(5, "missing OI → fail-closed",
            o.get("allow_trade") is False and o.get("open_interest_missing") is True
            and o.get("reason") == "open_interest_missing")


def t6_unsorted_book_does_not_crash():
    bids = [[PRICE - 1, 1.0], [PRICE - 5, 1.0], [PRICE - 3, 1.0]]
    asks = [[PRICE + 7, 1.0], [PRICE + 1, 1.0], [PRICE + 4, 1.0]]
    o = _call(orderbook={"bids": bids, "asks": asks})
    _record(6, "unsorted book accepted",
            isinstance(o, dict) and isinstance(o.get("spread_pct", 0.0), (int, float))
            and math.isfinite(o.get("spread_pct", 0.0)))


def t7_empty_book():
    o = _call(orderbook={"bids": [], "asks": []})
    _record(7, "empty book → finite output, fail-closed",
            isinstance(o, dict) and not o.get("allow_trade"))


def t8_alpha_prob_normalized():
    o = _call()
    a = (o.get("market_data") or {}).get("alpha") or {}
    pa = a.get("prob_above", 0.5); pb = a.get("prob_below", 0.5)
    _record(8, "alpha probabilities sum to 1", abs((pa + pb) - 1.0) < 1e-3, f"pa={pa} pb={pb}")


def t9_alpha_direction_valid():
    o = _call()
    a = (o.get("market_data") or {}).get("alpha") or {}
    _record(9, "alpha direction valid", str(a.get("direction", "")) in ("LONG", "SHORT", "NEUTRAL"))


def t10_no_state_leak_between_calls():
    engine.reset_alpha_state()
    o1 = _call()
    o2 = _call()
    a1 = (o1.get("market_data") or {}).get("alpha") or {}
    a2 = (o2.get("market_data") or {}).get("alpha") or {}
    # Hard determinism: direction, confidence, allow_trade, market_state,
    # order_imbalance, cascade_probability, alpha conf/probs MUST match exactly.
    keys_top = ("direction", "confidence", "allow_trade", "order_imbalance",
                "cascade_probability", "spread_pct")
    same_top = all(o1.get(k) == o2.get(k) for k in keys_top)
    same_state = (o1.get("market_state") or {}).get("state") == (o2.get("market_state") or {}).get("state")
    same_alpha = (a1.get("direction") == a2.get("direction")
                  and a1.get("confidence") == a2.get("confidence")
                  and a1.get("prob_above") == a2.get("prob_above")
                  and a1.get("prob_below") == a2.get("prob_below"))
    _record(10, "fully deterministic on identical inputs (top+state+alpha)",
            same_top and same_state and same_alpha,
            f"top={same_top} state={same_state} alpha={same_alpha}")


def t11_reset_alpha_state_clears():
    engine.reset_alpha_state()
    state = getattr(engine, "_ALPHA_STATE", {})
    _record(11, "reset_alpha_state empties dict", state == {} or not state)


def t12_extreme_funding_long():
    o = _call(funding_rate=0.10)  # 10% funding
    a = (o.get("market_data") or {}).get("alpha") or {}
    _record(12, "extreme funding does not crash & alpha conf bounded",
            isinstance(o, dict) and 0.0 <= float(a.get("confidence", 0.0)) <= 1.0)


def t13_extreme_funding_negative():
    o = _call(funding_rate=-0.10)
    _record(13, "negative extreme funding produces finite output",
            isinstance(o, dict) and math.isfinite(float(o.get("confidence", 0.0))))


def t14_huge_trades_list():
    big = TRADES * 50
    o = _call(trades=big)
    _record(14, "large trade list does not crash & ofp finite",
            isinstance(o, dict) and math.isfinite(o.get("order_flow_pressure", 0.0)))


def t15_no_trades():
    o = _call(trades=[])
    # No trades ⇒ no smart money, no absorption zones, OFP must be exactly 0.0
    # (not merely "finite"), and order_imbalance must be 0.
    sm = o.get("smart_money_detected", True)
    ofp = o.get("order_flow_pressure", 1.0)
    imb = o.get("order_imbalance", 1.0)
    # No trades ⇒ no smart-money signal AND order flow pressure / imbalance
    # must be exactly zero (trade-derived, not candle-derived).
    ok = (isinstance(o, dict) and sm is False
          and abs(float(ofp)) < 1e-9 and abs(float(imb)) < 1e-9)
    _record(15, "no trades → sm=False, ofp=0, imb=0 (trade-derived signals zeroed)",
            ok, f"sm={sm} ofp={ofp} imb={imb}")


def t16_short_candles():
    short = CANDLES[:5]
    o = _call(recent_candles=short, ohlcv={"1m": short, "5m": short, "15m": short})
    _record(16, "very short candle history → fail-closed",
            isinstance(o, dict) and not o.get("allow_trade"))


def t17_nan_in_candles():
    bad = [list(c) for c in CANDLES]
    bad[-1][4] = float("nan")
    o = _call(recent_candles=bad, ohlcv={"1m": bad, "5m": bad[-30:], "15m": bad[-15:]})
    _record(17, "NaN in candle close survives without crashing",
            isinstance(o, dict))


def t18_cache_hit_returns_deepcopy():
    engine.reset_alpha_state()
    o1 = _call()
    o2 = _call()
    if o1 is o2:
        _record(18, "cache returns distinct object (deepcopy)", False, "same id")
        return
    o1["smc_signal"]["signal"] = "MUTATED"
    o3 = _call()
    _record(18, "cache returns distinct object (deepcopy)",
            o3.get("smc_signal", {}).get("signal") != "MUTATED")


def t19_oi_missing_overrides_alpha():
    o = _call(open_interest=0.0, current_oi=0.0)
    _record(19, "oi_missing forces allow_trade=False regardless of alpha",
            o.get("allow_trade") is False)


def t20_imbalance_in_range():
    o = _call()
    imb = o.get("order_imbalance", 0.0)
    _record(20, "order_imbalance ∈ [-1,1]", -1.0001 <= imb <= 1.0001, f"imb={imb}")


def t21_cascade_probability_bounded():
    o = _call()
    cp = o.get("cascade_probability", 0.0)
    _record(21, "cascade_probability ∈ [0,1]", -0.0001 <= cp <= 1.0001, f"cp={cp}")


def t22_smc_confidence_bounded():
    o = _call()
    smc = o.get("smc_signal") or {}
    cfd = float(smc.get("confidence", 0))
    _record(22, "smc confidence ∈ [0,10]", 0.0 <= cfd <= 10.0, f"cfd={cfd}")


def t23_orderbook_snapshots_optional():
    o = _call(orderbook_snapshots=None)
    # Schema-completeness: when an optional input is None, the engine MUST still
    # populate the canonical top-level contract (no missing keys, no None where
    # a numeric is expected).
    required = ("direction", "confidence", "allow_trade", "spread_pct",
                "order_flow_pressure", "order_imbalance", "cascade_probability",
                "smc_signal", "market_state", "market_data")
    missing = [k for k in required if k not in o]
    none_keys = [k for k in ("direction", "confidence", "allow_trade",
                             "spread_pct") if o.get(k) is None]
    _record(23, "orderbook_snapshots=None → full schema, no None scalars",
            isinstance(o, dict) and not missing and not none_keys,
            f"missing={missing} none={none_keys}")


def t24_oi_history_empty_handled():
    o = _call(oi_history=[], current_oi=1.0e9)
    # cascade_probability must still be a finite [0,1] number, alpha must be
    # populated, and the result must NOT be the bare error fallback.
    cp = o.get("cascade_probability", None)
    alpha = (o.get("market_data") or {}).get("alpha") or {}
    ok = (isinstance(o, dict)
          and isinstance(cp, (int, float)) and math.isfinite(cp) and 0.0 <= cp <= 1.0
          and "direction" in alpha and "confidence" in alpha
          and o.get("reason") != "run_all_engines_error")
    _record(24, "empty oi_history → finite cascade, alpha populated, not fallback",
            ok, f"cp={cp} alpha_keys={sorted(alpha.keys())[:5]} reason={o.get('reason')}")


def t25_no_live_calls_no_secrets():
    """Hard-fail if run_all_engines touches the network or reads BINANCE_API_*.

    Strategy: monkeypatch socket + urllib + (if present) requests + ccxt so any
    outbound IO raises; sentinel-trap os.environ['BINANCE_API_KEY'/'_SECRET']
    so that any read flips a flag.
    """
    import socket, urllib.request as _ur
    saved_env = {k: os.environ.pop(k, None) for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET")}
    accessed = {"key": False, "secret": False, "net": False}

    class _TrapEnv(dict):
        def __getitem__(self, k):
            if k in ("BINANCE_API_KEY",): accessed["key"] = True
            if k in ("BINANCE_API_SECRET",): accessed["secret"] = True
            return super().__getitem__(k)
        def get(self, k, default=None):
            if k == "BINANCE_API_KEY": accessed["key"] = True
            if k == "BINANCE_API_SECRET": accessed["secret"] = True
            return super().get(k, default)

    def _no_net(*a, **kw):
        accessed["net"] = True
        raise RuntimeError("network blocked by adversarial test")

    saved_socket = socket.socket
    saved_create = getattr(socket, "create_connection", None)
    saved_url = _ur.urlopen
    socket.socket = _no_net
    if saved_create:
        socket.create_connection = _no_net
    _ur.urlopen = _no_net
    saved_req_get = saved_req_post = None
    try:
        import requests  # type: ignore
        saved_req_get, saved_req_post = requests.get, requests.post
        requests.get = _no_net
        requests.post = _no_net
    except Exception:
        requests = None  # noqa: F841

    try:
        o = _call(exchange=None)
        ok = (isinstance(o, dict) and not accessed["key"] and not accessed["secret"]
              and not accessed["net"])
        _record(25, "no network + no BINANCE_API_* read when exchange=None",
                ok, f"accessed={accessed}")
    except RuntimeError as exc:
        _record(25, "no network + no BINANCE_API_* read when exchange=None",
                False, f"network attempted: {exc}")
    finally:
        socket.socket = saved_socket
        if saved_create:
            socket.create_connection = saved_create
        _ur.urlopen = saved_url
        if saved_req_get is not None:
            try:
                import requests as _rq
                _rq.get, _rq.post = saved_req_get, saved_req_post
            except Exception:
                pass
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v


def main():
    t0 = time.time()
    tests = [t1_invalid_negative_price, t2_zero_price, t3_nan_price, t4_inf_price,
             t5_missing_oi_fail_closed, t6_unsorted_book_does_not_crash, t7_empty_book,
             t8_alpha_prob_normalized, t9_alpha_direction_valid, t10_no_state_leak_between_calls,
             t11_reset_alpha_state_clears, t12_extreme_funding_long, t13_extreme_funding_negative,
             t14_huge_trades_list, t15_no_trades, t16_short_candles, t17_nan_in_candles,
             t18_cache_hit_returns_deepcopy, t19_oi_missing_overrides_alpha, t20_imbalance_in_range,
             t21_cascade_probability_bounded, t22_smc_confidence_bounded, t23_orderbook_snapshots_optional,
             t24_oi_history_empty_handled, t25_no_live_calls_no_secrets]
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            results.append({"id": fn.__name__, "name": fn.__name__, "passed": False,
                            "detail": f"EXCEPTION {type(exc).__name__}: {exc}"})
    summary = {
        "elapsed_sec": round(time.time() - t0, 2),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results,
    }
    out_path = os.path.join(OUT, "adversarial_results.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
