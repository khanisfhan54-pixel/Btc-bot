# engine.py
#!/usr/bin/env python3
# =============================================================================
# engines.py — Market Data & Utility Layer
#
# ARCHITECTURAL ROLE (modular architecture):
#   ✔  Data provider       – order-book, trades, candle processing
#   ✔  Utility provider    – _safe_float, _clamp, _mean, spread helpers, etc.
#   ✔  Feature support     – get_market_data, analyze_volume_intelligence,
#                            MarketStateDetector, liquidity/OI/spoof engines
#
#   ✘  Signal generator    – final signal decisions live in signal_engine.py
#   ✘  Execution engine    – SL/TP, position sizing live in execution_logic.py
#
# Legacy signal/execution helpers are kept for backward-compatibility but are
# marked  # DEPRECATED (moved to signal_engine / execution_logic)
# =============================================================================
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import requests
except Exception:
    requests = None  # type: ignore

try:
    import websocket
except Exception:
    websocket = None  # type: ignore

try:
    from meta_filter import MetaFilter as _MetaFilter

    _learning_engine = globals().get("LEARNING_ENGINE")

    if _learning_engine is None:
        try:
            _learning_engine = self.learning_engine
        except Exception as e:
            logger.warning(
                "Failed to access learning_engine. Defaulting to None. error=%s",
                str(e),
            )
            _learning_engine = None

    META_FILTER = _MetaFilter(learning_engine=_learning_engine)
except Exception as _meta_import_exc:
    META_FILTER = None


def evaluate_meta_filter(
    features: Dict[str, Any],
    signal: Any,
    decision: Optional[Dict[str, Any]] = None,
    router_decision: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
    trades: Optional[list] = None,
) -> Dict[str, Any]:
    _fallback: Dict[str, Any] = {
        "allow_trade": True,
        "risk_scale": 1.0,
        "reason": "meta_filter_unavailable",
        "meta_state": {},
    }
    if META_FILTER is None:
        return _fallback
    try:
        return META_FILTER.evaluate(
            features=features,
            signal=signal,
            decision=decision,
            router_decision=router_decision,
            snapshot=snapshot,
            trades=trades,
        )
    except Exception as exc:
        logger.warning("[META_FILTER] evaluate failed (fallback allow): %s", exc)
        return _fallback


def apply_meta_to_decision(
    decision: Dict[str, Any],
    meta_result: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(decision, dict):
        decision = {}
    if not isinstance(meta_result, dict):
        meta_result = {"allow_trade": True, "risk_scale": 1.0, "reason": "invalid_meta"}

    if not meta_result.get("allow_trade", True):
        decision["execute"] = False
        decision["reason"] = meta_result.get("reason", "meta_blocked")
        decision["risk_scale"] = 0.0
        decision["meta_result"] = meta_result
        return decision

    risk_scale = float(meta_result.get("risk_scale", 1.0))
    if "position_size" in decision and decision["position_size"] is not None:
        try:
            decision["position_size"] = max(0.0, float(decision["position_size"]) * risk_scale)
        except Exception:
            pass

    decision["risk_scale"] = risk_scale
    decision["meta_result"] = meta_result
    return decision


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _enforce_entry_fee_metadata(fees, fee_type, trade_id=None):
    fees_val = _safe_float(fees, 0.0)

    fee_type_val = str(fee_type).lower().strip() if fee_type is not None else None

    if fee_type_val not in ("quote", "pct"):
        logger.warning(
            "Invalid fee_type detected upstream. Defaulting to pct. trade_id=%s",
            trade_id if trade_id is not None else "unknown",
        )
        fee_type_val = "pct"

    return fees_val, fee_type_val


def _clamp(value: Any, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return low


def _mean(values: List[float], default: float = 0.0) -> float:
    try:
        vals = [float(v) for v in values if v is not None]
        return statistics.mean(vals) if vals else default
    except Exception:
        return default


def _sigmoid(x: float) -> float:
    try:
        x = float(x)
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        z = math.exp(x)
        return z / (1.0 + z)
    except Exception:
        return 0.5


def compute_sma(values: Any, period: int = 14) -> float:
    try:
        vals = [float(v) for v in (values or []) if v is not None]
        if not vals:
            return 0.0
        if period <= 0:
            period = len(vals)
        window = vals[-period:]
        return sum(window) / max(len(window), 1)
    except Exception:
        return 0.0


def compute_sma_signal(values: Any, fast: int = 10, slow: int = 30) -> Dict[str, Any]:
    try:
        vals = [float(v) for v in (values or []) if v is not None]
        if len(vals) < 2:
            return {"signal": "NEUTRAL", "sma_fast": 0.0, "sma_slow": 0.0, "bias": 0.0}
        sma_fast = compute_sma(vals, fast)
        sma_slow = compute_sma(vals, slow)
        bias = 0.0
        signal = "NEUTRAL"
        if sma_fast > sma_slow:
            bias = 1.0
            signal = "BUY"
        elif sma_fast < sma_slow:
            bias = -1.0
            signal = "SELL"
        return {
            "signal": signal,
            "sma_fast": round(float(sma_fast), 6),
            "sma_slow": round(float(sma_slow), 6),
            "bias": bias,
        }
    except Exception:
        return {"signal": "NEUTRAL", "sma_fast": 0.0, "sma_slow": 0.0, "bias": 0.0}


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    if period <= 1:
        return float(values[-1])
    k = 2.0 / (period + 1.0)
    ema = float(values[0])
    for v in values[1:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


def _atr(candles: List[list], period: int = 14) -> float:
    try:
        if len(candles) < 2:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            prev_close = _safe_float(candles[i - 1][4])
            high = _safe_float(candles[i][2])
            low = _safe_float(candles[i][3])
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        return _mean(trs[-period:], 0.0)
    except Exception:
        return 0.0


def _body_ratio(candle: list) -> float:
    try:
        o = _safe_float(candle[1])
        h = _safe_float(candle[2])
        l = _safe_float(candle[3])
        c = _safe_float(candle[4])
        rng = max(h - l, 1e-9)
        return _clamp(abs(c - o) / rng, 0.0, 1.0)
    except Exception:
        return 0.0


def _wick_ratio(candle: list) -> float:
    return max(0.0, 1.0 - _body_ratio(candle))


def _trade_side(t: dict) -> str:
    side = t.get("side") or t.get("S") or t.get("takerSide") or ""
    return str(side).upper()


def _trade_price(t: dict, fallback: float = 0.0) -> float:
    return _safe_float(t.get("price", t.get("p", fallback)))


def _trade_amount(t: dict) -> float:
    return _safe_float(t.get("amount", t.get("q", 0.0)))


def _trade_usd(t: dict, fallback_price: float = 0.0) -> float:
    return _trade_price(t, fallback_price) * _trade_amount(t)


def _best_bid_ask(orderbook: dict) -> Tuple[float, float]:
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bid = _safe_float(bids[0][0]) if bids else 0.0
    ask = _safe_float(asks[0][0]) if asks else 0.0
    return bid, ask


def _book_volumes(orderbook: dict, depth: int = 20) -> Tuple[float, float]:
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    return sum(_safe_float(b[1]) for b in bids[:depth]), sum(_safe_float(a[1]) for a in asks[:depth])


def _ohlcv_to_closes(recent_candles: Any) -> List[float]:
    if recent_candles is None:
        return []
    try:
        if hasattr(recent_candles, "__getitem__") and "close" in getattr(recent_candles, "columns", []):
            return [float(x) for x in recent_candles["close"].tolist()]
    except Exception:
        pass
    try:
        return [float(r[4]) for r in recent_candles if len(r) >= 5]
    except Exception:
        return []


def _aggregate_ohlcv(rows: list, factor: int) -> list:
    try:
        rows = [r for r in (rows or []) if isinstance(r, (list, tuple)) and len(r) >= 6]
        if not rows or factor <= 1:
            return list(rows)
        out, chunk = [], []
        for row in rows:
            chunk.append(row)
            if len(chunk) == factor:
                ts = chunk[0][0]
                op = _safe_float(chunk[0][1])
                hi = max(_safe_float(r[2]) for r in chunk)
                lo = min(_safe_float(r[3]) for r in chunk)
                cl = _safe_float(chunk[-1][4])
                vol = sum(_safe_float(r[5]) for r in chunk)
                out.append([ts, op, hi, lo, cl, vol])
                chunk = []
        if chunk:
            ts = chunk[0][0]
            op = _safe_float(chunk[0][1])
            hi = max(_safe_float(r[2]) for r in chunk)
            lo = min(_safe_float(r[3]) for r in chunk)
            cl = _safe_float(chunk[-1][4])
            vol = sum(_safe_float(r[5]) for r in chunk)
            out.append([ts, op, hi, lo, cl, vol])
        return out
    except Exception:
        return []


def _round_levels(price: float, step: int = 250) -> List[float]:
    if price <= 0:
        return []
    base = int(price // step) * step
    return [float(base - step), float(base), float(base + step), float(base + 2 * step)]


def _spread_pct(orderbook: dict, price: float) -> float:
    try:
        bid, ask = _best_bid_ask(orderbook)
        if bid <= 0 or ask <= 0:
            return 0.0
        mid = (bid + ask) / 2.0
        return max(0.0, (ask - bid) / max(mid, 1e-9))
    except Exception:
        return 0.0


def _volume_side(trades: List[dict]) -> Tuple[float, float]:
    buy = 0.0
    sell = 0.0
    for t in trades or []:
        usd = _trade_usd(t)
        if _trade_side(t) == "BUY":
            buy += usd
        elif _trade_side(t) == "SELL":
            sell += usd
    return buy, sell


def heatmap_value_from_cluster(
    liquidation_cluster: float,
    open_interest: float,
    funding_rate: float = 0.0,
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    try:
        raw = 0.0
        if open_interest > 0:
            raw += min(liquidation_cluster / open_interest, 1.0) * 50.0
        raw += min(abs(_safe_float(funding_rate)) * 2000.0, 30.0)
        raw += min(max(spread_pct, 0.0) * 400.0, 20.0)
        heat_score = int(min(max(raw, 0.0), 100.0))
        color = "green" if heat_score < 35 else "yellow" if heat_score < 65 else "red"
        level = "low" if heat_score < 35 else "medium" if heat_score < 65 else "high"
        return {"heat_score": heat_score, "color": color, "level": level}
    except Exception:
        return {"heat_score": 0, "color": "green", "level": "low"}


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    timestamp: float = field(default_factory=lambda: time.time())
    orderbook: Dict[str, Any] = field(default_factory=dict)
    trades: List[dict] = field(default_factory=list)
    candles: Dict[str, List[list]] = field(default_factory=dict)
    open_interest: float = 0.0
    funding_rate: float = 0.0
    strategy_bias: str = "NEUTRAL"
    whale_walls: Dict[str, List[float]] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SniperSignal:
    bias: str
    entry_price: Optional[float]
    entry_zone: Optional[List[float]]
    stop_loss: Optional[float]
    take_profit: Optional[List[float]]
    rr_ratio: Optional[float]
    confidence_score: int
    setup_type: str
    state: str
    reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "bias": self.bias,
            "entry_price": self.entry_price,
            "entry_zone": self.entry_zone,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "rr_ratio": self.rr_ratio,
            "confidence_score": self.confidence_score,
            "setup_type": self.setup_type,
            "state": self.state,
            "reasons": self.reasons,
            "metadata": self.metadata,
        }


def compute_volume_spike_engine(
    ohlcv: list,
    timeframe: str = "unknown",
    spike_multiple: float = 3.0,
    explosion_multiple: float = 4.5,
    lookback: int = 20,
) -> Dict[str, Any]:
    try:
        rows = [r for r in (ohlcv or []) if isinstance(r, (list, tuple)) and len(r) >= 6]
        if len(rows) < 2:
            return {
                "timeframe": timeframe,
                "spike_detected": False,
                "explosion_detected": False,
                "strength_score": 0.0,
                "volume_ratio": 0.0,
                "current_volume": 0.0,
                "baseline_volume": 0.0,
                "price_move_pct": 0.0,
                "breakout": False,
                "direction_bias": 0.0,
                "price_move_strength": 0.0,
            }
        vols = [_safe_float(r[5]) for r in rows]
        closes = [_safe_float(r[4]) for r in rows]
        current_volume = vols[-1]
        prev_slice = vols[max(0, len(vols) - lookback - 1):-1] or vols[:-1]
        baseline = _mean(prev_slice, current_volume)
        if baseline <= 0:
            baseline = max(current_volume, 1e-9)
        volume_ratio = current_volume / baseline if baseline > 0 else 0.0
        prev_close = closes[-2]
        last_close = closes[-1]
        price_move_pct = ((last_close - prev_close) / prev_close) if prev_close else 0.0
        prev_window = closes[max(0, len(closes) - lookback - 1):-1] or closes[:-1]
        prev_high = max(prev_window) if prev_window else last_close
        prev_low = min(prev_window) if prev_window else last_close
        breakout = last_close > prev_high or last_close < prev_low
        spike_detected = volume_ratio >= spike_multiple
        price_move_strength = min(abs(price_move_pct) * 250.0, 1.0)
        explosion_detected = spike_detected and breakout and abs(price_move_pct) >= 0.001
        strength_score = _clamp(
            (volume_ratio / max(explosion_multiple, 1e-9)) * 0.65 + price_move_strength * 0.35,
            0.0,
            1.0,
        )
        if spike_detected and breakout:
            strength_score = max(strength_score, 0.60)
        if explosion_detected:
            strength_score = max(strength_score, 0.80)
        direction_bias = 1.0 if price_move_pct > 0 else -1.0 if price_move_pct < 0 else 0.0
        return {
            "timeframe": timeframe,
            "spike_detected": bool(spike_detected),
            "explosion_detected": bool(explosion_detected),
            "strength_score": round(float(strength_score), 4),
            "volume_ratio": round(float(volume_ratio), 4),
            "current_volume": round(float(current_volume), 4),
            "baseline_volume": round(float(baseline), 4),
            "price_move_pct": round(float(price_move_pct), 6),
            "breakout": bool(breakout),
            "direction_bias": round(float(direction_bias), 4),
            "price_move_strength": round(float(price_move_strength), 4),
        }
    except Exception:
        return {
            "timeframe": timeframe,
            "spike_detected": False,
            "explosion_detected": False,
            "strength_score": 0.0,
            "volume_ratio": 0.0,
            "current_volume": 0.0,
            "baseline_volume": 0.0,
            "price_move_pct": 0.0,
            "breakout": False,
            "direction_bias": 0.0,
            "price_move_strength": 0.0,
        }


def analyze_volume_intelligence(
    exchange=None,
    symbol: str = "BTC/USDT",
    primary_ohlcv=None,
    trades=None,
    use_exchange: bool = True,
) -> Dict[str, Any]:
    try:
        frames: Dict[str, list] = {}
        if exchange is not None and use_exchange:
            for tf in ("1m", "5m", "15m"):
                try:
                    data = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=60)
                    if data:
                        frames[tf] = data
                except Exception:
                    continue
        if primary_ohlcv:
            primary = list(primary_ohlcv[-120:]) if len(primary_ohlcv) > 120 else list(primary_ohlcv)
            frames["primary"] = primary
            if not exchange or not use_exchange:
                frames["x5"] = _aggregate_ohlcv(primary, 5)
                frames["x15"] = _aggregate_ohlcv(primary, 15)
        if not frames:
            return {
                "volume_spike": False,
                "volume_explosion": False,
                "volume_strength": 0.0,
                "mtf_confirmation": False,
                "timeframe": "unknown",
                "timeframe_breakdown": {},
                "spike_count": 0,
                "explosion_count": 0,
                "direction_bias": 0.0,
                "trade_delta": 0.0,
                "volume_ratio": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            }

        breakdown: Dict[str, Any] = {}
        spike_count = 0
        explosion_count = 0
        strengths: List[float] = []
        direction_scores: List[float] = []
        ratios: List[float] = []

        for label, data in frames.items():
            metrics = compute_volume_spike_engine(data, timeframe=label)
            breakdown[label] = metrics
            if metrics["spike_detected"]:
                spike_count += 1
            if metrics["explosion_detected"]:
                explosion_count += 1
            strengths.append(_safe_float(metrics["strength_score"]))
            direction_scores.append(_safe_float(metrics["direction_bias"]) * _safe_float(metrics["strength_score"]))
            ratios.append(_safe_float(metrics["volume_ratio"]))

        buy_notional = 0.0
        sell_notional = 0.0
        for t in (trades or [])[-100:]:
            usd = _trade_usd(t, 0.0)
            side = _trade_side(t)
            if side == "BUY":
                buy_notional += usd
            elif side == "SELL":
                sell_notional += usd

        total_notional = buy_notional + sell_notional
        trade_delta = (buy_notional - sell_notional) / (total_notional + 1e-9) if total_notional > 0 else 0.0
        if abs(trade_delta) > 0.2:
            strengths.append(min(1.0, abs(trade_delta)))
            direction_scores.append(trade_delta)

        mtf_confirmation = spike_count >= 2 or explosion_count >= 1
        volume_strength = max(strengths) if strengths else 0.0
        if mtf_confirmation:
            volume_strength = min(1.0, volume_strength + 0.08)
        if spike_count >= 2:
            volume_strength = min(1.0, volume_strength + 0.05)
        if explosion_count >= 1:
            volume_strength = max(volume_strength, 0.75)
        if abs(trade_delta) > 0.35:
            volume_strength = max(volume_strength, min(1.0, abs(trade_delta)))
        direction_bias = sum(direction_scores) / (len(direction_scores) or 1)
        direction_bias = _clamp(direction_bias, -1.0, 1.0)
        if abs(trade_delta) > abs(direction_bias):
            direction_bias = 0.7 * direction_bias + 0.3 * trade_delta
        direction_bias = _clamp(direction_bias, -1.0, 1.0)

        return {
            "volume_spike": bool(spike_count >= 1),
            "volume_explosion": bool(explosion_count >= 1),
            "volume_strength": round(float(_clamp(volume_strength, 0.0, 1.0)), 4),
            "mtf_confirmation": bool(mtf_confirmation),
            "timeframe": "multi",
            "timeframe_breakdown": breakdown,
            "spike_count": int(spike_count),
            "explosion_count": int(explosion_count),
            "direction_bias": round(float(direction_bias), 4),
            "trade_delta": round(float(trade_delta), 4),
            "volume_ratio": round(float(max(ratios) if ratios else 0.0), 4),
            "buy_notional": round(float(buy_notional), 4),
            "sell_notional": round(float(sell_notional), 4),
        }
    except Exception:
        return {
            "volume_spike": False,
            "volume_explosion": False,
            "volume_strength": 0.0,
            "mtf_confirmation": False,
            "timeframe": "unknown",
            "timeframe_breakdown": {},
            "spike_count": 0,
            "explosion_count": 0,
            "direction_bias": 0.0,
            "trade_delta": 0.0,
            "volume_ratio": 0.0,
            "buy_notional": 0.0,
            "sell_notional": 0.0,
        }


def order_flow_pressure_engine(orderbook: dict, trades: List[dict], price: float) -> Dict[str, Any]:
    try:
        bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
        buy_usd, sell_usd = _volume_side(trades)
        total_flow = buy_usd + sell_usd + 1e-9
        trade_delta = (buy_usd - sell_usd) / total_flow
        book_delta = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        pressure_score = _clamp(trade_delta * 0.65 + book_delta * 0.35, -1.0, 1.0)
        return {
            "pressure_score": round(float(pressure_score), 6),
            "buy_pressure": round(float(max(pressure_score, 0.0)), 6),
            "sell_pressure": round(float(max(-pressure_score, 0.0)), 6),
            "aggressive_buy_usd": round(float(buy_usd), 4),
            "aggressive_sell_usd": round(float(sell_usd), 4),
            "bid_consumption": round(float(max(0.0, sell_usd - buy_usd)), 4),
            "ask_consumption": round(float(max(0.0, buy_usd - sell_usd)), 4),
            "trade_delta": round(float(trade_delta), 6),
            "book_delta": round(float(book_delta), 6),
        }
    except Exception as exc:
        logger.error("order_flow_pressure_engine error: %s", exc)
        return {
            "pressure_score": 0.0,
            "buy_pressure": 0.0,
            "sell_pressure": 0.0,
            "aggressive_buy_usd": 0.0,
            "aggressive_sell_usd": 0.0,
            "bid_consumption": 0.0,
            "ask_consumption": 0.0,
            "trade_delta": 0.0,
            "book_delta": 0.0,
        }


def order_imbalance_engine(orderbook: dict) -> Dict[str, Any]:
    try:
        bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
        total = bid_vol + ask_vol
        imbalance = 0.0 if total == 0 else (bid_vol - ask_vol) / total
        skew = bid_vol / (ask_vol + 1e-9)
        return {
            "imbalance": round(float(imbalance), 6),
            "bid_vol": round(float(bid_vol), 4),
            "ask_vol": round(float(ask_vol), 4),
            "skew": round(float(skew), 6),
        }
    except Exception as exc:
        logger.error("order_imbalance_engine error: %s", exc)
        return {"imbalance": 0.0, "bid_vol": 0.0, "ask_vol": 0.0, "skew": 0.0}


def detect_liquidity_sweep(
    trades: List[dict],
    price: float,
    threshold_usd: float = 50_000,
    lookback: int = 40,
) -> Dict[str, Any]:
    try:
        recent = (trades or [])[-lookback:]
        if not recent:
            return {
                "sweep": False,
                "side": "unknown",
                "size_usd": 0.0,
                "trade": None,
                "reason": "no_trades",
                "buy_usd": 0.0,
                "sell_usd": 0.0,
            }
        large = []
        same_side = {"BUY": 0.0, "SELL": 0.0}
        for t in recent:
            side = _trade_side(t)
            usd = _trade_usd(t, price)
            if side in same_side:
                same_side[side] += usd
            if usd >= threshold_usd * 0.5:
                large.append((usd, side, t))
        if large:
            usd, side, trade = max(large, key=lambda x: x[0])
            return {
                "sweep": bool(usd >= threshold_usd),
                "side": side or "unknown",
                "size_usd": round(float(usd), 6),
                "trade": trade,
                "reason": "single_large_taker" if usd >= threshold_usd else "clustered_large_takers",
                "buy_usd": round(float(same_side["BUY"]), 6),
                "sell_usd": round(float(same_side["SELL"]), 6),
            }
        total_large = sum(_trade_usd(t, price) for t in recent if _trade_usd(t, price) >= threshold_usd * 0.2)
        if total_large >= threshold_usd:
            dominant = "BUY" if same_side["BUY"] > same_side["SELL"] else "SELL"
            return {
                "sweep": True,
                "side": dominant,
                "size_usd": round(float(total_large), 6),
                "trade": None,
                "reason": "cluster_sweep",
                "buy_usd": round(float(same_side["BUY"]), 6),
                "sell_usd": round(float(same_side["SELL"]), 6),
            }
        return {
            "sweep": False,
            "side": "BUY" if same_side["BUY"] >= same_side["SELL"] else "SELL",
            "size_usd": round(float(sum(same_side.values())), 6),
            "trade": None,
            "reason": "below_threshold",
            "buy_usd": round(float(same_side["BUY"]), 6),
            "sell_usd": round(float(same_side["SELL"]), 6),
        }
    except Exception as exc:
        logger.error("detect_liquidity_sweep error: %s", exc)
        return {
            "sweep": False,
            "side": "unknown",
            "size_usd": 0.0,
            "trade": None,
            "reason": "error",
            "buy_usd": 0.0,
            "sell_usd": 0.0,
        }


def track_liquidations(trades: List[dict], price: float, lookback: int = 100) -> Dict[str, Any]:
    try:
        recent = trades[-lookback:] if trades else []
        buy_usd = 0.0
        sell_usd = 0.0
        spikes = []
        for t in recent:
            usd = _trade_usd(t, price)
            side = _trade_side(t)
            if t.get("liquidation"):
                usd *= 1.15
            if side == "BUY":
                buy_usd += usd
            elif side == "SELL":
                sell_usd += usd
            if usd >= 100_000:
                spikes.append({"side": side, "usd": round(float(usd), 4), "trade": t})
        return {
            "buy_liq": round(float(buy_usd), 4),
            "sell_liq": round(float(sell_usd), 4),
            "total_liq": round(float(buy_usd + sell_usd), 4),
            "spikes": spikes,
        }
    except Exception as exc:
        logger.error("track_liquidations error: %s", exc)
        return {"buy_liq": 0.0, "sell_liq": 0.0, "total_liq": 0.0, "spikes": []}


def liquidation_stream_processor(liquidation_events: List[dict]) -> Dict[str, Any]:
    try:
        long_liq = 0.0
        short_liq = 0.0
        events = []
        for e in liquidation_events or []:
            side = str(e.get("side", "")).upper()
            usd = _safe_float(e.get("usd", e.get("size_usd", 0.0)))
            if side == "BUY":
                short_liq += usd
            elif side == "SELL":
                long_liq += usd
            events.append(e)
        total = long_liq + short_liq
        dominant = "neutral"
        if short_liq > long_liq * 1.1:
            dominant = "short_liquidations"
        elif long_liq > short_liq * 1.1:
            dominant = "long_liquidations"
        pressure = 0.0 if total == 0 else (short_liq - long_liq) / total
        return {
            "long_liquidations": round(float(long_liq), 4),
            "short_liquidations": round(float(short_liq), 4),
            "total_liquidations": round(float(total), 4),
            "dominant_side": dominant,
            "pressure": round(float(pressure), 6),
            "events": events,
        }
    except Exception as exc:
        logger.error("liquidation_stream_processor error: %s", exc)
        return {
            "long_liquidations": 0.0,
            "short_liquidations": 0.0,
            "total_liquidations": 0.0,
            "dominant_side": "neutral",
            "pressure": 0.0,
            "events": [],
        }


def liquidation_heatmap_engine(
    liquidation_cluster: float,
    open_interest: float,
    funding_rate: float = 0.0,
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    try:
        return heatmap_value_from_cluster(
            liquidation_cluster,
            open_interest,
            funding_rate=funding_rate,
            spread_pct=spread_pct,
        )
    except Exception as exc:
        logger.error("liquidation_heatmap_engine error: %s", exc)
        return {"heat_score": 0, "color": "green", "level": "low"}


def get_cascade_probability(
    open_interest: float,
    oi_history: List[float],
    liquidation_cluster: float,
    bid: float,
    ask: float,
    buy_volume: float,
    sell_volume: float,
    funding_rate: float,
    whale_flag: bool,
) -> float:
    try:
        score = 0.0
        if len(oi_history) >= 2 and oi_history[-2] > 0:
            oi_change = (open_interest - oi_history[-2]) / oi_history[-2]
            score += min(max(oi_change * 5.0, -0.25), 0.25)
        if open_interest > 0:
            cluster_ratio = min(liquidation_cluster / open_interest, 1.0)
            score += cluster_ratio * 0.25
        mid = (bid + ask) / 2.0 if (bid + ask) > 0 else 1.0
        spread_pct = (ask - bid) / mid if mid > 0 else 0.0
        score += min(spread_pct * 20.0, 0.15)
        total_vol = buy_volume + sell_volume
        if total_vol > 0:
            score += (abs(buy_volume - sell_volume) / total_vol) * 0.20
        score += min(abs(_safe_float(funding_rate)) * 500.0, 0.15)
        if whale_flag:
            score += 0.10
        return max(0.0, min(float(score), 1.0))
    except Exception as exc:
        logger.error("get_cascade_probability error: %s", exc)
        return 0.0


def funding_trap_detector(funding_rate: float, price: float, cascade_prob: float) -> Dict[str, Any]:
    try:
        fr = _safe_float(funding_rate)
        trap = abs(fr) > 0.02 and _safe_float(cascade_prob) > 0.4
        severity = min(1.0, abs(fr) * 50.0 * _safe_float(cascade_prob)) if trap else 0.0
        return {
            "trap": bool(trap),
            "severity": round(float(severity), 4),
            "funding_rate": round(float(fr), 8),
        }
    except Exception as exc:
        logger.error("funding_trap_detector error: %s", exc)
        return {"trap": False, "severity": 0.0, "funding_rate": 0.0}


def oi_spike_detection(current_oi: float, oi_history: List[float], price: float) -> Dict[str, Any]:
    try:
        hist = [float(x) for x in (oi_history or []) if x is not None and float(x) > 0]
        if len(hist) < 2 or current_oi <= 0:
            return {
                "oi_spike": False,
                "oi_direction": "flat",
                "delta_pct": 0.0,
                "zscore": 0.0,
                "risk": 0.0,
            }
        mu = statistics.mean(hist)
        sigma = statistics.pstdev(hist) if len(hist) >= 2 else 0.0
        z = (current_oi - mu) / (sigma + 1e-9) if sigma > 0 else 0.0
        delta_pct = (current_oi - hist[-1]) / (hist[-1] + 1e-9)
        oi_spike = abs(delta_pct) >= 0.02 or abs(z) >= 2.0
        direction = "build_up" if delta_pct > 0 else "deleveraging" if delta_pct < 0 else "flat"
        risk = min(1.0, abs(z) / 4.0 + abs(delta_pct) * 4.0)
        return {
            "oi_spike": bool(oi_spike),
            "oi_direction": direction,
            "delta_pct": round(float(delta_pct), 6),
            "zscore": round(float(z), 4),
            "risk": round(float(risk), 4),
        }
    except Exception as exc:
        logger.error("oi_spike_detection error: %s", exc)
        return {"oi_spike": False, "oi_direction": "flat", "delta_pct": 0.0, "zscore": 0.0, "risk": 0.0}


def detect_stop_hunt(orderbook: dict, trades: List[dict], recent_candles: Any = None) -> Dict[str, Any]:
    try:
        best_bid, best_ask = _best_bid_ask(orderbook)
        mid = (best_bid + best_ask) / 2.0 if (best_bid + best_ask) > 0 else 0.0
        buy_taker = 0.0
        sell_taker = 0.0
        for t in (trades or [])[-200:]:
            usd = _trade_usd(t, mid)
            side = _trade_side(t)
            if side == "BUY":
                buy_taker += usd
            elif side == "SELL":
                sell_taker += usd
        dominant = "BUY" if buy_taker >= sell_taker else "SELL"
        ratio = (max(buy_taker, sell_taker) / (min(buy_taker, sell_taker) + 1e-9)) if min(buy_taker, sell_taker) > 0 else 0.0
        closes = _ohlcv_to_closes(recent_candles)
        spike = False
        if len(closes) >= 10 and mid > 0:
            recent_hi = max(closes[-10:])
            recent_lo = min(closes[-10:])
            if mid > recent_hi * 1.003 or mid < recent_lo * 0.997:
                spike = True
        bids_vol, asks_vol = _book_volumes(orderbook, depth=10)
        thin_book = (bids_vol + asks_vol) > 0 and min(bids_vol, asks_vol) / (max(bids_vol, asks_vol) + 1e-9) < 0.35
        stop_hunt = bool(spike and ratio >= 1.5 and thin_book) or bool(spike and ratio >= 2.0)
        return {
            "stop_hunt": stop_hunt,
            "stop_hunt_detected": stop_hunt,
            "dominant": dominant,
            "ratio": round(float(ratio), 3),
            "buy_taker": round(float(buy_taker), 4),
            "sell_taker": round(float(sell_taker), 4),
            "spike": bool(spike),
            "sweep_side": dominant.lower(),
            "strength": round(float(_clamp((ratio / 3.0) if spike else 0.0, 0.0, 1.0)), 4),
        }
    except Exception as exc:
        logger.error("detect_stop_hunt error: %s", exc)
        return {
            "stop_hunt": False,
            "stop_hunt_detected": False,
            "dominant": "BUY",
            "ratio": 0.0,
            "buy_taker": 0.0,
            "sell_taker": 0.0,
            "spike": False,
            "sweep_side": "neutral",
            "strength": 0.0,
        }


def detect_smart_money_absorption(orderbook: dict, trades: List[dict]) -> Dict[str, Any]:
    try:
        bids = (orderbook.get("bids") or [])[:10]
        asks = (orderbook.get("asks") or [])[:10]
        bids_vol = sum(_safe_float(b[1]) for b in bids)
        asks_vol = sum(_safe_float(a[1]) for a in asks)
        buy_usd = 0.0
        sell_usd = 0.0
        for t in (trades or [])[-100:]:
            side = _trade_side(t)
            usd = _trade_usd(t, 0.0)
            if side == "BUY":
                buy_usd += usd
            elif side == "SELL":
                sell_usd += usd
        absorption = False
        score = 0.0
        if sell_usd > buy_usd * 1.5 and bids_vol > asks_vol * 1.15:
            absorption = True
            score = min(1.0, ((bids_vol / (asks_vol + 1e-9)) * (sell_usd / (buy_usd + 1e-9))) / 10.0)
        elif buy_usd > sell_usd * 1.5 and asks_vol > bids_vol * 1.15:
            absorption = True
            score = min(1.0, ((asks_vol / (bids_vol + 1e-9)) * (buy_usd / (sell_usd + 1e-9))) / 10.0)
        return {
            "absorption": bool(absorption),
            "score": round(float(score), 4),
            "buy_usd": round(float(buy_usd), 4),
            "sell_usd": round(float(sell_usd), 4),
            "bids_vol": round(float(bids_vol), 4),
            "asks_vol": round(float(asks_vol), 4),
        }
    except Exception as exc:
        logger.error("detect_smart_money_absorption error: %s", exc)
        return {
            "absorption": False,
            "score": 0.0,
            "buy_usd": 0.0,
            "sell_usd": 0.0,
            "bids_vol": 0.0,
            "asks_vol": 0.0,
        }


def _detect_spoofing_details(
    orderbook_snapshots: List[dict],
    top_n: int = 3,
    cancel_ratio_thresh: float = 0.6,
    min_peak_size: float = 1000.0,
) -> Dict[str, Any]:
    try:
        snaps = orderbook_snapshots or []
        if len(snaps) < 3:
            return {"spoof": False, "evidence": []}
        evidence = []
        for side in ("bids", "asks"):
            for level in range(top_n):
                sizes = []
                prices = []
                for snap in snaps:
                    levels = snap.get(side, []) or []
                    if len(levels) > level:
                        prices.append(_safe_float(levels[level][0]))
                        sizes.append(_safe_float(levels[level][1]))
                    else:
                        prices.append(None)
                        sizes.append(0.0)
                peak = max(sizes) if sizes else 0.0
                last = sizes[-1] if sizes else 0.0
                if peak >= min_peak_size and last <= peak * (1.0 - cancel_ratio_thresh):
                    evidence.append(
                        {
                            "side": side,
                            "level": level,
                            "peak": round(float(peak), 4),
                            "last": round(float(last), 4),
                            "prices": prices,
                        }
                    )
        return {"spoof": bool(evidence), "evidence": evidence}
    except Exception as exc:
        logger.error("_detect_spoofing_details error: %s", exc)
        return {"spoof": False, "evidence": []}


def detect_spoofing(order_book: Any) -> bool:
    """Public boolean API. Accepts one orderbook dict or a list of snapshots."""
    try:
        if isinstance(order_book, list):
            details = _detect_spoofing_details(order_book)
        else:
            details = _detect_spoofing_details([order_book or {}])
        return bool(details.get("spoof", False))
    except Exception:
        return False


def calculate_liquidity_score(
    orderbook: dict,
    trades: Optional[List[dict]] = None,
    recent_candles: Any = None,
) -> float:
    try:
        bids = (orderbook.get("bids") or [])[:10]
        asks = (orderbook.get("asks") or [])[:10]
        bid_vol = sum(_safe_float(b[1]) for b in bids)
        ask_vol = sum(_safe_float(a[1]) for a in asks)
        total_depth = bid_vol + ask_vol
        spread = _spread_pct(orderbook, 0.0)
        spread_score = 1.0 - _clamp(spread * 100.0, 0.0, 1.0)
        depth_score = _clamp(math.log1p(total_depth) / 10.0, 0.0, 1.0)
        trade_volume = 0.0
        for t in (trades or [])[-100:]:
            trade_volume += _trade_usd(t, 0.0)
        volume_score = _clamp(math.log1p(trade_volume) / 14.0, 0.0, 1.0)
        score = (spread_score * 0.35) + (depth_score * 0.40) + (volume_score * 0.25)
        return round(_clamp(score, 0.0, 1.0), 6)
    except Exception:
        return 0.0


def calculate_ml_confidence(imbalance: float, spread: float, liquidity_score: float) -> float:
    try:
        imb = _clamp(abs(_safe_float(imbalance)), 0.0, 1.0)
        liq = _clamp(_safe_float(liquidity_score), 0.0, 1.0)
        spread_penalty = _clamp(1.0 - (_safe_float(spread) * 100.0), 0.0, 1.0)
        confidence = (imb * 0.45) + (liq * 0.40) + (spread_penalty * 0.15)
        return round(_clamp(confidence, 0.0, 1.0), 6)
    except Exception:
        return 0.0


def get_market_data(
    orderbook: dict,
    trades: Optional[List[dict]] = None,
    recent_candles: Any = None,
    price: float = 0.0,
    orderbook_snapshots: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    try:
        bids = (orderbook.get("bids") or [])[:10]
        asks = (orderbook.get("asks") or [])[:10]
        bid_vol = sum(_safe_float(b[1]) for b in bids)
        ask_vol = sum(_safe_float(a[1]) for a in asks)
        total_vol = bid_vol + ask_vol
        imbalance = 0.0 if total_vol == 0 else (bid_vol - ask_vol) / total_vol
        spread = _spread_pct(orderbook, price)
        liquidity_score = calculate_liquidity_score(orderbook, trades, recent_candles)
        spoof_details = _detect_spoofing_details(orderbook_snapshots or [orderbook])
        liq_sweep = detect_liquidity_sweep(trades or [], price)
        rows: list = []
        if isinstance(recent_candles, dict):
            rows = (
                recent_candles.get("1m")
                or recent_candles.get("15m")
                or recent_candles.get("primary")
                or []
            )
        else:
            rows = recent_candles or []
        rows = [r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 6]
        recent_rows = rows[-20:] if rows else []
        recent_high = max(_safe_float(r[2]) for r in recent_rows) if recent_rows else price
        recent_low = min(_safe_float(r[3]) for r in recent_rows) if recent_rows else price
        signal = "NONE"
        if not spoof_details.get("spoof", False) and liquidity_score >= 0.50 and spread <= 0.001:
            if imbalance >= 0.12:
                signal = "LONG"
            elif imbalance <= -0.12:
                signal = "SHORT"
        confidence = calculate_ml_confidence(imbalance, spread, liquidity_score)
        return {
            "signal": signal,
            "confidence": confidence,
            "liquidity_score": liquidity_score,
            "imbalance": round(float(imbalance), 6),
            "spread_pct": round(float(spread), 6),
            "high": round(float(recent_high), 2),
            "low": round(float(recent_low), 2),
            "liquidity_sweep": {
                "side": str((liq_sweep or {}).get("side", "unknown")).upper(),
                "sweep": bool((liq_sweep or {}).get("sweep", False)),
                "size_usd": _safe_float((liq_sweep or {}).get("size_usd", 0.0)),
            },
            "spoof_detected": bool(spoof_details.get("spoof", False)),
            "spoof_details": spoof_details,
        }
    except Exception:
        return {
            "signal": "NONE",
            "confidence": 0.0,
            "liquidity_score": 0.0,
            "imbalance": 0.0,
            "spread_pct": 0.0,
            "high": price,
            "low": price,
            "liquidity_sweep": {"side": "unknown", "sweep": False, "size_usd": 0.0},
            "spoof_detected": False,
            "spoof_details": {"spoof": False, "evidence": []},
        }


def smart_money_detection_engine(orderbook: dict, trades: List[dict], price: float) -> Dict[str, Any]:
    try:
        absorption = detect_smart_money_absorption(orderbook, trades)
        sweep = detect_liquidity_sweep(trades, price)
        z = []
        for side in ("bids", "asks"):
            levels = (orderbook.get(side) or [])[:10]
            if levels:
                sizes = [_safe_float(x[1]) for x in levels]
                thresh = _mean(sizes, 0.0) + (statistics.pstdev(sizes) if len(sizes) > 2 else 0.0)
                for p, s in levels:
                    if _safe_float(s) >= thresh:
                        z.append({"side": side[:-1], "price": _safe_float(p), "size": _safe_float(s)})
        smart_score = 0.0
        smart_score += 0.5 if absorption.get("absorption") else 0.0
        smart_score += 0.3 if sweep.get("sweep") else 0.0
        smart_score += min(0.2, len(z) * 0.05)
        return {
            "smart_money_detected": bool(smart_score >= 0.5),
            "smart_money_score": round(float(_clamp(smart_score, 0.0, 1.0)), 4),
            "absorption_zones": z,
        }
    except Exception as exc:
        logger.error("smart_money_detection_engine error: %s", exc)
        return {"smart_money_detected": False, "smart_money_score": 0.0, "absorption_zones": []}


def smart_money_absorption_engine(orderbook: dict, trades: List[dict], price: float) -> Dict[str, Any]:
    try:
        res = detect_smart_money_absorption(orderbook, trades)
        zones = []
        for side in ("bids", "asks"):
            levels = (orderbook.get(side) or [])[:10]
            if levels:
                sizes = [_safe_float(x[1]) for x in levels]
                thresh = _mean(sizes, 0.0) + (statistics.pstdev(sizes) if len(sizes) > 2 else 0.0)
                for p, s in levels:
                    if _safe_float(s) >= thresh:
                        zones.append(
                            {
                                "side": side[:-1],
                                "price": _safe_float(p),
                                "size": _safe_float(s),
                                "distance_points": abs(_safe_float(p) - price),
                            }
                        )
        res["absorption_zones"] = zones
        return res
    except Exception as exc:
        logger.error("smart_money_absorption_engine error: %s", exc)
        return {
            "absorption": False,
            "score": 0.0,
            "buy_usd": 0.0,
            "sell_usd": 0.0,
            "bids_vol": 0.0,
            "asks_vol": 0.0,
            "absorption_zones": [],
        }


def stop_hunt_engine(orderbook: dict, trades: List[dict], ohlcv: Any = None, price: float = 0.0) -> Dict[str, Any]:
    try:
        return detect_stop_hunt(orderbook, trades, recent_candles=ohlcv)
    except Exception as exc:
        logger.error("stop_hunt_engine error: %s", exc)
        return {"stop_hunt_detected": False, "reason": "error", "sweep_side": "neutral", "strength": 0.0}


def market_maker_position_model(
    order_flow_pressure: Dict[str, Any],
    order_imbalance: Dict[str, Any],
    liquidity_map: Dict[str, Any],
    price: float,
) -> Dict[str, Any]:
    try:
        ofp = _safe_float(order_flow_pressure.get("pressure_score", 0.0))
        imb = _safe_float(order_imbalance.get("imbalance", 0.0))
        nearest = liquidity_map.get("nearest_zone") or {}
        side = str(nearest.get("side", "neutral")).lower()
        dist = _safe_float(nearest.get("distance_points", 0.0))
        bias_score = ofp * 0.45 + imb * 0.35
        if side == "bid":
            bias_score += 0.1
        elif side == "ask":
            bias_score -= 0.1
        if dist > 300:
            bias_score *= 0.95
        bias = "neutral"
        if bias_score > 0.18:
            bias = "bullish"
        elif bias_score < -0.18:
            bias = "bearish"
        return {
            "market_maker_bias": bias,
            "confidence": round(float(min(1.0, abs(bias_score))), 4),
            "details": {
                "bias_score": round(float(bias_score), 6),
                "nearest_side": side,
                "nearest_distance": round(float(dist), 4),
            },
        }
    except Exception as exc:
        logger.error("market_maker_position_model error: %s", exc)
        return {"market_maker_bias": "neutral", "confidence": 0.0, "details": {}}


def strategy_optimization_engine(
    volatility: float,
    recent_performance: dict,
    cascade_probability: float,
    order_flow_pressure: float = 0.0,
    order_imbalance: float = 0.0,
) -> Dict[str, Any]:
    try:
        win_rate = _safe_float(recent_performance.get("win_rate", recent_performance.get("win_rate_pct", 0.0)))
        if win_rate > 1.0:
            win_rate /= 100.0
        threshold_scale = 1.0
        confidence_scale = 1.0
        risk_scale = 1.0
        cooldown = 3
        notes = []
        if volatility < 0.008:
            threshold_scale *= 0.85
            confidence_scale *= 0.92
            risk_scale *= 0.80
            cooldown = 2
            notes.append("low_vol_relax")
        elif volatility > 0.025:
            threshold_scale *= 1.10
            confidence_scale *= 1.05
            risk_scale *= 0.70
            cooldown = 4
            notes.append("high_vol_strict")
        if win_rate < 0.45:
            threshold_scale *= 0.92
            confidence_scale *= 0.95
            notes.append("low_winrate_relax")
        elif win_rate > 0.60:
            confidence_scale *= 1.05
            threshold_scale *= 1.05
            notes.append("high_winrate_selective")
        signal_bias = _clamp(
            order_flow_pressure * 0.35
            + order_imbalance * 0.25
            + (
                0.15
                if cascade_probability < 0.25
                else -0.10
                if cascade_probability > 0.65
                else 0.0
            ),
            -0.4,
            0.4,
        )
        return {
            "threshold_scale": round(float(threshold_scale), 4),
            "confidence_scale": round(float(confidence_scale), 4),
            "risk_scale": round(float(risk_scale), 4),
            "cooldown": int(cooldown),
            "signal_bias": round(float(signal_bias), 4),
            "notes": notes or ["balanced"],
        }
    except Exception as exc:
        logger.error("strategy_optimization_engine error: %s", exc)
        return {
            "threshold_scale": 1.0,
            "confidence_scale": 1.0,
            "risk_scale": 1.0,
            "cooldown": 3,
            "signal_bias": 0.0,
            "notes": ["fallback"],
        }


# DEPRECATED (moved to signal_engine) — kept for backward compatibility
def compute_score(
    sma_signal: Any,
    ob_imbalance: float,
    whale_signal: Any,
    funding_rate: float,
    cascade_probability: float,
) -> Dict[str, Any]:
    try:
        def _signal_to_bias(v: Any) -> float:
            if isinstance(v, dict):
                for key in ("direction", "signal", "bias"):
                    if key in v:
                        return _signal_to_bias(v.get(key))
                return 0.0
            s = str(v or "").upper()
            if s in ("BUY", "LONG", "BULL", "BULLISH", "UP"):
                return 1.0
            if s in ("SELL", "SHORT", "BEAR", "BEARISH", "DOWN"):
                return -1.0
            return 0.0

        sma_bias = _signal_to_bias(sma_signal)
        whale_bias = _signal_to_bias(whale_signal)
        ob_bias = _clamp(ob_imbalance, -1.0, 1.0)
        fr = _safe_float(funding_rate)
        cprob = _clamp(cascade_probability, 0.0, 1.0)

        bull = 0.5
        bull += sma_bias * 0.20
        bull += ob_bias * 0.20
        bull += whale_bias * 0.15
        bull += (-_clamp(fr * 500.0, -0.25, 0.25)) * 0.15
        bull += (1.0 - cprob) * 0.15
        bull = _clamp(bull, 0.0, 1.0)

        long_score = round(float(bull), 6)
        short_score = round(float(1.0 - bull), 6)
        ai_score = round(float(long_score - short_score), 6)
        confidence = round(float(max(long_score, short_score)), 6)

        if ai_score > 0.05 and confidence >= 0.55:
            signal = "LONG"
        elif ai_score < -0.05 and confidence >= 0.55:
            signal = "SHORT"
        else:
            signal = "HOLD"

        return {
            "ai_score": ai_score,
            "confidence": confidence,
            "long_score": long_score,
            "short_score": short_score,
            "signal": signal,
            "direction": signal,
            "components": {
                "sma": round(float(sma_bias), 6),
                "ob": round(float(ob_bias), 6),
                "whale": round(float(whale_bias), 6),
                "funding": round(float(fr), 8),
                "cascade": round(float(cprob), 6),
            },
        }
    except Exception as exc:
        logger.error("compute_score error: %s", exc)
        return {
            "ai_score": 0.0,
            "confidence": 0.0,
            "long_score": 0.0,
            "short_score": 0.0,
            "signal": "HOLD",
            "direction": "HOLD",
            "components": {"sma": 0.0, "ob": 0.0, "whale": 0.0, "funding": 0.0, "cascade": 0.0},
        }


def _estimate_volatility_from_ohlcv(ohlcv: Any, period: int = 20) -> float:
    try:
        closes = _ohlcv_to_closes(ohlcv)
        if len(closes) < 2:
            return 0.0
        window = closes[-period:] if len(closes) >= period else closes
        rets = [
            math.log(window[i] / window[i - 1])
            for i in range(1, len(window))
            if window[i - 1] > 0 and window[i] > 0
        ]
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) if var > 0 else 0.0
    except Exception:
        return 0.0


def detect_liquidation_clusters(
    liquidation_cluster_usd: float,
    open_interest: float,
    funding_rate: float,
    cascade_prob: float,
) -> Dict[str, Any]:
    try:
        liq_ratio = (liquidation_cluster_usd / open_interest) if open_interest > 0 else 0.0
        funding_pressure = min(1.0, abs(_safe_float(funding_rate)) * 400.0)
        cluster_score = min(1.0, (liq_ratio * 3.0) + (cascade_prob * 0.45) + (funding_pressure * 0.25))
        zone = "low"
        if cluster_score >= 0.75:
            zone = "critical"
        elif cluster_score >= 0.5:
            zone = "high"
        elif cluster_score >= 0.25:
            zone = "medium"
        return {
            "cluster_score": round(float(cluster_score), 4),
            "zone": zone,
            "liq_ratio": round(float(liq_ratio), 6),
            "funding_pressure": round(float(funding_pressure), 6),
        }
    except Exception as exc:
        logger.error("detect_liquidation_clusters error: %s", exc)
        return {
            "cluster_score": 0.0,
            "zone": "low",
            "liq_ratio": 0.0,
            "funding_pressure": 0.0,
        }


def detect_liquidation_clusters_from_trade_flow(
    trades: List[dict],
    open_interest: float,
    funding_rate: float,
    cascade_prob: float,
    price: float = 0.0,
) -> Dict[str, Any]:
    liq_track = track_liquidations(trades, price)
    return detect_liquidation_clusters(
        liq_track.get("total_liq", 0.0),
        open_interest,
        funding_rate,
        cascade_prob,
    )


def detect_liquidity_gravity(orderbook: dict, price: float, depth: int = 10) -> Dict[str, Any]:
    return liquidity_gravity_engine(orderbook, price, depth=depth)


def detect_liquidity_map(orderbook: dict, price: float, depth: int = 10) -> Dict[str, Any]:
    return predict_liquidity_map(orderbook, price, depth=depth)


def get_liquidation_heatmap(
    liquidation_cluster: float,
    open_interest: float,
    funding_rate: float = 0.0,
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    return liquidation_heatmap_engine(
        liquidation_cluster,
        open_interest,
        funding_rate=funding_rate,
        spread_pct=spread_pct,
    )


def _extract_price_series(candles: Any) -> List[float]:
    try:
        if isinstance(candles, dict):
            for k in ("1m", "5m", "15m", "primary"):
                if k in candles:
                    closes = _ohlcv_to_closes(candles.get(k))
                    if closes:
                        return closes
            return []
        return _ohlcv_to_closes(candles)
    except Exception:
        return []


def detect_entry_condition(price, liquidity_map, engines, ai_score, confidence, volume_intel=None):
    return detect_entry_trigger(price, liquidity_map, engines, ai_score, confidence, volume_intel=volume_intel)


def build_trade_plan_for_signal(price, direction, liquidity_map):
    return build_trade_plan(price, direction, liquidity_map)


def _normalize_signal(v: Any) -> float:
    if isinstance(v, dict):
        for key in ("direction", "signal", "bias"):
            if key in v:
                return _normalize_signal(v.get(key))
        return 0.0
    s = str(v or "").upper()
    if s in ("BUY", "LONG", "BULL", "BULLISH", "UP"):
        return 1.0
    if s in ("SELL", "SHORT", "BEAR", "BEARISH", "DOWN"):
        return -1.0
    return 0.0


# =========================
# INSTITUTIONAL EXTENSIONS
# =========================

SMC_LEARNING_PATH = os.path.join(os.path.dirname(__file__), "smc_learning_memory.json")

def _wick_stats(candle: list) -> Dict[str, float]:
    try:
        o = _safe_float(candle[1])
        h = _safe_float(candle[2])
        l = _safe_float(candle[3])
        c = _safe_float(candle[4])
        rng = max(h - l, 1e-9)
        body_hi = max(o, c)
        body_lo = min(o, c)
        upper = max(0.0, h - body_hi) / rng
        lower = max(0.0, body_lo - l) / rng
        body = abs(c - o) / rng
        return {"upper": _clamp(upper, 0.0, 1.0), "lower": _clamp(lower, 0.0, 1.0), "body": _clamp(body, 0.0, 1.0)}
    except Exception:
        return {"upper": 0.0, "lower": 0.0, "body": 0.0}


def _displacement_score(rows: List[list]) -> Dict[str, Any]:
    try:
        if len(rows) < 5:
            return {"exists": False, "direction": None, "score": 0.0, "atr_ratio": 0.0}
        last = rows[-1]
        closes = [r[4] for r in rows[-20:]]
        atr = max(_atr(rows[-30:], 14), 1.0)
        body = abs(_safe_float(last[4]) - _safe_float(last[1]))
        wick = _wick_stats(last)
        prev_high = max(r[2] for r in rows[-6:-1]) if len(rows) >= 6 else rows[-2][2]
        prev_low = min(r[3] for r in rows[-6:-1]) if len(rows) >= 6 else rows[-2][3]

        direction = None
        break_up = last[4] > prev_high
        break_down = last[4] < prev_low
        if break_up:
            direction = "LONG"
        elif break_down:
            direction = "SHORT"

        atr_ratio = body / atr
        score = 0.0
        if direction:
            score += 0.35
        if atr_ratio >= 0.75:
            score += 0.35
        if wick["body"] >= 0.55:
            score += 0.15
        if wick["upper"] <= 0.25 or wick["lower"] <= 0.25:
            score += 0.15
        return {
            "exists": bool(direction and score >= 0.6),
            "direction": direction,
            "score": round(_clamp(score, 0.0, 1.0), 4),
            "atr_ratio": round(_clamp(atr_ratio, 0.0, 10.0), 4),
            "body_ratio": round(wick["body"], 4),
            "wick": wick,
        }
    except Exception:
        return {"exists": False, "direction": None, "score": 0.0, "atr_ratio": 0.0}


def predict_liquidity_map(orderbook: dict, price: float, depth: int = 10) -> Dict[str, Any]:
    try:
        bids = (orderbook.get("bids") or [])[: max(depth, 5)]
        asks = (orderbook.get("asks") or [])[: max(depth, 5)]
        zones = []
        for i, (p, s) in enumerate(asks):
            p = _safe_float(p)
            s = _safe_float(s)
            if p > 0:
                zones.append({"side": "ask", "price": p, "size": s, "source": "book", "rank": i, "distance_points": abs(p - price)})
        for i, (p, s) in enumerate(bids):
            p = _safe_float(p)
            s = _safe_float(s)
            if p > 0:
                zones.append({"side": "bid", "price": p, "size": s, "source": "book", "rank": i, "distance_points": abs(price - p)})

        if zones:
            largest = max(zones, key=lambda z: z.get("size", 0.0))
            nearest = min(zones, key=lambda z: z.get("distance_points", 1e18))
        else:
            largest = {"side": "none", "price": price, "size": 0.0, "source": "none", "rank": -1, "distance_points": 0.0}
            nearest = largest

        return {
            "liquidity_map": zones,
            "largest_zone": largest,
            "nearest_zone": nearest,
            "support_zone": min([z for z in zones if z["side"] == "bid"], key=lambda z: z["distance_points"], default=None),
            "resistance_zone": min([z for z in zones if z["side"] == "ask"], key=lambda z: z["distance_points"], default=None),
            "zone_count": len(zones),
        }
    except Exception:
        return {
            "liquidity_map": [],
            "largest_zone": {"side": "none", "price": price, "size": 0.0, "source": "none", "rank": -1, "distance_points": 0.0},
            "nearest_zone": {"side": "none", "price": price, "size": 0.0, "source": "none", "rank": -1, "distance_points": 0.0},
            "support_zone": None,
            "resistance_zone": None,
            "zone_count": 0,
        }


def liquidity_gravity_engine(orderbook: dict, price: float, depth: int = 10) -> Dict[str, Any]:
    try:
        lm = predict_liquidity_map(orderbook, price, depth=depth)
        zones = lm.get("liquidity_map", [])
        if not zones:
            return {"gravity_score": 0.0, "pull_side": "neutral", "pull_price": price, "reason": "no_zones"}

        nearest = lm.get("nearest_zone") or {}
        side = str(nearest.get("side", "neutral"))
        dist = _safe_float(nearest.get("distance_points", 0.0))
        size = _safe_float(nearest.get("size", 0.0))
        avg_size = _mean([_safe_float(z.get("size", 0.0)) for z in zones], 0.0) or 1.0

        gravity = _clamp((size / avg_size) * 0.45 + (1.0 - min(dist / max(price * 0.003, 1.0), 1.0)) * 0.55, 0.0, 1.0)
        pull_side = "ask" if side == "ask" else "bid" if side == "bid" else "neutral"

        return {
            "gravity_score": round(float(gravity), 4),
            "pull_side": pull_side,
            "pull_price": round(_safe_float(nearest.get("price", price)), 2),
            "reason": "nearest_wall_magnet",
        }
    except Exception:
        return {"gravity_score": 0.0, "pull_side": "neutral", "pull_price": price, "reason": "error"}


def institutional_score_engine(
    price: float,
    orderbook: dict,
    trades: List[dict],
    candles: Dict[str, Any],
    open_interest: float,
    funding_rate: float,
    volume_intel: Dict[str, Any],
    market_state: Dict[str, Any],
    liquidity_map: Dict[str, Any],
    liquidity_gravity: Dict[str, Any],
    liquidity_sweep: Dict[str, Any],
    liq_track: Dict[str, Any],
    liq_clusters: Dict[str, Any],
    liq_heatmap: Dict[str, Any],
    stop_hunt: Dict[str, Any],
    absorption: Dict[str, Any],
    smart_money: Dict[str, Any],
    oi_spike: Dict[str, Any],
    cascade_probability: float,
    spoof: Dict[str, Any],
    order_flow: Dict[str, Any],
    order_imbalance: Dict[str, Any],
    market_maker: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        price = _safe_float(price)
        state = str((market_state or {}).get("state", "CHOPPY")).upper()
        allow_trade = bool((market_state or {}).get("allow_trade", False))
        compression = _safe_float((market_state or {}).get("compression", 1.0))
        vol_strength = _safe_float((volume_intel or {}).get("volume_strength", 0.0))
        mtf_conf = 1.0 if (volume_intel or {}).get("mtf_confirmation") else 0.0

        sweep = bool((liquidity_sweep or {}).get("sweep", False))
        stop = bool((stop_hunt or {}).get("stop_hunt", False))
        smart = bool((smart_money or {}).get("smart_money_detected", False) or (absorption or {}).get("absorption", False))
        oispike = bool((oi_spike or {}).get("oi_spike", False))
        disp = _displacement_score((candles or {}).get("1m", []) if isinstance(candles, dict) else candles)

        trap_conf = _safe_float((trap := (liquidity_sweep or {})).get("strength", 0.0))
        fvg = detect_fvg((candles or {}).get("15m", []) if isinstance(candles, dict) else candles)
        mtf = compute_mtf_bias(candles)
        structure = detect_structure((candles or {}).get("15m", []) if isinstance(candles, dict) else candles)
        fib_dir = "LONG" if mtf.get("htf_trend") == "bullish" or structure.get("trend") == "bullish" else "SHORT"
        fib = detect_fibonacci_zone((candles or {}).get("15m", []) if isinstance(candles, dict) else candles, price, fib_dir)
        regime = detect_market_regime((candles or {}).get("15m", []) if isinstance(candles, dict) else candles, _estimate_volatility_from_ohlcv(candles))

        # Hard filters first
        hard_pass = (
            allow_trade
            and state in ("COMPRESSION", "EXPANSION", "TRENDING")
            and state != "CHOPPY"
            and compression <= 0.50
            and disp.get("exists")
            and (sweep or stop or smart or oispike)
        )

        score = 0.0
        score += 1.8 if hard_pass else 0.0
        score += 1.2 if sweep else 0.0
        score += 0.9 if stop else 0.0
        score += 0.9 if smart else 0.0
        score += 0.7 if disp.get("exists") else 0.0
        score += 0.7 if fib.get("in_zone") else 0.0
        score += 0.6 if (fib.get("rejected") or fib.get("held")) else 0.0
        score += 0.6 if fvg.get("exists") else 0.0
        score += 0.6 if mtf.get("alignment_score", 0.0) >= 0.50 else 0.0
        score += 0.5 if (market_maker or {}).get("market_maker_bias") in ("bullish", "bearish") else 0.0
        score += 0.4 if (liq_heatmap or {}).get("heat_score", 0) >= 35 else 0.0
        score += 0.3 if (liq_clusters or {}).get("zone") in ("high", "critical") else 0.0

        ob = (orderbook or {})
        bid_vol = sum(_safe_float(b[1]) for b in (ob.get("bids") or [])[:10])
        ask_vol = sum(_safe_float(a[1]) for a in (ob.get("asks") or [])[:10])
        book_imb = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        score += 0.2 if abs(book_imb) > 0.15 else 0.0
        score += 0.2 if abs(_safe_float((order_flow or {}).get("strength", 0.0))) > 0.35 else 0.0

        direction = "HOLD"
        if mtf.get("htf_trend") == "bullish" and fib_dir == "LONG":
            direction = "LONG"
        elif mtf.get("htf_trend") == "bearish" and fib_dir == "SHORT":
            direction = "SHORT"

        if sweep and direction == "HOLD":
            direction = "LONG" if str((liquidity_sweep or {}).get("side", "")).upper() == "SELL" else "SHORT"
        if disp.get("direction") in ("LONG", "SHORT"):
            direction = disp["direction"]

        return {
            "institutional_score": round(_clamp(score, 0.0, 10.0), 4),
            "signal_strength": round(_clamp(score / 10.0, 0.0, 1.0), 4),
            "ai_score": round(1.0 if direction == "LONG" else -1.0 if direction == "SHORT" else 0.0, 4),
            "confidence": round(_clamp(score / 10.0, 0.0, 1.0), 4),
            "direction": direction,
            "details": {
                "state": state,
                "displacement": disp,
                "fib": fib,
                "fvg": fvg,
                "mtf": mtf,
                "regime": regime,
                "book_imbalance": round(float(book_imb), 4),
                "volume_strength": vol_strength,
                "sweep": sweep,
                "stop_hunt": stop,
                "smart_money": smart,
                "oi_spike": oispike,
                "cascade_probability": _safe_float(cascade_probability, 0.0),
            },
        }
    except Exception:
        return {
            "institutional_score": 0.0,
            "signal_strength": 0.0,
            "ai_score": 0.0,
            "confidence": 0.0,
            "direction": "HOLD",
            "details": {},
        }



def _to_rows(candles: Any) -> List[list]:
    if not candles:
        return []
    if isinstance(candles, dict):
        for key in ("1m", "3m", "5m", "15m", "1h", "primary"):
            if key in candles and candles[key]:
                return _to_rows(candles[key])
        return []
    rows = []
    for c in candles:
        if isinstance(c, (list, tuple)) and len(c) >= 6:
            rows.append(
                [
                    int(c[0]),
                    _safe_float(c[1]),
                    _safe_float(c[2]),
                    _safe_float(c[3]),
                    _safe_float(c[4]),
                    _safe_float(c[5]),
                ]
            )
    return rows


def _aggregate_rows(rows: List[list], factor: int) -> List[list]:
    rows = _to_rows(rows)
    if factor <= 1 or len(rows) < factor:
        return list(rows)
    out, chunk = [], []
    for r in rows:
        chunk.append(r)
        if len(chunk) == factor:
            ts = chunk[0][0]
            op = chunk[0][1]
            hi = max(x[2] for x in chunk)
            lo = min(x[3] for x in chunk)
            cl = chunk[-1][4]
            vol = sum(x[5] for x in chunk)
            out.append([ts, op, hi, lo, cl, vol])
            chunk = []
    if chunk:
        ts = chunk[0][0]
        op = chunk[0][1]
        hi = max(x[2] for x in chunk)
        lo = min(x[3] for x in chunk)
        cl = chunk[-1][4]
        vol = sum(x[5] for x in chunk)
        out.append([ts, op, hi, lo, cl, vol])
    return out


def _pivot_swings(rows: List[list], left: int = 2, right: int = 2) -> Dict[str, List[dict]]:
    rows = _to_rows(rows)
    highs, lows = [], []
    if len(rows) < left + right + 3:
        return {"highs": highs, "lows": lows}
    for i in range(left, len(rows) - right):
        hi = rows[i][2]
        lo = rows[i][3]
        prev_highs = [rows[j][2] for j in range(i - left, i)]
        next_highs = [rows[j][2] for j in range(i + 1, i + right + 1)]
        prev_lows = [rows[j][3] for j in range(i - left, i)]
        next_lows = [rows[j][3] for j in range(i + 1, i + right + 1)]
        if hi >= max(prev_highs) and hi > max(next_highs):
            highs.append({"index": i, "price": hi, "ts": rows[i][0]})
        if lo <= min(prev_lows) and lo < min(next_lows):
            lows.append({"index": i, "price": lo, "ts": rows[i][0]})
    return {"highs": highs, "lows": lows}


def detect_structure(candles: Any) -> Dict[str, Any]:
    rows = _to_rows(candles)
    if len(rows) < 10:
        return {
            "trend": "ranging",
            "state": "RANGING",
            "bos": None,
            "choch": None,
            "swing_highs": [],
            "swing_lows": [],
            "last_swing_high": None,
            "last_swing_low": None,
            "confidence": 0.0,
            "reason": "not_enough_data",
        }

    piv = _pivot_swings(rows, 2, 2)
    highs = piv["highs"]
    lows = piv["lows"]
    last_close = rows[-1][4]
    prev_close = rows[-2][4]
    atr = _atr(rows[-30:], 14)
    body = _body_ratio(rows[-1])

    last_swing_high = highs[-1]["price"] if highs else None
    prev_swing_high = highs[-2]["price"] if len(highs) >= 2 else None
    last_swing_low = lows[-1]["price"] if lows else None
    prev_swing_low = lows[-2]["price"] if len(lows) >= 2 else None

    hh = bool(last_swing_high is not None and prev_swing_high is not None and last_swing_high > prev_swing_high)
    hl = bool(last_swing_low is not None and prev_swing_low is not None and last_swing_low > prev_swing_low)
    lh = bool(last_swing_high is not None and prev_swing_high is not None and last_swing_high < prev_swing_high)
    ll = bool(last_swing_low is not None and prev_swing_low is not None and last_swing_low < prev_swing_low)

    bos_up = bool(last_swing_high is not None and prev_close <= last_swing_high < last_close)
    bos_down = bool(last_swing_low is not None and prev_close >= last_swing_low > last_close)

    bullish = hh and hl
    bearish = lh and ll

    choch = None
    if bullish and bos_down:
        choch = "bearish"
    elif bearish and bos_up:
        choch = "bullish"

    trend = "ranging"
    state = "RANGING"
    if bullish and not bearish:
        trend = "bullish"
        state = "TRENDING"
    elif bearish and not bullish:
        trend = "bearish"
        state = "TRENDING"

    if atr > 0 and len(rows) >= 4:
        impulse = abs(rows[-1][4] - rows[-4][4]) / max(rows[-4][4], 1e-9)
    else:
        impulse = 0.0

    confidence = 0.0
    confidence += 0.35 if bullish or bearish else 0.10
    confidence += 0.25 if bos_up or bos_down else 0.05
    confidence += 0.20 if body >= 0.50 else 0.05
    confidence += 0.20 if impulse > 0.004 else 0.05
    confidence = _clamp(confidence, 0.0, 1.0)

    return {
        "trend": trend,
        "state": state if choch is None else "TRANSITION",
        "bos": "bullish" if bos_up else "bearish" if bos_down else None,
        "choch": choch,
        "swing_highs": highs,
        "swing_lows": lows,
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "atr": atr,
        "confidence": round(confidence, 4),
        "reason": "structure_detected",
    }


def detect_liquidity(candles: Any, price: float) -> Dict[str, Any]:
    rows = _to_rows(candles)
    if len(rows) < 10:
        return {
            "liquidity_map": [],
            "buy_side_liquidity": [],
            "sell_side_liquidity": [],
            "equal_highs": [],
            "equal_lows": [],
            "nearest_above": None,
            "nearest_below": None,
            "zone_count": 0,
        }

    price = _safe_float(price)
    atr = max(_atr(rows[-30:], 14), price * 0.0005)
    tol = max(atr * 0.20, price * 0.0008, 1.0)

    piv = _pivot_swings(rows, 2, 2)
    highs = piv["highs"]
    lows = piv["lows"]

    zones = []
    buy_side, sell_side = [], []
    equal_highs, equal_lows = [], []

    def _add_zone(side: str, p: float, kind: str, strength: float = 0.7):
        item = {
            "side": side,
            "price": round(float(p), 2),
            "type": kind,
            "strength": round(float(strength), 3),
            "distance": round(abs(price - p), 2),
        }
        zones.append(item)
        if side == "ask":
            buy_side.append(item)
        else:
            sell_side.append(item)

    for h in highs:
        _add_zone("ask", h["price"], "swing_high", 0.75)
    for l in lows:
        _add_zone("bid", l["price"], "swing_low", 0.75)

    for arr, side, target in ((highs, "ask", equal_highs), (lows, "bid", equal_lows)):
        for i in range(1, len(arr)):
            a = arr[i - 1]["price"]
            b = arr[i]["price"]
            if abs(a - b) <= tol:
                p = (a + b) / 2.0
                target.append({"price": round(p, 2), "levels": [a, b], "tolerance": round(tol, 4)})
                _add_zone(side, p, "equal_high" if side == "ask" else "equal_low", 0.90)

    recent = rows[-30:]
    recent_high = max(r[2] for r in recent)
    recent_low = min(r[3] for r in recent)
    _add_zone("ask", recent_high, "range_high", 0.65)
    _add_zone("bid", recent_low, "range_low", 0.65)

    uniq = {}
    for z in zones:
        uniq[z["price"]] = z
    zones = sorted(uniq.values(), key=lambda x: x["distance"])

    nearest_above = min([z for z in zones if z["price"] > price], key=lambda x: x["price"] - price, default=None)
    nearest_below = min([z for z in zones if z["price"] < price], key=lambda x: price - x["price"], default=None)

    return {
        "liquidity_map": zones,
        "buy_side_liquidity": buy_side,
        "sell_side_liquidity": sell_side,
        "equal_highs": equal_highs,
        "equal_lows": equal_lows,
        "nearest_above": nearest_above,
        "nearest_below": nearest_below,
        "zone_count": len(zones),
        "atr": atr,
    }


def analyze_liquidity_intent(orderbook: dict, trades: List[dict], candles: Any) -> Dict[str, Any]:
    rows = _to_rows(candles)
    price = _safe_float(rows[-1][4] if rows else 0.0)
    liquidity_map = detect_liquidity(rows, price)
    best_bid, best_ask = _best_bid_ask(orderbook)
    bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
    orderbook_skew = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
    trade_delta = 0.0
    if trades:
        buy, sell = _volume_side(trades[-100:])
        trade_delta = (buy - sell) / (buy + sell + 1e-9)

    zones = liquidity_map.get("liquidity_map", [])
    bait_liquidity = 0.0
    resting_liquidity = 0.0
    engineered_liquidity = 0.0

    for z in zones:
        dist = _safe_float(z.get("distance"))
        strength = _safe_float(z.get("strength", 0.0))
        if z.get("type") in ("equal_high", "equal_low", "range_high", "range_low"):
            bait_liquidity += strength * 0.4
        if z.get("type") in ("swing_high", "swing_low"):
            resting_liquidity += strength * 0.5
        engineered_liquidity += strength * max(0.1, 1.0 - min(dist / max(price * 0.01, 1.0), 1.0)) * 0.3

    if abs(orderbook_skew) > 0.15:
        engineered_liquidity += abs(orderbook_skew) * 0.15
    if abs(trade_delta) > 0.2:
        engineered_liquidity += abs(trade_delta) * 0.15

    return {
        "bait_liquidity": round(_clamp(bait_liquidity, 0.0, 1.0), 4),
        "resting_liquidity": round(_clamp(resting_liquidity, 0.0, 1.0), 4),
        "engineered_liquidity": round(_clamp(engineered_liquidity, 0.0, 1.0), 4),
        "liquidity_zones": zones,
        "nearest_above": liquidity_map.get("nearest_above"),
        "nearest_below": liquidity_map.get("nearest_below"),
        "orderbook_skew": round(float(orderbook_skew), 4),
        "trade_delta": round(float(trade_delta), 4),
        "best_bid": round(float(best_bid), 2),
        "best_ask": round(float(best_ask), 2),
    }


def detect_fvg(candles: Any) -> Dict[str, Any]:
    rows = _to_rows(candles)
    if len(rows) < 3:
        return {
            "exists": False,
            "filled": False,
            "entry_zone": (None, None),
            "strength": 0.0,
            "direction": None,
        }
    c1, c2, c3 = rows[-3], rows[-2], rows[-1]
    atr = max(_atr(rows[-30:], 14), 1.0)

    bullish_gap = c1[2] < c3[3]
    bearish_gap = c1[3] > c3[2]
    exists = bullish_gap or bearish_gap
    direction = "bullish" if bullish_gap else "bearish" if bearish_gap else None

    if bullish_gap:
        low, high = c1[2], c3[3]
    elif bearish_gap:
        low, high = c3[2], c1[3]
    else:
        low, high = None, None

    filled = False
    if exists and low is not None and high is not None:
        filled = c2[3] <= high and c2[2] >= low or (rows[-1][3] <= high and rows[-1][2] >= low)

    size = abs((high or 0.0) - (low or 0.0))
    strength = _clamp((size / atr) * 0.6 + (_body_ratio(c3) * 0.4), 0.0, 1.0) if exists else 0.0
    return {
        "exists": bool(exists),
        "filled": bool(filled),
        "entry_zone": (round(float(low), 2) if low is not None else None, round(float(high), 2) if high is not None else None),
        "strength": round(float(strength), 4),
        "direction": direction,
    }


def score_order_block(ob: dict, volume: float, reaction: float) -> float:
    """
    Score order block quality.
    """
    try:
        if not ob:
            return 0.0
        freshness = _clamp(ob.get("freshness", 0.5), 0.0, 1.0)
        imbalance = _clamp(ob.get("imbalance", ob.get("displacement", 0.5)), 0.0, 1.0)
        reaction_strength = _clamp(reaction, 0.0, 1.0)
        volume_confirmation = _clamp(volume, 0.0, 1.0)
        liq_proximity = _clamp(1.0 - _safe_float(ob.get("distance_to_liquidity", 0.0)) / max(_safe_float(ob.get("atr", 100.0)) * 4.0, 1.0), 0.0, 1.0)
        score = (
            freshness * 0.20
            + imbalance * 0.25
            + reaction_strength * 0.25
            + volume_confirmation * 0.20
            + liq_proximity * 0.10
        )
        return round(_clamp(score, 0.0, 1.0), 4)
    except Exception:
        return 0.0


def analyze_orderflow(trades: List[dict], orderbook: dict) -> Dict[str, Any]:
    """
    Delta, absorption, aggression, strength.
    """
    try:
        buy_usd, sell_usd = _volume_side(trades or [])
        total = buy_usd + sell_usd + 1e-9
        delta = (buy_usd - sell_usd) / total
        bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
        book_skew = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        aggression = "buy" if delta > 0 else "sell" if delta < 0 else "buy"
        absorption = False
        if delta < -0.15 and bid_vol > ask_vol * 1.1:
            absorption = True
        if delta > 0.15 and ask_vol > bid_vol * 1.1:
            absorption = True
        strength = _clamp(abs(delta) * 0.7 + abs(book_skew) * 0.3 + (0.15 if absorption else 0.0), 0.0, 1.0)
        return {
            "delta": round(float(delta), 4),
            "absorption": bool(absorption),
            "aggression": aggression,
            "strength": round(float(strength), 4),
            "buy_usd": round(float(buy_usd), 4),
            "sell_usd": round(float(sell_usd), 4),
            "book_skew": round(float(book_skew), 4),
        }
    except Exception:
        return {
            "delta": 0.0,
            "absorption": False,
            "aggression": "buy",
            "strength": 0.0,
            "buy_usd": 0.0,
            "sell_usd": 0.0,
            "book_skew": 0.0,
        }


def compute_mtf_bias(candles_by_tf: Any) -> Dict[str, Any]:
    """
    1H/4H trend + premium/discount, 5m/15m execution structure.
    """
    try:
        if isinstance(candles_by_tf, dict):
            c1h = _to_rows(candles_by_tf.get("1h") or candles_by_tf.get("1H") or candles_by_tf.get("60m") or [])
            c4h = _to_rows(candles_by_tf.get("4h") or candles_by_tf.get("4H") or [])
            c15 = _to_rows(candles_by_tf.get("15m") or [])
            c5 = _to_rows(candles_by_tf.get("5m") or [])
            c1 = _to_rows(candles_by_tf.get("1m") or candles_by_tf.get("primary") or [])
        else:
            c1 = _to_rows(candles_by_tf)
            c5 = _aggregate_ohlcv(c1, 5)
            c15 = _aggregate_ohlcv(c1, 15)
            c1h = _aggregate_ohlcv(c1, 60)
            c4h = _aggregate_ohlcv(c1, 240)

        def _trend(rows: List[list]) -> Tuple[str, float]:
            if len(rows) < 6:
                return "neutral", 0.0
            closes = [r[4] for r in rows]
            e1 = _ema(closes[-10:], min(10, len(closes[-10:])))
            e2 = _ema(closes[-20:], min(20, len(closes[-20:]))) if len(closes) >= 20 else e1
            slope = (closes[-1] - closes[-6]) / max(closes[-6], 1e-9)
            if e1 > e2 and slope > 0:
                return "bullish", min(1.0, abs(slope) * 25.0)
            if e1 < e2 and slope < 0:
                return "bearish", min(1.0, abs(slope) * 25.0)
            return "neutral", min(1.0, abs(slope) * 10.0)

        h1_trend, h1_conf = _trend(c1h)
        h4_trend, h4_conf = _trend(c4h if c4h else c1h)

        # premium/discount using 1H/4H range midpoint
        base_rows = c4h if len(c4h) >= 10 else c1h
        if not base_rows:
            base_rows = c15 if c15 else c5
        if base_rows:
            hi = max(r[2] for r in base_rows)
            lo = min(r[3] for r in base_rows)
            mid = (hi + lo) / 2.0
            last_price = c1[-1][4] if c1 else (c5[-1][4] if c5 else (c15[-1][4] if c15 else 0.0))
            htf_zone = "discount" if last_price < mid else "premium"
        else:
            htf_zone = "neutral"

        s15 = detect_structure(c15 if c15 else c5)
        s5 = detect_structure(c5 if c5 else c15)

        if s15.get("bos") == "bullish" or s5.get("bos") == "bullish":
            ltf_structure = "bos_up"
        elif s15.get("bos") == "bearish" or s5.get("bos") == "bearish":
            ltf_structure = "bos_down"
        else:
            ltf_structure = "range"

        alignment_score = 0.0
        if h1_trend != "neutral":
            alignment_score += 0.25
        if h4_trend != "neutral":
            alignment_score += 0.25
        if h1_trend == h4_trend and h1_trend != "neutral":
            alignment_score += 0.25
        if (htf_zone == "discount" and h1_trend == "bullish") or (htf_zone == "premium" and h1_trend == "bearish"):
            alignment_score += 0.10
        if (ltf_structure == "bos_up" and h1_trend == "bullish") or (ltf_structure == "bos_down" and h1_trend == "bearish"):
            alignment_score += 0.15

        return {
            "htf_trend": h1_trend if h1_trend == h4_trend else ("bullish" if h1_conf >= h4_conf and h1_trend != "neutral" else "bearish" if h4_trend != "neutral" else "neutral"),
            "htf_zone": htf_zone,
            "ltf_structure": ltf_structure,
            "alignment_score": round(_clamp(alignment_score, 0.0, 1.0), 4),
            "h1": {"trend": h1_trend, "confidence": round(h1_conf, 4)},
            "h4": {"trend": h4_trend, "confidence": round(h4_conf, 4)},
        }
    except Exception:
        return {
            "htf_trend": "neutral",
            "htf_zone": "neutral",
            "ltf_structure": "range",
            "alignment_score": 0.0,
            "h1": {"trend": "neutral", "confidence": 0.0},
            "h4": {"trend": "neutral", "confidence": 0.0},
        }


def detect_traps(
    orderbook: dict,
    trades: List[dict],
    candles: Any,
    volume: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Detect breakout trap, liquidity sweep, inducement trap.
    """
    try:
        rows = _to_rows(candles)
        if len(rows) < 5:
            return {"type": None, "strength": 0.0, "confirmation": False, "reasons": []}

        last = rows[-1]
        prev = rows[-2]
        recent = rows[-20:]
        highs = [r[2] for r in recent[:-1]] if len(recent) > 1 else [last[2]]
        lows = [r[3] for r in recent[:-1]] if len(recent) > 1 else [last[3]]
        recent_high = max(highs)
        recent_low = min(lows)
        atr = max(_atr(rows[-30:], 14), 1.0)
        body = _body_ratio(last)
        wick = _wick_stats(last)
        bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
        delta = 0.0
        if trades:
            buy, sell = _volume_side(trades[-100:])
            delta = (buy - sell) / (buy + sell + 1e-9)
        vol_strength = _safe_float((volume or {}).get("volume_strength", 0.0))
        volume_spike = bool((volume or {}).get("volume_spike") or (volume or {}).get("volume_explosion"))

        trap_type = None
        strength = 0.0
        reasons = []
        confirmation = False

        # Breakout trap (fake BOS)
        if last[4] > recent_high and prev[4] <= recent_high:
            if body < 0.45 or wick["upper"] > 0.35 or delta < 0.05:
                trap_type = "breakout"
                strength += 0.35
                reasons += ["fake_bos_above_range", "weak_follow_through", "rejection_wick"]

        if last[4] < recent_low and prev[4] >= recent_low:
            if body < 0.45 or wick["lower"] > 0.35 or delta > -0.05:
                trap_type = "breakout"
                strength += 0.35
                reasons += ["fake_bos_below_range", "weak_follow_through", "rejection_wick"]

        # Liquidity sweep
        swept_high = last[2] > recent_high + atr * 0.05 and last[4] < recent_high
        swept_low = last[3] < recent_low - atr * 0.05 and last[4] > recent_low
        if swept_high or swept_low:
            trap_type = "sweep"
            strength += 0.45
            reasons += ["liquidity_sweep", "reclaim_after_sweep"]

        # Inducement
        if volume_spike and abs(delta) < 0.12 and body < 0.55 and (swept_high or swept_low or last[4] > recent_high or last[4] < recent_low):
            if trap_type is None:
                trap_type = "inducement"
            strength += 0.30
            reasons += ["retail_lure_setup", "volume_without_direction"]

        # Orderbook / flow confirmation
        if trap_type == "sweep" and ((swept_high and delta < 0) or (swept_low and delta > 0)):
            confirmation = True
            strength += 0.20
            reasons += ["orderflow_reversal_confirmed"]
        if trap_type == "breakout" and volume_spike and body >= 0.5 and abs(delta) > 0.18:
            confirmation = True
            strength += 0.15
            reasons += ["volume_and_displacement"]
        if trap_type == "inducement" and abs(delta) > 0.15 and vol_strength >= 0.5:
            confirmation = True
            strength += 0.15
            reasons += ["flow_and_volume_alignment"]

        if bid_vol + ask_vol > 0:
            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            if trap_type in ("sweep", "breakout") and abs(imbalance) > 0.15:
                strength += 0.10
                reasons.append("book_imbalance")

        return {
            "type": trap_type,
            "strength": round(_clamp(strength, 0.0, 1.0), 4),
            "confirmation": bool(confirmation),
            "reasons": reasons,
            "swept_high": bool(swept_high),
            "swept_low": bool(swept_low),
            "delta": round(float(delta), 4),
            "body_ratio": round(float(body), 4),
        }
    except Exception:
        return {"type": None, "strength": 0.0, "confirmation": False, "reasons": []}


def compute_liquidity_magnet(liquidity_zones: List[dict], price: float) -> Dict[str, Any]:
    """
    Predicts target liquidity pool and probability.
    """
    try:
        price = _safe_float(price)
        if not liquidity_zones:
            return {"target_price": price, "distance": 0.0, "probability": 0.0}

        above = [z for z in liquidity_zones if _safe_float(z.get("price")) > price]
        below = [z for z in liquidity_zones if _safe_float(z.get("price")) < price]

        # Prefer the nearest strong zone on opposite side of current price.
        candidates = above if above else below
        if not candidates:
            candidates = liquidity_zones

        target = max(
            candidates,
            key=lambda z: (
                _safe_float(z.get("strength", 0.0)),
                -abs(_safe_float(z.get("price")) - price),
            ),
        )
        tprice = _safe_float(target.get("price", price))
        dist = abs(tprice - price)
        strength = _clamp(_safe_float(target.get("strength", 0.5)), 0.0, 1.0)
        probability = _clamp(strength * 0.7 + (1.0 - min(dist / max(price * 0.01, 1.0), 1.0)) * 0.3, 0.0, 1.0)
        return {
            "target_price": round(float(tprice), 2),
            "distance": round(float(dist), 2),
            "probability": round(float(probability), 4),
            "target_side": target.get("side"),
            "target_type": target.get("type"),
        }
    except Exception:
        return {"target_price": price, "distance": 0.0, "probability": 0.0}


def detect_market_regime(candles: Any, volatility: float) -> Dict[str, Any]:
    """
    trend | range | expansion | manipulation
    """
    try:
        rows = _to_rows(candles)
        if len(rows) < 8:
            return {"type": "range", "confidence": 0.0}

        closes = [r[4] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        recent = rows[-20:] if len(rows) >= 20 else rows
        recent_closes = [r[4] for r in recent]
        recent_high = max(r[2] for r in recent)
        recent_low = min(r[3] for r in recent)
        rng = max(recent_high - recent_low, 1e-9)
        atr = max(_atr(rows[-30:], 14), 1.0)
        compression = atr / rng
        slope = (closes[-1] - closes[-6]) / max(closes[-6], 1e-9) if len(closes) >= 6 else 0.0
        wickiness = _wick_stats(rows[-1])["upper"] + _wick_stats(rows[-1])["lower"]

        if volatility > 0.02 and abs(slope) > 0.01:
            rtype = "expansion"
            conf = min(1.0, 0.55 + volatility * 10.0)
        elif compression < 0.18 and abs(slope) < 0.006:
            rtype = "range"
            conf = min(1.0, 0.55 + (0.18 - compression) * 2.0)
        elif wickiness > 0.55 and abs(slope) < 0.008:
            rtype = "manipulation"
            conf = min(1.0, 0.60 + wickiness * 0.4)
        else:
            rtype = "trend"
            conf = min(1.0, 0.50 + min(abs(slope) * 20.0, 0.35) + min(volatility * 8.0, 0.15))

        return {"type": rtype, "confidence": round(float(_clamp(conf, 0.0, 1.0)), 4)}
    except Exception:
        return {"type": "range", "confidence": 0.0}


def compute_confluence_score(components: dict) -> float:
    """
    0..10 institutional confluence score.
    """
    try:
        smc = components.get("smc_signal")
        trap = components.get("trap")
        mtf = components.get("mtf_bias")
        orderflow = components.get("orderflow")
        liquidity = components.get("liquidity")
        volume = components.get("volume")
        regime = components.get("regime")
        fvg = components.get("fvg")

        def _norm(x: Any, default: float = 0.0) -> float:
            if isinstance(x, dict):
                for k in ("confidence", "strength", "score", "alignment_score", "probability"):
                    if k in x:
                        return _clamp(_safe_float(x.get(k), default), 0.0, 1.0)
                return default
            return _clamp(_safe_float(x, default), 0.0, 1.0)

        smc_v = _norm(smc)
        trap_v = _norm(trap)
        mtf_v = _norm(mtf)
        of_v = _norm(orderflow)
        liq_v = _norm(liquidity)
        vol_v = _norm(volume)
        reg_v = _norm(regime)
        fvg_v = _norm(fvg)

        score01 = (
            smc_v * 0.25
            + trap_v * 0.20
            + mtf_v * 0.20
            + of_v * 0.12
            + liq_v * 0.10
            + vol_v * 0.08
            + fvg_v * 0.03
            + reg_v * 0.02
        )
        return round(float(_clamp(score01 * 10.0, 0.0, 10.0)), 4)
    except Exception:
        return 0.0


class SMCLearningMemory:
    """
    Lightweight adaptive learning memory.
    Stores which setup features have historically won/lost.
    """

    def __init__(self, path: str = SMC_LEARNING_PATH):
        self.path = path
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception:
            return {"total": 0, "feature_stats": {}, "regime_stats": {}}

    def _save(self) -> None:
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def _key(features: Dict[str, Any]) -> str:
        regime = str(features.get("market_state", "unknown"))
        trap = str(features.get("trap_type", "none"))
        fib = "fib" if features.get("fib_in_zone") else "nofib"
        sweep = "sweep" if features.get("sweep") else "nosweep"
        return f"{regime}|{trap}|{fib}|{sweep}"

    def adjust(self, features: Dict[str, Any]) -> float:
        key = self._key(features)
        stats = self.data.get("feature_stats", {}).get(key, {"wins": 0, "losses": 0, "trades": 0})
        trades = max(1, int(stats.get("trades", 0)))
        winrate = _clamp(stats.get("wins", 0) / trades, 0.0, 1.0)
        return (winrate - 0.5) * 2.0

    def update(self, trade_result: str, features: Dict[str, Any]) -> None:
        key = self._key(features)
        stats = self.data.setdefault("feature_stats", {}).setdefault(
            key, {"wins": 0, "losses": 0, "trades": 0}
        )
        stats["trades"] += 1
        if str(trade_result).upper() in ("TP", "WIN", "PROFIT", "TAKE_PROFIT"):
            stats["wins"] += 1
        elif str(trade_result).upper() in ("SL", "LOSS", "STOP"):
            stats["losses"] += 1
        self.data["total"] = int(self.data.get("total", 0)) + 1
        self._save()


_LEARNING = SMCLearningMemory()


def update_model_weights(trade_outcome: dict):
    """
    Update adaptive memory from a finished trade.
    """
    try:
        features = trade_outcome.get("features", {}) or {}
        result = trade_outcome.get("result", trade_outcome.get("outcome", ""))
        _LEARNING.update(result, features)
        return {
            "updated": True,
            "total": _LEARNING.data.get("total", 0),
            "key": SMCLearningMemory._key(features),
        }
    except Exception:
        return {"updated": False}


def update_learning_memory(trade_result: dict):
    return update_model_weights(trade_result)


def score_setup(
    structure: Dict[str, Any],
    liquidity: Dict[str, Any],
    trap: Dict[str, Any],
    fib_zone: Dict[str, Any],
    volume_intel: Optional[Dict[str, Any]],
    market_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    features = {
        "market_state": str((market_state or {}).get("state", "unknown")),
        "trap_type": trap.get("trap_type") or "none",
        "fib_in_zone": bool(fib_zone.get("in_zone")),
        "sweep": bool(trap.get("trap")),
    }

    base = 0.0
    reasons = []

    if structure.get("trend") in ("bullish", "bearish"):
        base += 2.0
        reasons.append("trend_aligned")
    if structure.get("bos") or structure.get("choch"):
        base += 2.0
        reasons.append("structure_shift")
    if trap.get("trap"):
        base += 2.0
        reasons.append("trap_detected")
    if fib_zone.get("in_zone") and fib_zone.get("rejected"):
        base += 2.0
        reasons.append("fib_rejection")
    if volume_intel and (volume_intel.get("volume_spike") or volume_intel.get("volume_explosion")):
        base += 1.0
        reasons.append("volume_support")
    if market_state and str(market_state.get("state", "")).upper() not in ("CHOPPY", "RANGING"):
        base += 1.0
        reasons.append("market_ok")

    learning_boost = _LEARNING.adjust(features)
    adjusted = _clamp(base + learning_boost, 0.0, 10.0)

    return {
        "score": int(round(adjusted)),
        "raw_score": round(base, 2),
        "learning_boost": round(learning_boost, 3),
        "reasons": reasons,
        "features": features,
    }


def build_risk_plan(
    entry: float,
    direction: str,
    structure: Dict[str, Any],
    trap: Dict[str, Any],
    liquidity: Dict[str, Any],
    fib_zone: Dict[str, Any],
    candles_1m: Any = None,
) -> Dict[str, Any]:
    rows = _to_rows(candles_1m)
    atr = max(_atr(rows[-30:], 14), _safe_float(entry) * 0.0006, 25.0)
    buffer = max(atr * 0.20, 15.0)

    if direction == "LONG":
        invalidation = min(
            [
                x
                for x in [
                    trap.get("last_low"),
                    structure.get("last_swing_low"),
                    fib_zone.get("low"),
                ]
                if x is not None
            ],
            default=entry - atr,
        ) - buffer

        next_targets = [
            z["price"] for z in liquidity.get("buy_side_liquidity", []) if z.get("price") and z["price"] > entry
        ]
        tp1 = min(next_targets) if next_targets else entry + (entry - invalidation) * 2.0
        tp2 = max(tp1 + (entry - invalidation) * 1.5, entry + (entry - invalidation) * 3.0)
    else:
        invalidation = max(
            [
                x
                for x in [
                    trap.get("last_high"),
                    structure.get("last_swing_high"),
                    fib_zone.get("high"),
                ]
                if x is not None
            ],
            default=entry + atr,
        ) + buffer

        next_targets = [
            z["price"] for z in liquidity.get("sell_side_liquidity", []) if z.get("price") and z["price"] < entry
        ]
        tp1 = max(next_targets) if next_targets else entry - (invalidation - entry) * 2.0
        tp2 = min(tp1 - (invalidation - entry) * 1.5, entry - (invalidation - entry) * 3.0)

    risk = abs(entry - invalidation)
    reward = abs(tp2 - entry)
    rr = reward / risk if risk > 0 else 0.0

    return {
        "entry": round(float(entry), 2),
        "sl": round(float(invalidation), 2),
        "tp": [round(float(tp1), 2), round(float(tp2), 2)],
        "rr": round(float(rr), 2),
        "invalidations": [round(float(invalidation), 2)],
        "risk_points": round(float(risk), 2),
    }


def detect_fibonacci_zone(candles_15m: Any, price: float, direction: str) -> Dict[str, Any]:
    rows = _to_rows(candles_15m)
    price = _safe_float(price)
    if len(rows) < 10:
        return {
            "timeframe": "15m",
            "direction": direction,
            "anchor_low": None,
            "anchor_high": None,
            "low": None,
            "high": None,
            "in_zone": False,
            "rejected": False,
            "held": False,
            "confluence": 0.0,
        }

    piv = _pivot_swings(rows, 2, 2)
    highs = piv["highs"]
    lows = piv["lows"]
    last = rows[-1]
    body = _body_ratio(last)
    wick = _wick_stats(last)
    atr = max(_atr(rows[-30:], 14), price * 0.0005)

    anchor_low = None
    anchor_high = None

    if direction == "LONG":
        low_candidates = [l for l in lows if l["price"] < price]
        high_candidates = [h for h in highs if h["price"] > (low_candidates[-1]["price"] if low_candidates else -1)]
        if low_candidates and high_candidates:
            anchor_low = low_candidates[-1]["price"]
            anchor_high = high_candidates[-1]["price"]
        elif lows and highs:
            anchor_low = lows[-1]["price"]
            anchor_high = highs[-1]["price"]
    elif direction == "SHORT":
        high_candidates = [h for h in highs if h["price"] > price]
        low_candidates = [l for l in lows if l["price"] < (high_candidates[-1]["price"] if high_candidates else price)]
        if high_candidates and low_candidates:
            anchor_high = high_candidates[-1]["price"]
            anchor_low = low_candidates[-1]["price"]
        elif highs and lows:
            anchor_high = highs[-1]["price"]
            anchor_low = lows[-1]["price"]

    if anchor_low is None or anchor_high is None or anchor_high <= anchor_low:
        return {
            "timeframe": "15m",
            "direction": direction,
            "anchor_low": anchor_low,
            "anchor_high": anchor_high,
            "low": None,
            "high": None,
            "in_zone": False,
            "rejected": False,
            "held": False,
            "confluence": 0.0,
        }

    leg = anchor_high - anchor_low
    if direction == "LONG":
        z_high = anchor_high - leg * 0.618
        z_low = anchor_high - leg * 0.705
        zone_low, zone_high = min(z_low, z_high), max(z_low, z_high)
    else:
        z_low = anchor_low + leg * 0.618
        z_high = anchor_low + leg * 0.705
        zone_low, zone_high = min(z_low, z_high), max(z_low, z_high)

    in_zone = zone_low <= price <= zone_high or (last[3] <= zone_high and last[2] >= zone_low)
    held = False
    rejected = False
    if in_zone:
        if direction == "LONG":
            held = last[4] >= zone_low and (body >= 0.45 or last[4] > last[1])
            rejected = (wick["lower"] > wick["upper"]) and last[4] > zone_high
        else:
            held = last[4] <= zone_high and (body >= 0.45 or last[4] < last[1])
            rejected = (wick["upper"] > wick["lower"]) and last[4] < zone_low

    confluence = 0.0
    confluence += 0.35 if in_zone else 0.0
    confluence += 0.35 if rejected else 0.0
    confluence += 0.15 if held else 0.0
    confluence += 0.15 if atr > 0 else 0.0

    return {
        "timeframe": "15m",
        "direction": direction,
        "anchor_low": round(float(anchor_low), 2),
        "anchor_high": round(float(anchor_high), 2),
        "low": round(float(zone_low), 2),
        "high": round(float(zone_high), 2),
        "in_zone": bool(in_zone),
        "rejected": bool(rejected),
        "held": bool(held),
        "confluence": round(_clamp(confluence, 0.0, 1.0), 4),
    }


def detect_trap(
    candles_1m: Any,
    price: float,
    structure: Dict[str, Any],
    liquidity: Dict[str, Any],
    fib_zone: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rows = _to_rows(candles_1m)
    if len(rows) < 4:
        return {"trap": False, "trap_type": None, "direction": "NONE", "confidence": 0.0, "reasons": []}

    last = rows[-1]
    prev = rows[-2]
    last_close = last[4]
    last_high = last[2]
    last_low = last[3]
    body = _body_ratio(last)
    wick = _wick_stats(last)
    atr = max(_atr(rows[-30:], 14), price * 0.0005)

    reasons = []
    trap = False
    trap_type = None
    direction = "NONE"
    sweep_level = None

    buy_liq_levels = [z["price"] for z in liquidity.get("buy_side_liquidity", []) if z.get("price")]
    sell_liq_levels = [z["price"] for z in liquidity.get("sell_side_liquidity", []) if z.get("price")]

    nearest_buy = max(buy_liq_levels) if buy_liq_levels else None
    nearest_sell = min(sell_liq_levels) if sell_liq_levels else None

    if nearest_buy is not None and last_high > nearest_buy + atr * 0.05 and last_close < nearest_buy:
        if body >= 0.35 and wick["upper"] > wick["lower"]:
            trap = True
            trap_type = "BUY_SIDE_SWEEP_FAIL"
            direction = "SHORT"
            sweep_level = nearest_buy
            reasons += ["ran_buy_side_liquidity", "failed_acceptance_above_high", "reclaimed_below_level"]

    if nearest_sell is not None and last_low < nearest_sell - atr * 0.05 and last_close > nearest_sell:
        if body >= 0.35 and wick["lower"] > wick["upper"]:
            trap = True
            trap_type = "SELL_SIDE_SWEEP_FAIL"
            direction = "LONG"
            sweep_level = nearest_sell
            reasons += ["ran_sell_side_liquidity", "failed_acceptance_below_low", "reclaimed_above_level"]

    if structure.get("bos") == "bullish" and direction == "LONG":
        reasons.append("bos_bullish")
    if structure.get("bos") == "bearish" and direction == "SHORT":
        reasons.append("bos_bearish")
    if structure.get("choch") == "bullish" and direction == "LONG":
        reasons.append("choch_bullish")
    if structure.get("choch") == "bearish" and direction == "SHORT":
        reasons.append("choch_bearish")

    if fib_zone and fib_zone.get("in_zone"):
        reasons.append("fib_zone_alignment")
        if fib_zone.get("rejected"):
            reasons.append("fib_rejection_confirmed")

    confidence = 0.0
    confidence += 0.40 if trap else 0.0
    confidence += 0.25 if trap_type else 0.0
    confidence += 0.20 if fib_zone and fib_zone.get("in_zone") else 0.0
    confidence += 0.15 if body >= 0.45 else 0.0

    return {
        "trap": bool(trap),
        "trap_type": trap_type,
        "direction": direction,
        "sweep_level": sweep_level,
        "confidence": round(_clamp(confidence, 0.0, 1.0), 4),
        "reasons": reasons,
        "last_high": round(float(last_high), 2),
        "last_low": round(float(last_low), 2),
        "last_close": round(float(last_close), 2),
    }


def compute_mtf_bias(candles_by_tf: Any) -> Dict[str, Any]:
    try:
        if isinstance(candles_by_tf, dict):
            c1h = _to_rows(candles_by_tf.get("1h") or candles_by_tf.get("1H") or candles_by_tf.get("60m") or [])
            c4h = _to_rows(candles_by_tf.get("4h") or candles_by_tf.get("4H") or [])
            c15 = _to_rows(candles_by_tf.get("15m") or [])
            c5 = _to_rows(candles_by_tf.get("5m") or [])
            c1 = _to_rows(candles_by_tf.get("1m") or candles_by_tf.get("primary") or [])
        else:
            c1 = _to_rows(candles_by_tf)
            c5 = _aggregate_ohlcv(c1, 5)
            c15 = _aggregate_ohlcv(c1, 15)
            c1h = _aggregate_ohlcv(c1, 60)
            c4h = _aggregate_ohlcv(c1, 240)

        def _trend(rows: List[list]) -> Tuple[str, float]:
            if len(rows) < 6:
                return "neutral", 0.0
            closes = [r[4] for r in rows]
            e1 = _ema(closes[-10:], min(10, len(closes[-10:])))
            e2 = _ema(closes[-20:], min(20, len(closes[-20:]))) if len(closes) >= 20 else e1
            slope = (closes[-1] - closes[-6]) / max(closes[-6], 1e-9)
            if e1 > e2 and slope > 0:
                return "bullish", min(1.0, abs(slope) * 25.0)
            if e1 < e2 and slope < 0:
                return "bearish", min(1.0, abs(slope) * 25.0)
            return "neutral", min(1.0, abs(slope) * 10.0)

        h1_trend, h1_conf = _trend(c1h)
        h4_trend, h4_conf = _trend(c4h if c4h else c1h)

        base_rows = c4h if len(c4h) >= 10 else c1h
        if not base_rows:
            base_rows = c15 if c15 else c5
        if base_rows:
            hi = max(r[2] for r in base_rows)
            lo = min(r[3] for r in base_rows)
            mid = (hi + lo) / 2.0
            last_price = c1[-1][4] if c1 else (c5[-1][4] if c5 else (c15[-1][4] if c15 else 0.0))
            htf_zone = "discount" if last_price < mid else "premium"
        else:
            htf_zone = "neutral"

        s15 = detect_structure(c15 if c15 else c5)
        s5 = detect_structure(c5 if c5 else c15)

        if s15.get("bos") == "bullish" or s5.get("bos") == "bullish":
            ltf_structure = "bos_up"
        elif s15.get("bos") == "bearish" or s5.get("bos") == "bearish":
            ltf_structure = "bos_down"
        else:
            ltf_structure = "range"

        alignment_score = 0.0
        if h1_trend != "neutral":
            alignment_score += 0.25
        if h4_trend != "neutral":
            alignment_score += 0.25
        if h1_trend == h4_trend and h1_trend != "neutral":
            alignment_score += 0.25
        if (htf_zone == "discount" and h1_trend == "bullish") or (htf_zone == "premium" and h1_trend == "bearish"):
            alignment_score += 0.10
        if (ltf_structure == "bos_up" and h1_trend == "bullish") or (ltf_structure == "bos_down" and h1_trend == "bearish"):
            alignment_score += 0.15

        return {
            "htf_trend": h1_trend
            if h1_trend == h4_trend
            else ("bullish" if h1_conf >= h4_conf and h1_trend != "neutral" else "bearish" if h4_trend != "neutral" else "neutral"),
            "htf_zone": htf_zone,
            "ltf_structure": ltf_structure,
            "alignment_score": round(_clamp(alignment_score, 0.0, 1.0), 4),
            "h1": {"trend": h1_trend, "confidence": round(h1_conf, 4)},
            "h4": {"trend": h4_trend, "confidence": round(h4_conf, 4)},
        }
    except Exception:
        return {
            "htf_trend": "neutral",
            "htf_zone": "neutral",
            "ltf_structure": "range",
            "alignment_score": 0.0,
            "h1": {"trend": "neutral", "confidence": 0.0},
            "h4": {"trend": "neutral", "confidence": 0.0},
        }


def analyze_orderflow(trades: List[dict], orderbook: dict) -> Dict[str, Any]:
    try:
        buy_usd, sell_usd = _volume_side(trades or [])
        total = buy_usd + sell_usd + 1e-9
        delta = (buy_usd - sell_usd) / total
        bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
        book_skew = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        aggression = "buy" if delta > 0 else "sell" if delta < 0 else "buy"
        absorption = False
        if delta < -0.15 and bid_vol > ask_vol * 1.1:
            absorption = True
        if delta > 0.15 and ask_vol > bid_vol * 1.1:
            absorption = True
        strength = _clamp(abs(delta) * 0.7 + abs(book_skew) * 0.3 + (0.15 if absorption else 0.0), 0.0, 1.0)
        return {
            "delta": round(float(delta), 4),
            "absorption": bool(absorption),
            "aggression": aggression,
            "strength": round(float(strength), 4),
            "buy_usd": round(float(buy_usd), 4),
            "sell_usd": round(float(sell_usd), 4),
            "book_skew": round(float(book_skew), 4),
        }
    except Exception:
        return {
            "delta": 0.0,
            "absorption": False,
            "aggression": "buy",
            "strength": 0.0,
            "buy_usd": 0.0,
            "sell_usd": 0.0,
            "book_skew": 0.0,
        }


def detect_market_regime(candles: Any, volatility: float) -> Dict[str, Any]:
    try:
        rows = _to_rows(candles)
        if len(rows) < 8:
            return {"type": "range", "confidence": 0.0}

        closes = [r[4] for r in rows]
        recent = rows[-20:] if len(rows) >= 20 else rows
        recent_high = max(r[2] for r in recent)
        recent_low = min(r[3] for r in recent)
        rng = max(recent_high - recent_low, 1e-9)
        atr = max(_atr(rows[-30:], 14), 1.0)
        compression = atr / rng
        slope = (closes[-1] - closes[-6]) / max(closes[-6], 1e-9) if len(closes) >= 6 else 0.0
        wickiness = _wick_stats(rows[-1])["upper"] + _wick_stats(rows[-1])["lower"]
        sweep_like = False
        if len(rows) >= 2 and (rows[-1][2] > recent_high or rows[-1][3] < recent_low) and _body_ratio(rows[-1]) < 0.45:
            sweep_like = True

        if volatility > 0.02 and abs(slope) > 0.01:
            rtype = "expansion"
            conf = min(1.0, 0.55 + volatility * 10.0)
        elif compression < 0.18 and abs(slope) < 0.006:
            rtype = "range"
            conf = min(1.0, 0.55 + (0.18 - compression) * 2.0)
        elif sweep_like or (wickiness > 0.55 and abs(slope) < 0.008):
            rtype = "manipulation"
            conf = min(1.0, 0.60 + wickiness * 0.4)
        else:
            rtype = "trend"
            conf = min(1.0, 0.50 + min(abs(slope) * 20.0, 0.35) + min(volatility * 8.0, 0.15))

        return {"type": rtype, "confidence": round(float(_clamp(conf, 0.0, 1.0)), 4)}
    except Exception:
        return {"type": "range", "confidence": 0.0}


def compute_confluence_score(components: dict) -> float:
    try:
        smc = components.get("smc_signal")
        trap = components.get("trap")
        mtf = components.get("mtf_bias")
        orderflow = components.get("orderflow")
        liquidity = components.get("liquidity")
        volume = components.get("volume")
        regime = components.get("regime")
        fvg = components.get("fvg")

        def _norm(x: Any, default: float = 0.0) -> float:
            if isinstance(x, dict):
                for k in ("confidence", "strength", "score", "alignment_score", "probability"):
                    if k in x:
                        return _clamp(_safe_float(x.get(k), default), 0.0, 1.0)
                return default
            return _clamp(_safe_float(x, default), 0.0, 1.0)

        smc_v = _norm(smc)
        trap_v = _norm(trap)
        mtf_v = _norm(mtf)
        of_v = _norm(orderflow)
        liq_v = _norm(liquidity)
        vol_v = _norm(volume)
        reg_v = _norm(regime)
        fvg_v = _norm(fvg)

        score01 = (
            smc_v * 0.25
            + trap_v * 0.20
            + mtf_v * 0.20
            + of_v * 0.12
            + liq_v * 0.10
            + vol_v * 0.08
            + fvg_v * 0.03
            + reg_v * 0.02
        )
        return round(float(_clamp(score01 * 10.0, 0.0, 10.0)), 4)
    except Exception:
        return 0.0


def score_setup(
    structure: Dict[str, Any],
    liquidity: Dict[str, Any],
    trap: Dict[str, Any],
    fib_zone: Dict[str, Any],
    volume_intel: Optional[Dict[str, Any]],
    market_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    features = {
        "market_state": str((market_state or {}).get("state", "unknown")),
        "trap_type": trap.get("trap_type") or "none",
        "fib_in_zone": bool(fib_zone.get("in_zone")),
        "sweep": bool(trap.get("trap")),
    }

    base = 0.0
    reasons = []

    if structure.get("trend") in ("bullish", "bearish"):
        base += 2.0
        reasons.append("trend_aligned")
    if structure.get("bos") or structure.get("choch"):
        base += 2.0
        reasons.append("structure_shift")
    if trap.get("trap"):
        base += 2.0
        reasons.append("trap_detected")
    if fib_zone.get("in_zone") and fib_zone.get("rejected"):
        base += 2.0
        reasons.append("fib_rejection")
    if volume_intel and (volume_intel.get("volume_spike") or volume_intel.get("volume_explosion")):
        base += 1.0
        reasons.append("volume_support")
    if market_state and str(market_state.get("state", "")).upper() not in ("CHOPPY", "RANGING"):
        base += 1.0
        reasons.append("market_ok")

    learning_boost = _LEARNING.adjust(features)
    adjusted = _clamp(base + learning_boost, 0.0, 10.0)

    return {
        "score": int(round(adjusted)),
        "raw_score": round(base, 2),
        "learning_boost": round(learning_boost, 3),
        "reasons": reasons,
        "features": features,
    }


# DEPRECATED (moved to signal_engine) — kept for backward compatibility
def evaluate_smc_sniper(
    candles_by_tf: Any,
    orderbook: dict,
    trades: List[dict],
    price: float,
    volume_intel: Optional[Dict[str, Any]] = None,
    market_state: Optional[Dict[str, Any]] = None,
    engines_out: Optional[Dict[str, Any]] = None,
    use_learning: bool = True,
) -> Dict[str, Any]:
    engines_out = engines_out or {}
    market_state = market_state or {}

    if isinstance(candles_by_tf, dict):
        c1m = _to_rows(candles_by_tf.get("1m") or candles_by_tf.get("primary") or [])
        c5m = _to_rows(candles_by_tf.get("5m") or [])
        c15m = _to_rows(candles_by_tf.get("15m") or [])
        c1h = _to_rows(candles_by_tf.get("1h") or [])
    else:
        c1m = _to_rows(candles_by_tf)
        c5m = _aggregate_rows(c1m, 5)
        c15m = _aggregate_rows(c1m, 15)
        c1h = _aggregate_rows(c1m, 60)

    if len(c15m) < 10 or len(c1m) < 8:
        return {
            "signal": "NONE",
            "entry": None,
            "sl": None,
            "tp": [],
            "reason": "not_enough_data",
            "confidence": 0,
            "market_state": str(market_state.get("state", "unknown")),
            "trap_type": None,
            "fib_zone": {"low": None, "high": None, "timeframe": "15m"},
            "invalidations": [],
            "features": {},
        }

    structure_15 = detect_structure(c15m)
    liquidity_15 = detect_liquidity(c15m, price)
    mtf = compute_mtf_bias(candles_by_tf)
    liquidity_intent = analyze_liquidity_intent(orderbook, trades, candles_by_tf)

    direction = "NONE"
    if mtf.get("htf_trend") == "bullish" or structure_15.get("trend") == "bullish":
        direction = "LONG"
    elif mtf.get("htf_trend") == "bearish" or structure_15.get("trend") == "bearish":
        direction = "SHORT"

    trap = detect_traps(orderbook, trades, c1m, volume_intel)
    if trap.get("type") and trap.get("direction") in ("LONG", "SHORT"):
        direction = trap["direction"]

    fib = detect_fibonacci_zone(c15m, price, direction if direction != "NONE" else ("LONG" if structure_15.get("trend") == "bullish" else "SHORT"))
    trap = detect_traps(orderbook, trades, c1m, volume_intel)

    fvg_15 = detect_fvg(c15m)
    fvg_5 = detect_fvg(c5m)
    fvg = fvg_15 if fvg_15.get("exists") else fvg_5

    orderflow = analyze_orderflow(trades, orderbook)
    regime = detect_market_regime(c15m if c15m else c1m, _estimate_volatility_from_ohlcv(c1m))
    liquidity_magnet = compute_liquidity_magnet(liquidity_intent.get("liquidity_zones", []), price)

    # Order block proxy from last opposite candle before impulse
    ob = {}
    if len(c1m) >= 4:
        base_candle = c1m[-2] if direction == "LONG" else c1m[-2]
        ob = {
            "freshness": 1.0,
            "imbalance": 1.0 if _body_ratio(base_candle) >= 0.55 else 0.6,
            "distance_to_liquidity": liquidity_intent.get("nearest_above", {}).get("distance", 0.0)
            if direction == "LONG"
            else liquidity_intent.get("nearest_below", {}).get("distance", 0.0),
            "atr": _atr(c1m[-30:], 14),
        }
    ob_score = score_order_block(ob, volume=_safe_float(volume_intel.get("volume_strength", 0.0) if volume_intel else 0.0), reaction=_safe_float(fvg.get("strength", 0.0)))

    confluence = compute_confluence_score(
        {
            "smc_signal": structure_15.get("confidence", 0.0),
            "trap": trap.get("strength", 0.0),
            "mtf_bias": mtf.get("alignment_score", 0.0),
            "orderflow": orderflow.get("strength", 0.0),
            "liquidity": liquidity_magnet.get("probability", 0.0),
            "volume": volume_intel.get("volume_strength", 0.0) if volume_intel else 0.0,
            "regime": regime.get("confidence", 0.0),
            "fvg": fvg.get("strength", 0.0),
        }
    )

    # Block weak / misaligned setups.
    if mtf.get("alignment_score", 0.0) < 0.45 and not (trap.get("trap") and trap.get("strength", 0.0) >= 0.65):
        return {
            "signal": "NONE",
            "entry": None,
            "sl": None,
            "tp": [],
            "reason": "mtf_misaligned",
            "confidence": int(round(confluence)),
            "market_state": regime.get("type", "unknown"),
            "trap_type": trap.get("type"),
            "fib_zone": fib,
            "invalidations": [],
            "features": {
                "mtf_bias": mtf,
                "trap": trap,
                "orderflow": orderflow,
                "liquidity": liquidity_intent,
                "fvg": fvg,
                "regime": regime,
                "liquidity_magnet": liquidity_magnet,
                "order_block_score": ob_score,
                "confluence": confluence,
            },
        }

    if regime.get("type") == "manipulation" and confluence < 7:
        return {
            "signal": "NONE",
            "entry": None,
            "sl": None,
            "tp": [],
            "reason": "manipulation_regime_filtered",
            "confidence": int(round(confluence)),
            "market_state": regime.get("type", "unknown"),
            "trap_type": trap.get("type"),
            "fib_zone": fib,
            "invalidations": [],
            "features": {
                "mtf_bias": mtf,
                "trap": trap,
                "orderflow": orderflow,
                "liquidity": liquidity_intent,
                "fvg": fvg,
                "regime": regime,
                "liquidity_magnet": liquidity_magnet,
                "order_block_score": ob_score,
                "confluence": confluence,
            },
        }

    # Signal decision
    signal = "NONE"
    reason_parts = []

    # trap wins if aligned
    if trap.get("type") in ("sweep", "breakout", "inducement") and trap.get("direction") in ("LONG", "SHORT"):
        if fib.get("in_zone") and (fib.get("rejected") or fib.get("held")):
            signal = trap["direction"]
            reason_parts += [trap.get("type"), "fib_zone", "trap_reclaim"]
    if signal == "NONE":
        if structure_15.get("trend") in ("bullish", "bearish") and fib.get("in_zone") and (fib.get("rejected") or fib.get("held")):
            signal = "LONG" if structure_15.get("trend") == "bullish" else "SHORT"
            reason_parts += ["structure", "fib_golden_zone"]
    if signal == "NONE":
        if fvg.get("exists") and not fvg.get("filled") and direction in ("LONG", "SHORT"):
            signal = direction
            reason_parts += ["fvg_confluence"]
    if signal == "NONE" and confluence >= 7:
        signal = "LONG" if mtf.get("htf_trend") == "bullish" else "SHORT" if mtf.get("htf_trend") == "bearish" else "NONE"

    if signal == "NONE":
        return {
            "signal": "NONE",
            "entry": None,
            "sl": None,
            "tp": [],
            "reason": "no_high_quality_setup",
            "confidence": int(round(confluence)),
            "market_state": regime.get("type", structure_15.get("state", "unknown")),
            "trap_type": trap.get("type"),
            "fib_zone": fib,
            "invalidations": [],
            "features": {
                "mtf_bias": mtf,
                "trap": trap,
                "orderflow": orderflow,
                "liquidity": liquidity_intent,
                "fvg": fvg,
                "regime": regime,
                "liquidity_magnet": liquidity_magnet,
                "order_block_score": ob_score,
                "confluence": confluence,
            },
        }

    entry = price if fib.get("in_zone") else _safe_float(fib.get("high") if signal == "LONG" else fib.get("low"), price)
    risk_plan = build_risk_plan(entry, signal, structure_15, trap, liquidity_15, fib, candles_1m=c1m)

    magnet_target = _safe_float(liquidity_magnet.get("target_price", 0.0), 0.0)
    if magnet_target <= 0:
        magnet_target = risk_plan["tp"][1] if risk_plan.get("tp") else entry + (abs(entry - risk_plan["sl"]) * 2.0 if signal == "LONG" else -abs(entry - risk_plan["sl"]) * 2.0)

    if signal == "LONG":
        tp1 = magnet_target if magnet_target > entry else risk_plan["tp"][0]
        tp2 = max(tp1, risk_plan["tp"][1] if len(risk_plan.get("tp", [])) > 1 else tp1)
    else:
        tp1 = magnet_target if magnet_target < entry else risk_plan["tp"][0]
        tp2 = min(tp1, risk_plan["tp"][1] if len(risk_plan.get("tp", [])) > 1 else tp1)

    confidence = int(
        _clamp(
            round(confluence)
            + (1 if fib.get("rejected") else 0)
            + (1 if trap.get("trap") else 0)
            + (1 if orderflow.get("absorption") else 0),
            1,
            10,
        )
    )

    features = {
        "mtf_bias": mtf,
        "trap": trap,
        "orderflow": orderflow,
        "liquidity": liquidity_intent,
        "fvg": fvg,
        "regime": regime,
        "liquidity_magnet": liquidity_magnet,
        "order_block_score": ob_score,
        "confluence": confluence,
        "structure": structure_15,
        "fib_in_zone": fib.get("in_zone"),
        "fib_rejected": fib.get("rejected"),
    }

    return {
        "signal": signal,
        "entry": round(float(entry), 2) if entry else None,
        "sl": risk_plan["sl"],
        "tp": [round(float(tp1), 2), round(float(tp2), 2)],
        "reason": " + ".join(
            [x for x in [
                "trap" if trap.get("trap") else None,
                trap.get("type"),
                structure_15.get("bos"),
                structure_15.get("choch"),
                "fib_golden_zone",
                "fvg" if fvg.get("exists") else None,
                "orderflow" if orderflow.get("strength", 0.0) > 0.4 else None,
            ] if x]
        )
        or "smc_setup",
        "confidence": confidence,
        "market_state": regime.get("type", structure_15.get("state", "unknown")),
        "trap_type": trap.get("type"),
        "fib_zone": {
            "low": fib.get("low"),
            "high": fib.get("high"),
            "timeframe": "15m",
            "anchor_low": fib.get("anchor_low"),
            "anchor_high": fib.get("anchor_high"),
        },
        "invalidations": risk_plan.get("invalidations", []),
        "features": features,
        "entry_type": "fib_retest" if fib.get("in_zone") else "structure_retest",
    }


# DEPRECATED (moved to signal_engine) — kept for backward compatibility
def detect_entry_trigger(price, liquidity_map, engines, ai_score, confidence, volume_intel=None):
    try:
        smc = (engines or {}).get("smc_signal") or {}
        if smc.get("signal") in ("LONG", "SHORT") and _safe_float(smc.get("confidence", 0)) >= 8:
            return {
                "trigger": True,
                "reason": f"smc_{smc.get('signal').lower()}",
                "confidence": _safe_float(smc.get("confidence", 0)) / 10.0,
            }

        market_state = (engines or {}).get("market_state", {}) or {}
        if str(market_state.get("state", "CHOPPY")).upper() == "CHOPPY":
            return {"trigger": False, "reason": "choppy_market", "confidence": 0.0}

        sweep = bool((engines.get("liquidity_sweep") or {}).get("sweep", False))
        stop_hunt = bool((engines.get("stop_hunt") or {}).get("stop_hunt", False))
        smart = bool(engines.get("smart_money_detected", False) or (engines.get("absorption") or {}).get("absorption", False))
        vol_ok = bool(volume_intel and (volume_intel.get("volume_explosion") or (volume_intel.get("volume_spike") and volume_intel.get("mtf_confirmation"))))
        if not (sweep or stop_hunt or smart or vol_ok):
            return {"trigger": False, "reason": "no_liquidity_event", "confidence": 0.0}

        zones = (liquidity_map or {}).get("liquidity_map") if isinstance(liquidity_map, dict) else liquidity_map
        zones = zones or []
        if not zones:
            return {"trigger": False, "reason": "no_liquidity_zones", "confidence": 0.0}

        nearest = min(
            [z for z in zones if isinstance(z, dict) and _safe_float(z.get("price", 0.0)) > 0],
            key=lambda z: abs(_safe_float(z.get("price")) - _safe_float(price)),
            default=None,
        )
        if not nearest:
            return {"trigger": False, "reason": "no_nearest_zone", "confidence": 0.0}

        dist = abs(_safe_float(price) - _safe_float(nearest.get("price")))
        window_ok = 150.0 <= dist <= 450.0
        strong_signal = abs(_safe_float(ai_score)) >= 0.25 and _safe_float(confidence) >= 0.55
        trigger = bool(window_ok and strong_signal)

        return {
            "trigger": trigger,
            "reason": f"window_ok={window_ok}, sweep={sweep}, stop_hunt={stop_hunt}, smart={smart}, vol_ok={vol_ok}",
            "confidence": _safe_float(confidence, 0.0),
        }
    except Exception as e:
        logger.error("Sniper trigger error: %s", e)
        return {"trigger": False, "reason": "error", "confidence": 0.0}


# DEPRECATED (moved to signal_engine) — EXECUTION MOVED TO execution_logic.py
def build_trade_plan(price, direction, liquidity_map):
    try:
        price = _safe_float(price, 0.0)
        zones = (liquidity_map or {}).get("liquidity_map") if isinstance(liquidity_map, dict) else liquidity_map
        zones = zones or []

        values = []
        for z in zones:
            if isinstance(z, dict):
                p = _safe_float(z.get("price"))
                if p > 0:
                    values.append(p)
            else:
                p = _safe_float(z)
                if p > 0:
                    values.append(p)

        if not values:
            return None

        below = sorted([v for v in values if v < price])
        above = sorted([v for v in values if v > price])

        if direction == "LONG":
            entry = price
            sl = (below[-1] - 20.0) if below else entry - 90.0
            sl = min(sl, entry - 50.0)
            tp1 = entry + 150.0
            tp2 = entry + 220.0
            tp3 = entry + 300.0
        elif direction == "SHORT":
            entry = price
            sl = (above[0] + 20.0) if above else entry + 90.0
            sl = max(sl, entry + 50.0)
            tp1 = entry - 150.0
            tp2 = entry - 220.0
            tp3 = entry - 300.0
        else:
            return None

        risk = abs(entry - sl)
        rr = abs(tp3 - entry) / risk if risk > 0 else 0.0
        return {
            "entry": round(float(entry), 2),
            "sl": round(float(sl), 2),
            "tp": [round(float(tp1), 2), round(float(tp2), 2), round(float(tp3), 2)],
            "rr": round(float(rr), 2),
        }
    except Exception as e:
        logger.error("Trade plan error: %s", e)
        return None


def run_all_engines(
    orderbook: Optional[dict] = None,
    trades: Optional[List[dict]] = None,
    price: Optional[float] = None,
    exchange: Any = None,
    symbol: Optional[str] = None,
    cascade_prob: float = 0.0,
    recent_candles: Any = None,
    open_interest: float = 0.0,
    funding_rate: Optional[float] = None,
    orderbook_snapshots: Optional[List[dict]] = None,
    liquidation_events: Optional[List[dict]] = None,
    performance: Optional[dict] = None,
    volume_intelligence: Optional[dict] = None,
    ohlcv: Any = None,
    oi_history: Optional[List[float]] = None,
    current_oi: Optional[float] = None,
    market_state_detector: Optional[Any] = None,
) -> Dict[str, Any]:
    orderbook = orderbook or {}
    trades = trades or []
    price = _safe_float(price, 0.0)
    ohlcv_data = ohlcv if ohlcv is not None else recent_candles
    try:
        fr = _safe_float(funding_rate, 0.0)
        if exchange is not None and symbol:
            try:
                fetched = exchange.fetch_funding_rate(symbol)
                fr = _safe_float((fetched or {}).get("fundingRate", fr), fr)
            except Exception:
                pass

        vol_intel = volume_intelligence or analyze_volume_intelligence(
            exchange=None,
            symbol=symbol or "BTC/USDT",
            primary_ohlcv=ohlcv_data,
            trades=trades,
            use_exchange=False,
        )

        # Candle frames
        if isinstance(ohlcv_data, dict):
            candles_by_tf = dict(ohlcv_data)
            candles_by_tf.setdefault("1m", _to_rows(ohlcv_data.get("1m") or ohlcv_data.get("primary") or []))
            candles_by_tf.setdefault("5m", _to_rows(ohlcv_data.get("5m") or []))
            candles_by_tf.setdefault("15m", _to_rows(ohlcv_data.get("15m") or []))
            candles_by_tf.setdefault("1h", _to_rows(ohlcv_data.get("1h") or []))
        else:
            base = _to_rows(ohlcv_data)
            candles_by_tf = {
                "1m": _aggregate_ohlcv(base, 1),
                "5m": _aggregate_ohlcv(base, 5),
                "15m": _aggregate_ohlcv(base, 15),
                "1h": _aggregate_ohlcv(base, 60),
                "4h": _aggregate_ohlcv(base, 240),
            }

        primary_1m = candles_by_tf.get("1m") or candles_by_tf.get("primary") or ohlcv_data or recent_candles
        primary_15m = candles_by_tf.get("15m") or _aggregate_ohlcv(primary_1m, 15)

        liquidity_map = predict_liquidity_map(orderbook, price, depth=10)
        gravity = liquidity_gravity_engine(orderbook, price, depth=10)
        sweep = detect_liquidity_sweep(trades, price, threshold_usd=50_000)
        liq_track = track_liquidations(trades, price, lookback=100)
        liq_events = liquidation_stream_processor(liquidation_events or [])
        liquid_cluster_usd = _safe_float(liq_track.get("total_liq", 0.0)) + _safe_float(liq_events.get("total_liquidations", 0.0))
        oi_value = _safe_float(current_oi if current_oi is not None else open_interest, 1_000_000.0) if (current_oi if current_oi is not None else open_interest) else 1_000_000.0
        liq_clusters = detect_liquidation_clusters(
            liquidation_cluster_usd=liquid_cluster_usd,
            open_interest=oi_value,
            funding_rate=fr,
            cascade_prob=_safe_float(cascade_prob, 0.0),
        )
        stop_hunt = detect_stop_hunt(orderbook, trades, recent_candles=ohlcv_data)
        absorption = detect_smart_money_absorption(orderbook, trades)
        smart = smart_money_detection_engine(orderbook, trades, price)
        smart_abs = smart_money_absorption_engine(orderbook, trades, price)
        spoof_details = _detect_spoofing_details(orderbook_snapshots if orderbook_snapshots is not None else [orderbook])
        spoof = spoof_details
        best_bid, best_ask = _best_bid_ask(orderbook)
        bid_vol, ask_vol = _book_volumes(orderbook, depth=10)
        ob_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        ofp = order_flow_pressure_engine(orderbook, trades, price)
        imb = order_imbalance_engine(orderbook)
        mm = market_maker_position_model(ofp, imb, liquidity_map, price)
        oi_hist = list(oi_history or [])
        if current_oi is None:
            current_oi = _safe_float(open_interest, 0.0)
        oi = oi_spike_detection(
            current_oi=current_oi,
            oi_history=oi_hist or [current_oi * 0.98, current_oi],
            price=price,
        )
        cprob = _safe_float(cascade_prob, 0.0)
        if cprob <= 0.0:
            cprob = get_cascade_probability(
                open_interest=oi_value,
                oi_history=oi_hist or [oi_value * 0.95, oi_value],
                liquidation_cluster=liquid_cluster_usd,
                bid=best_bid or price,
                ask=best_ask or price,
                buy_volume=sum(_trade_usd(t, price) for t in trades if _trade_side(t) == "BUY"),
                sell_volume=sum(_trade_usd(t, price) for t in trades if _trade_side(t) == "SELL"),
                funding_rate=fr,
                whale_flag=any(_trade_usd(t, price) >= 500_000 for t in trades),
            )
        spread_pct = _spread_pct(orderbook, price)
        heatmap = liquidation_heatmap_engine(
            liquidation_cluster=liq_track["total_liq"] + liq_events["total_liquidations"],
            open_interest=oi_value,
            funding_rate=fr,
            spread_pct=spread_pct,
        )
        _msd = market_state_detector if market_state_detector is not None else MarketStateDetector()
        market_state = _msd.detect(
            MarketSnapshot(
                symbol=(symbol or "BTC/USDT").replace("/", "").upper(),
                price=price,
                orderbook=orderbook,
                trades=trades,
                candles={
                    "1m": _aggregate_ohlcv(ohlcv_data, 1),
                    "3m": _aggregate_ohlcv(ohlcv_data, 3),
                    "5m": _aggregate_ohlcv(ohlcv_data, 5),
                    "15m": _aggregate_ohlcv(ohlcv_data, 15),
                },
                open_interest=_safe_float(current_oi if current_oi is not None else open_interest, 0.0),
                funding_rate=fr,
            )
        )
        strategy = strategy_optimization_engine(
            volatility=_estimate_volatility_from_ohlcv(ohlcv_data),
            recent_performance=performance or {},
            cascade_probability=cprob,
            order_flow_pressure=ofp.get("pressure_score", 0.0),
            order_imbalance=imb.get("imbalance", 0.0),
        )

        liquidity_intent = analyze_liquidity_intent(orderbook, trades, candles_by_tf)
        mtf_bias = compute_mtf_bias(candles_by_tf)
        trap = detect_traps(orderbook, trades, primary_1m, vol_intel)
        fvg_15 = detect_fvg(primary_15m)
        fvg_5 = detect_fvg(candles_by_tf.get("5m") or primary_1m)
        fvg = fvg_15 if fvg_15.get("exists") else fvg_5
        orderflow = analyze_orderflow(trades, orderbook)
        regime = detect_market_regime(primary_15m if primary_15m else primary_1m, _estimate_volatility_from_ohlcv(ohlcv_data))
        liquidity_magnet = compute_liquidity_magnet(liquidity_intent.get("liquidity_zones", []), price)

        # Proxy order block score
        ob_proxy = {
            "freshness": 1.0,
            "imbalance": 1.0 if orderflow.get("delta", 0.0) > 0 else 0.7,
            "distance_to_liquidity": liquidity_intent.get("nearest_above", {}).get("distance", 0.0)
            if mtf_bias.get("htf_trend") == "bullish"
            else liquidity_intent.get("nearest_below", {}).get("distance", 0.0),
            "atr": _atr(_to_rows(primary_1m)[-30:], 14),
        }
        ob_score = score_order_block(
            ob_proxy,
            volume=_safe_float(vol_intel.get("volume_strength", 0.0)),
            reaction=_safe_float(fvg.get("strength", 0.0)),
        )

        confluence = compute_confluence_score(
            {
                "smc_signal": mtf_bias.get("alignment_score", 0.0),
                "trap": trap.get("strength", 0.0),
                "mtf_bias": mtf_bias.get("alignment_score", 0.0),
                "orderflow": orderflow.get("strength", 0.0),
                "liquidity": liquidity_magnet.get("probability", 0.0),
                "volume": vol_intel.get("volume_strength", 0.0),
                "regime": regime.get("confidence", 0.0),
                "fvg": fvg.get("strength", 0.0),
            }
        )

        market_data = get_market_data(
            orderbook=orderbook,
            trades=trades,
            recent_candles=ohlcv_data,
            price=price,
            orderbook_snapshots=orderbook_snapshots if orderbook_snapshots else [orderbook],
        )
        liquidity_score = market_data["liquidity_score"]

        institutional = institutional_score_engine(
            price=price,
            orderbook=orderbook,
            trades=trades,
            candles={"1m": _aggregate_ohlcv(ohlcv_data, 1)},
            open_interest=oi_value,
            funding_rate=fr,
            volume_intel=vol_intel,
            market_state=market_state,
            liquidity_map=liquidity_map,
            liquidity_gravity=gravity,
            liquidity_sweep=sweep,
            liq_track=liq_track,
            liq_clusters=liq_clusters,
            liq_heatmap=heatmap,
            stop_hunt=stop_hunt,
            absorption=absorption,
            smart_money=smart,
            oi_spike=oi,
            cascade_probability=cprob,
            spoof=spoof,
            order_flow=ofp,
            order_imbalance=imb,
            market_maker=mm,
        )

        smc_signal = evaluate_smc_sniper(
            candles_by_tf=candles_by_tf,
            orderbook=orderbook,
            trades=trades,
            price=price,
            volume_intel=vol_intel,
            market_state=market_state,
            engines_out={},
            use_learning=True,
        )

        # If SMC is strong, use it to set the composite direction.
        if smc_signal.get("signal") in ("LONG", "SHORT") and _safe_float(smc_signal.get("confidence", 0)) >= 7:
            institutional["direction"] = smc_signal["signal"]
            institutional["ai_score"] = 0.75 if smc_signal["signal"] == "LONG" else -0.75
            institutional["confidence"] = _clamp(_safe_float(smc_signal.get("confidence", 0)) / 10.0, 0.0, 1.0)

        liq_data = {
            "long_liquidations": _safe_float(liq_events.get("long_liquidations", 0.0)),
            "short_liquidations": _safe_float(liq_events.get("short_liquidations", 0.0)),
            "total_liquidations": _safe_float(liq_events.get("total_liquidations", 0.0)),
            "dominant_side": liq_events.get("dominant_side", "neutral"),
            "pressure": _safe_float(liq_events.get("pressure", 0.0)),
            "events": liq_events.get("events", []),
        }
        if not liq_data["events"]:
            liq_data = {
                "long_liquidations": _safe_float(liq_track.get("sell_liq", 0.0)),
                "short_liquidations": _safe_float(liq_track.get("buy_liq", 0.0)),
                "total_liquidations": _safe_float(liq_track.get("total_liq", 0.0)),
                "dominant_side": "neutral",
                "pressure": 0.0,
                "events": liq_track.get("spikes", []),
            }

        liquidity_bundle = {
            **liquidity_map,
            "liquidity_intent": liquidity_intent,
            "liquidity_magnet": liquidity_magnet,
            "liquidity_zones": liquidity_intent.get("liquidity_zones", []),
        }

        return {
            "order_flow_pressure": round(_safe_float(ofp.get("pressure_score", 0.0)), 6),
            "order_imbalance": round(_safe_float(imb.get("imbalance", 0.0)), 6),
            "smart_money_detected": bool(smart.get("smart_money_detected", False) or smart_abs.get("absorption", False)),
            "absorption_zones": smart.get("absorption_zones", smart_abs.get("absorption_zones", [])),
            "stop_hunt_detected": bool(stop_hunt.get("stop_hunt", False)),
            "market_maker_bias": str(mm.get("market_maker_bias", "neutral")),
            "oi_spike": bool(oi.get("oi_spike", False)),
            "liquidation_data": liq_data,
            "cascade_probability": round(_safe_float(cprob, 0.0), 6),
            "strategy_adjustment": strategy,
            "liquidity_map": liquidity_bundle,
            "liquidity": liquidity_intent,
            "liquidity_gravity": gravity,
            "liquidity_sweep": sweep,
            "liquidation_tracking": liq_track,
            "liquidation_clusters": liq_clusters,
            "liquidation_heatmap": heatmap,
            "stop_hunt": stop_hunt,
            "trap": trap,
            "absorption": absorption,
            "funding_trap": funding_trap_detector(fr, price, cprob),
            "spoof": spoof,
            "market_data": market_data,
            "liquidity_score": market_data["liquidity_score"],
            "imbalance": market_data["imbalance"],
            "spread_pct": market_data["spread_pct"],
            "spoof_detected": market_data["spoof_detected"],
            "spoof_details": market_data["spoof_details"],
            "volume_intelligence": vol_intel,
            "volume_spike": vol_intel.get("volume_spike", False),
            "volume_explosion": vol_intel.get("volume_explosion", False),
            "volume_strength": vol_intel.get("volume_strength", 0.0),
            "mtf_confirmation": vol_intel.get("mtf_confirmation", False),
            "market_state": market_state,
            "mtf_bias": mtf_bias,
            "funding_rate": round(float(fr), 8),
            "orderbook_imbalance": round(float(ob_imbalance), 6),
            "order_flow_details": ofp,
            "orderflow": orderflow,
            "order_imbalance_details": imb,
            "smart_money_details": smart,
            "smart_money_absorption_details": smart_abs,
            "market_maker_details": mm,
            "oi_details": oi,
            "fvg": fvg,
            "liquidity_magnet": liquidity_magnet,
            "regime": regime,
            "confluence_score": round(float(confluence), 4),
            "order_block_score": round(float(ob_score), 4),
            "institutional_score": institutional.get("institutional_score", 0.0),
            "signal_strength": institutional.get("signal_strength", 0.0),
            "ai_score": institutional.get("ai_score", 0.0),
            "confidence": institutional.get("confidence", 0.0),
            "direction": institutional.get("direction", "HOLD"),
            "smc_signal": smc_signal,
            "composite": institutional,
        }
    except Exception as exc:
        logger.error("run_all_engines error: %s", exc)
        return {
            "order_flow_pressure": 0.0,
            "order_imbalance": 0.0,
            "smart_money_detected": False,
            "absorption_zones": [],
            "stop_hunt_detected": False,
            "market_maker_bias": "neutral",
            "oi_spike": False,
            "liquidation_data": {
                "long_liquidations": 0.0,
                "short_liquidations": 0.0,
                "total_liquidations": 0.0,
                "dominant_side": "neutral",
                "pressure": 0.0,
                "events": [],
            },
            "cascade_probability": 0.0,
            "strategy_adjustment": {
                "threshold_scale": 1.0,
                "confidence_scale": 1.0,
                "risk_scale": 1.0,
                "cooldown": 3,
                "signal_bias": 0.0,
                "notes": ["fallback"],
            },
            "liquidity_map": {
                "liquidity_map": [],
                "largest_zone": {},
                "nearest_zone": {},
                "support_zone": {},
                "resistance_zone": {},
                "zone_count": 0,
            },
            "liquidity": {
                "bait_liquidity": 0.0,
                "resting_liquidity": 0.0,
                "engineered_liquidity": 0.0,
                "liquidity_zones": [],
            },
            "liquidity_gravity": {
                "gravity_score": 0.0,
                "pull_side": "neutral",
                "pull_price": price or 0.0,
                "reason": "error_fallback",
            },
            "liquidity_sweep": {
                "sweep": False,
                "side": "unknown",
                "size_usd": 0.0,
                "trade": None,
                "reason": "error",
            },
            "liquidation_tracking": {
                "buy_liq": 0.0,
                "sell_liq": 0.0,
                "total_liq": 0.0,
                "spikes": [],
            },
            "liquidation_clusters": {
                "cluster_score": 0.0,
                "zone": "low",
                "liq_ratio": 0.0,
                "funding_pressure": 0.0,
            },
            "liquidation_heatmap": {"heat_score": 0, "color": "green", "level": "low"},
            "stop_hunt": {
                "stop_hunt": False,
                "stop_hunt_detected": False,
                "dominant": "BUY",
                "ratio": 0.0,
                "buy_taker": 0.0,
                "sell_taker": 0.0,
                "spike": False,
                "sweep_side": "neutral",
                "strength": 0.0,
            },
            "trap": {"trap": False, "trap_type": None, "direction": "NONE", "confidence": 0.0, "reasons": []},
            "absorption": {
                "absorption": False,
                "score": 0.0,
                "buy_usd": 0.0,
                "sell_usd": 0.0,
                "bids_vol": 0.0,
                "asks_vol": 0.0,
            },
            "funding_trap": {"trap": False, "severity": 0.0, "funding_rate": 0.0},
            "spoof": {"spoof": False, "evidence": []},
            "volume_intelligence": {
                "volume_spike": False,
                "volume_explosion": False,
                "volume_strength": 0.0,
                "mtf_confirmation": False,
                "timeframe": "unknown",
                "direction_bias": 0.0,
            },
            "market_state": {
                "state": "CHOPPY",
                "substate": "CHOPPY",
                "allow_trade": True,
                "bias": 0.0,
                "volatility": 0.0,
                "compression": 1.0,
                "timeframe_breakdown": {},
            },
            "mtf_bias": {
                "htf_trend": "neutral",
                "htf_zone": "neutral",
                "ltf_structure": "range",
                "alignment_score": 0.0,
            },
            "funding_rate": 0.0,
            "orderbook_imbalance": 0.0,
            "orderflow": {"delta": 0.0, "absorption": False, "aggression": "buy", "strength": 0.0},
            "fvg": {"exists": False, "filled": False, "entry_zone": (None, None), "strength": 0.0, "direction": None},
            "liquidity_magnet": {"target_price": price or 0.0, "distance": 0.0, "probability": 0.0},
            "regime": {"type": "range", "confidence": 0.0},
            "confluence_score": 0.0,
            "order_block_score": 0.0,
            "institutional_score": 0.0,
            "signal_strength": 0.0,
            "ai_score": 0.0,
            "confidence": 0.0,
            "direction": "HOLD",
            "smc_signal": {
                "signal": "NONE",
                "entry": None,
                "sl": None,
                "tp": [],
                "reason": "fallback",
                "confidence": 0,
                "market_state": "unknown",
                "trap_type": None,
                "fib_zone": {"low": None, "high": None, "timeframe": "15m"},
                "invalidations": [],
                "features": {},
            },
            "composite": {
                "ai_score": 0.0,
                "confidence": 0.0,
                "institutional_score": 0.0,
                "signal_strength": 0.0,
                "direction": "HOLD",
                "long_score": 0.0,
                "short_score": 0.0,
                "threshold_scale": 1.0,
                "confidence_scale": 1.0,
                "risk_scale": 1.0,
                "components": {},
            },
        }


class MarketStateDetector:
    def __init__(self) -> None:
        self.history: Deque[Dict[str, Any]] = deque(maxlen=200)

    def _tf_state(self, candles: List[list]) -> Dict[str, Any]:
        if not candles or len(candles) < 20:
            return {
                "state": "CHOPPY",
                "bias": 0.0,
                "volatility": 0.0,
                "atr": 0.0,
                "compression": 1.0,
                "slope": 0.0,
            }
        closes = [_safe_float(c[4]) for c in candles if len(c) >= 5]
        highs = [_safe_float(c[2]) for c in candles if len(c) >= 5]
        lows = [_safe_float(c[3]) for c in candles if len(c) >= 5]
        ema_fast = _ema(closes[-10:], 10)
        ema_mid = _ema(closes[-20:], 20)
        ema_slow = _ema(closes[-50:], 50) if len(closes) >= 50 else _ema(closes, min(50, len(closes)))
        atr = _atr(candles[-40:], 14)
        rng = max(max(highs[-20:]) - min(lows[-20:]), 1e-9)
        compression = _clamp(atr / rng, 0.0, 1.0)
        vol = _clamp(
            (statistics.pstdev(closes[-20:]) / max(_mean(closes[-20:]), 1e-9)) if len(closes) >= 2 else 0.0,
            0.0,
            1.0,
        )
        bias = 0.0
        if ema_fast > ema_mid > ema_slow:
            bias = 1.0
        elif ema_fast < ema_mid < ema_slow:
            bias = -1.0
        slope = 0.0
        if len(closes) >= 6 and closes[-6] > 0:
            slope = (closes[-1] - closes[-6]) / closes[-6]
        if compression < 0.18 and vol < 0.007:
            state = "COMPRESSION"
        elif abs(slope) > 0.012 and bias != 0.0:
            state = "TRENDING"
        elif abs(slope) > 0.010 and vol > 0.010:
            state = "EXPANSION"
        elif vol < 0.010 and abs(bias) < 0.5:
            state = "RANGING"
        else:
            state = "CHOPPY"
        return {
            "state": state,
            "bias": bias,
            "volatility": vol,
            "atr": atr,
            "compression": compression,
            "slope": slope,
            "ema_fast": ema_fast,
            "ema_mid": ema_mid,
            "ema_slow": ema_slow,
        }

    def detect(self, snapshot: MarketSnapshot) -> Dict[str, Any]:
        tf_breakdown = {}
        candles = snapshot.candles or {}
        for tf in ("1m", "3m", "5m", "15m"):
            tf_breakdown[tf] = self._tf_state(candles.get(tf, []))
        states = [v["state"] for v in tf_breakdown.values()]
        biases = [v["bias"] for v in tf_breakdown.values()]
        vols = [v["volatility"] for v in tf_breakdown.values()]
        compressions = [v["compression"] for v in tf_breakdown.values()]
        slopes = [v["slope"] for v in tf_breakdown.values()]
        trending_votes = states.count("TRENDING")
        compression_votes = states.count("COMPRESSION")
        expansion_votes = states.count("EXPANSION")
        ranging_votes = states.count("RANGING")
        overall_bias = 0.0
        if abs(sum(biases)) >= 2:
            overall_bias = 1.0 if sum(biases) > 0 else -1.0
        if compression_votes >= 2 and _mean(compressions) < 0.2:
            market_state = "COMPRESSION"
        elif expansion_votes >= 2 and _mean(vols) > 0.012:
            market_state = "EXPANSION"
        elif trending_votes >= 2 and abs(sum(slopes)) > 0.01:
            market_state = "TRENDING"
        elif ranging_votes >= 2:
            market_state = "RANGING"
        else:
            market_state = "CHOPPY"
        pullback = False
        if self.history:
            prev_state = self.history[-1].get("state", "CHOPPY")
            if prev_state == "EXPANSION" and market_state in ("RANGING", "TRENDING", "CHOPPY"):
                pullback = True
        allow_trade = market_state in ("COMPRESSION", "EXPANSION") or pullback
        result = {
            "state": market_state,
            "substate": "PULLBACK" if pullback else market_state,
            "allow_trade": allow_trade,
            "bias": overall_bias,
            "volatility": _mean(vols),
            "compression": _mean(compressions),
            "timeframe_breakdown": tf_breakdown,
        }
        self.history.append(result)
        return result


class LiquidityMapper:
    def __init__(self, level_count: int = 12, distance_window: Tuple[float, float] = (200.0, 400.0)) -> None:
        self.level_count = level_count
        self.distance_window = distance_window

    @staticmethod
    def _swing_levels(candles: List[list], lookback: int = 20) -> Tuple[List[float], List[float]]:
        highs, lows = [], []
        rows = candles[-lookback:]
        if len(rows) < 5:
            return highs, lows
        for i in range(2, len(rows) - 2):
            h = _safe_float(rows[i][2])
            l = _safe_float(rows[i][3])
            prev_h = max(_safe_float(rows[i - 1][2]), _safe_float(rows[i - 2][2]))
            next_h = max(_safe_float(rows[i + 1][2]), _safe_float(rows[i + 2][2]))
            prev_l = min(_safe_float(rows[i - 1][3]), _safe_float(rows[i - 2][3]))
            next_l = min(_safe_float(rows[i + 1][3]), _safe_float(rows[i + 2][3]))
            if h >= prev_h and h >= next_h:
                highs.append(h)
            if l <= prev_l and l <= next_l:
                lows.append(l)
        return highs, lows

    @staticmethod
    def _vpvr_nodes(candles: List[list], buckets: int = 20) -> List[Tuple[float, float]]:
        if len(candles) < 10:
            return []
        prices = [_safe_float(c[4]) for c in candles if len(c) >= 5]
        vols = [_safe_float(c[5]) for c in candles if len(c) >= 6]
        if not prices or not vols:
            return []
        lo, hi = min(prices), max(prices)
        if hi <= lo:
            return []
        step = (hi - lo) / max(buckets, 1)
        hist = {}
        for p, v in zip(prices, vols):
            idx = int((p - lo) / step) if step > 0 else 0
            key = lo + (idx * step)
            hist[key] = hist.get(key, 0.0) + v
        return sorted(hist.items(), key=lambda x: x[1], reverse=True)[: min(6, len(hist))]

    @staticmethod
    def _orderbook_walls(orderbook: dict, price: float) -> Tuple[List[float], List[float]]:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        bid_sizes = [_safe_float(b[1]) for b in bids[:20]]
        ask_sizes = [_safe_float(a[1]) for a in asks[:20]]
        bid_thresh = _mean(bid_sizes, 0.0) + (statistics.pstdev(bid_sizes) if len(bid_sizes) > 2 else 0.0)
        ask_thresh = _mean(ask_sizes, 0.0) + (statistics.pstdev(ask_sizes) if len(ask_sizes) > 2 else 0.0)
        above, below = [], []
        for p, s in asks[:20]:
            if _safe_float(s) >= ask_thresh and _safe_float(p) > price:
                above.append(_safe_float(p))
        for p, s in bids[:20]:
            if _safe_float(s) >= bid_thresh and _safe_float(p) < price:
                below.append(_safe_float(p))
        return above, below

    def map(self, snapshot: MarketSnapshot) -> Dict[str, Any]:
        price = _safe_float(snapshot.price)
        candles_1m = snapshot.candles.get("1m", [])
        candles_3m = snapshot.candles.get("3m", [])
        candles_5m = snapshot.candles.get("5m", [])
        combined = candles_1m[-60:] + candles_3m[-60:] + candles_5m[-60:]
        above, below = [], []
        swing_highs, swing_lows = self._swing_levels(combined, lookback=30)
        vpvr = self._vpvr_nodes(combined)
        wall_above, wall_below = self._orderbook_walls(snapshot.orderbook, price)
        candidates = []
        for h in swing_highs:
            candidates.append(("above", h, "swing_high", 0.75))
        for l in swing_lows:
            candidates.append(("below", l, "swing_low", 0.75))
        for k, v in vpvr:
            if v <= 0:
                continue
            candidates.append(("above" if k >= price else "below", k, "vpvr_node", 0.65))
        for p in wall_above:
            candidates.append(("above", p, "book_wall", 0.85))
        for p in wall_below:
            candidates.append(("below", p, "book_wall", 0.85))
        for lvl in _round_levels(price, 250):
            candidates.append(("above" if lvl > price else "below", lvl, "round_number", 0.5))
        rounded_highs = {}
        rounded_lows = {}
        for c in combined[-80:]:
            if len(c) < 6:
                continue
            h = round(_safe_float(c[2]) / 10.0) * 10.0
            l = round(_safe_float(c[3]) / 10.0) * 10.0
            rounded_highs[h] = rounded_highs.get(h, 0) + 1
            rounded_lows[l] = rounded_lows.get(l, 0) + 1
        for p, cnt in rounded_highs.items():
            if cnt >= 2 and p > price:
                candidates.append(("above", p, "equal_high", min(1.0, 0.4 + cnt * 0.1)))
        for p, cnt in rounded_lows.items():
            if cnt >= 2 and p < price:
                candidates.append(("below", p, "equal_low", min(1.0, 0.4 + cnt * 0.1)))
        for side, level, source, strength in candidates:
            dist = abs(level - price)
            if dist <= 0:
                continue
            item = {
                "price": float(level),
                "distance_points": float(dist),
                "source": source,
                "strength": round(float(strength), 3),
            }
            if side == "above":
                above.append(item)
            else:
                below.append(item)
        above = sorted({x["price"]: x for x in above}.values(), key=lambda x: x["distance_points"])[: self.level_count]
        below = sorted({x["price"]: x for x in below}.values(), key=lambda x: x["distance_points"])[: self.level_count]
        nearest_above = above[0] if above else None
        nearest_below = below[0] if below else None
        nearest_dist = None
        if nearest_above and nearest_below:
            nearest_dist = min(nearest_above["distance_points"], nearest_below["distance_points"])
        elif nearest_above:
            nearest_dist = nearest_above["distance_points"]
        elif nearest_below:
            nearest_dist = nearest_below["distance_points"]
        no_trade = True
        if nearest_dist is not None:
            lo, hi = self.distance_window
            no_trade = not (lo <= nearest_dist <= hi)
        return {
            "liquidity_above": above,
            "liquidity_below": below,
            "nearest_above": nearest_above,
            "nearest_below": nearest_below,
            "nearest_distance": nearest_dist,
            "no_trade": no_trade,
        }


class BreakoutValidator:
    def __init__(self, volume_multiplier: float = 1.5) -> None:
        self.volume_multiplier = volume_multiplier

    def validate(self, candles: List[list], direction: str, breakout_level: float) -> Dict[str, Any]:
        if len(candles) < 25:
            return {
                "valid": False,
                "fake_breakout": True,
                "score": 0,
                "reasons": ["not_enough_data"],
            }
        last = candles[-1]
        prev = candles[-2]
        recent = candles[-21:-1]
        current_vol = _safe_float(last[5]) if len(last) > 5 else 0.0
        avg_vol = _mean([_safe_float(c[5]) for c in recent if len(c) > 5], 0.0)
        vol_ok = current_vol > avg_vol * self.volume_multiplier if avg_vol > 0 else False
        body = _body_ratio(last)
        wick = _wick_ratio(last)
        close = _safe_float(last[4])
        prev_close = _safe_float(prev[4])
        if direction == "LONG":
            break_ok = close > breakout_level and prev_close <= breakout_level
            follow = close >= breakout_level and _safe_float(last[1]) <= breakout_level * 1.002
        else:
            break_ok = close < breakout_level and prev_close >= breakout_level
            follow = close <= breakout_level and _safe_float(last[1]) >= breakout_level * 0.998
        candle_quality = body >= 0.55 and wick <= 0.45
        fake_breakout = not (vol_ok and break_ok and candle_quality and follow)
        score = min((3 if vol_ok else 0) + (3 if candle_quality else 0) + (2 if break_ok else 0) + (2 if follow else 0), 10)
        reasons = [
            "volume_spike" if vol_ok else "no_volume_spike",
            "body_dominance" if candle_quality else "wicky_candle",
            "level_broken" if break_ok else "no_break",
            "follow_through_ok" if follow else "follow_through_fail",
        ]
        return {
            "valid": bool(not fake_breakout),
            "fake_breakout": bool(fake_breakout),
            "score": int(score),
            "reasons": reasons,
            "volume_ok": vol_ok,
            "current_volume": current_vol,
            "avg_volume": avg_vol,
            "body_ratio": body,
            "wick_ratio": wick,
        }


class TrapFilter:
    def __init__(self) -> None:
        self.oi_history: Deque[float] = deque(maxlen=50)
        self.price_history: Deque[float] = deque(maxlen=50)

    def evaluate(self, snapshot: MarketSnapshot, direction: str, breakout_level: float, breakout_valid: Dict[str, Any]) -> Dict[str, Any]:
        price = _safe_float(snapshot.price)
        self.price_history.append(price)
        self.oi_history.append(_safe_float(snapshot.open_interest))
        reasons = []
        reject = False
        opposite_bias = "WAIT"
        if len(self.oi_history) >= 2:
            prev_oi = self.oi_history[-2]
            curr_oi = self.oi_history[-1]
            if prev_oi > 0 and curr_oi < prev_oi * 0.995:
                reject = True
                reasons.append("oi_decreasing")
        if len(self.price_history) >= 2:
            prev_price = self.price_history[-2]
            if abs(price - prev_price) >= 140:
                reject = True
                reasons.append("too_fast_liquidity_sweep")
        fr = _safe_float(snapshot.funding_rate)
        if direction == "LONG" and fr > 0.01:
            reject = True
            opposite_bias = "SHORT"
            reasons.append("crowded_long_funding")
        elif direction == "SHORT" and fr < -0.01:
            reject = True
            opposite_bias = "LONG"
            reasons.append("crowded_short_funding")
        if not breakout_valid.get("volume_ok", False):
            reject = True
            reasons.append("no_volume_expansion")
        if breakout_valid.get("fake_breakout", False):
            reject = True
            reasons.append("fake_breakout")
        return {
            "reject": bool(reject),
            "opposite_bias": opposite_bias,
            "reasons": reasons,
            "funding_rate": fr,
        }


class EntryTriggerEngine:
    def __init__(self) -> None:
        self.max_trades_per_session = 3
        self.trades_taken = 0
        self.last_loss_ts = 0.0
        self.loss_cooldown_sec = 600.0
        self.session_start = time.time()

    def _cooldown_active(self) -> bool:
        return (time.time() - self.last_loss_ts) < self.loss_cooldown_sec

    def _estimate_confidence(
        self,
        market_state: Dict[str, Any],
        liquidity: Dict[str, Any],
        breakout_valid: Dict[str, Any],
        trap: Dict[str, Any],
        strategy_bias: str,
        direction: str,
        volume_intel: Optional[Dict[str, Any]] = None,
    ) -> int:
        score = 0
        if market_state.get("allow_trade"):
            score += 1
        if market_state.get("state") in ("COMPRESSION", "EXPANSION"):
            score += 2
        if liquidity.get("nearest_distance") is not None and not liquidity.get("no_trade"):
            score += 1
        if breakout_valid.get("valid"):
            score += 3
        if not trap.get("reject"):
            score += 2
        if strategy_bias in (direction, "NEUTRAL"):
            score += 1
        if volume_intel:
            if volume_intel.get("volume_spike"):
                score += 1
            if volume_intel.get("volume_explosion"):
                score += 1
            if volume_intel.get("mtf_confirmation"):
                score += 1
        return int(_clamp(score, 1, 10))

    def _build_rr(self, entry: float, sl: float, direction: str) -> Tuple[List[float], float]:
        risk = abs(entry - sl)
        if risk <= 0:
            return [], 0.0
        if direction == "LONG":
            tps = [entry + 150.0, entry + 220.0, entry + 300.0]
        else:
            tps = [entry - 150.0, entry - 220.0, entry - 300.0]
        rr = abs(tps[-1] - entry) / risk
        return tps, rr

    def generate(
        self,
        snapshot: MarketSnapshot,
        market_state: Dict[str, Any],
        liquidity: Dict[str, Any],
        breakout_valid: Dict[str, Any],
        trap: Dict[str, Any],
        strategy_bias: str,
        volume_intel: Optional[Dict[str, Any]] = None,
    ) -> SniperSignal:
        if self.trades_taken >= self.max_trades_per_session:
            return SniperSignal(
                bias="WAIT",
                entry_price=None,
                entry_zone=None,
                stop_loss=None,
                take_profit=None,
                rr_ratio=None,
                confidence_score=0,
                setup_type="session_limit",
                state="WAIT",
                reasons=["max_trades_per_session_reached"],
            )

        if self._cooldown_active():
            return SniperSignal(
                bias="WAIT",
                entry_price=None,
                entry_zone=None,
                stop_loss=None,
                take_profit=None,
                rr_ratio=None,
                confidence_score=0,
                setup_type="cooldown",
                state="WAIT",
                reasons=["cooldown_after_loss"],
            )

        price = _safe_float(snapshot.price)
        state_name = str(market_state.get("state", "CHOPPY"))
        substate = str(market_state.get("substate", state_name))
        allowed = market_state.get("allow_trade", False)

        if liquidity.get("no_trade", True):
            return SniperSignal(
                bias="WAIT",
                entry_price=None,
                entry_zone=None,
                stop_loss=None,
                take_profit=None,
                rr_ratio=None,
                confidence_score=0,
                setup_type="liquidity_missing",
                state="WAIT",
                reasons=["no_liquidity_within_200_400_points"],
            )

        if not allowed:
            return SniperSignal(
                bias="WAIT",
                entry_price=None,
                entry_zone=None,
                stop_loss=None,
                take_profit=None,
                rr_ratio=None,
                confidence_score=0,
                setup_type="state_filter",
                state="WAIT",
                reasons=[f"market_state_{state_name}_not_allowed"],
            )

        nearest_above = liquidity.get("nearest_above")
        nearest_below = liquidity.get("nearest_below")
        above_lvl = _safe_float(nearest_above["price"]) if nearest_above else 0.0
        below_lvl = _safe_float(nearest_below["price"]) if nearest_below else 0.0

        funding = _safe_float(snapshot.funding_rate)
        bias = "WAIT"
        reasons = []
        setup_type = "none"

        if funding > 0.01:
            strategy_bias = "SHORT" if strategy_bias == "NEUTRAL" else strategy_bias
            reasons.append("crowded_long_funding_bias_short")
        elif funding < -0.01:
            strategy_bias = "LONG" if strategy_bias == "NEUTRAL" else strategy_bias
            reasons.append("crowded_short_funding_bias_long")

        if trap.get("reject"):
            if trap.get("opposite_bias") in ("LONG", "SHORT"):
                bias = trap.get("opposite_bias")
                setup_type = "liquidity_grab"
                reasons.append("trap_detected_opposite_setup")
            else:
                return SniperSignal(
                    bias="WAIT",
                    entry_price=None,
                    entry_zone=None,
                    stop_loss=None,
                    take_profit=None,
                    rr_ratio=None,
                    confidence_score=0,
                    setup_type="trap_reject",
                    state="WAIT",
                    reasons=trap.get("reasons", []),
                )

        if bias == "WAIT":
            if breakout_valid.get("valid") and state_name in ("COMPRESSION", "EXPANSION") and strategy_bias in ("LONG", "NEUTRAL") and nearest_above:
                bias = "LONG"
                setup_type = "breakout" if state_name == "COMPRESSION" else "pullback"
                reasons.extend(["resistance_breakout", "volume_confirmed"])
            elif (not breakout_valid.get("valid")) and state_name in ("COMPRESSION", "EXPANSION") and snapshot.strategy_bias in ("LONG", "NEUTRAL") and nearest_below:
                if _safe_float(snapshot.price) >= below_lvl:
                    bias = "LONG"
                    setup_type = "pullback"
                    reasons.append("higher_low_forming")

        if bias == "WAIT":
            if breakout_valid.get("valid") and state_name in ("COMPRESSION", "EXPANSION") and strategy_bias in ("SHORT", "NEUTRAL") and nearest_below:
                bias = "SHORT"
                setup_type = "breakout" if state_name == "COMPRESSION" else "pullback"
                reasons.extend(["support_breakdown", "volume_confirmed"])
            elif (not breakout_valid.get("valid")) and state_name in ("COMPRESSION", "EXPANSION") and snapshot.strategy_bias in ("SHORT", "NEUTRAL") and nearest_above:
                if _safe_float(snapshot.price) <= above_lvl:
                    bias = "SHORT"
                    setup_type = "pullback"
                    reasons.append("lower_high_forming")

        if bias == "WAIT":
            return SniperSignal(
                bias="WAIT",
                entry_price=None,
                entry_zone=None,
                stop_loss=None,
                take_profit=None,
                rr_ratio=None,
                confidence_score=0,
                setup_type="wait",
                state="WAIT",
                reasons=["no_high_probability_setup"],
            )

        if bias == "LONG":
            level = above_lvl if above_lvl > 0 else price
            entry_price = level
            zone_low = level - 10.0
            zone_high = level + 10.0
            sl = max(below_lvl - 20.0, entry_price - _clamp(_atr(snapshot.candles.get("1m", [])[-30:], 14), 50.0, 120.0))
            sl = min(sl, entry_price - 50.0)
        else:
            level = below_lvl if below_lvl > 0 else price
            entry_price = level
            zone_low = level - 10.0
            zone_high = level + 10.0
            sl = min(above_lvl + 20.0, entry_price + _clamp(_atr(snapshot.candles.get("1m", [])[-30:], 14), 50.0, 120.0))
            sl = max(sl, entry_price + 50.0)

        tps, rr = self._build_rr(entry_price, sl, bias)
        if rr < 2.0:
            if bias == "LONG":
                tps = [entry_price + 150.0, entry_price + 220.0, entry_price + 300.0]
            else:
                tps = [entry_price - 150.0, entry_price - 220.0, entry_price - 300.0]
            rr = abs(tps[-1] - entry_price) / abs(entry_price - sl)

        confidence = self._estimate_confidence(
            market_state=market_state,
            liquidity=liquidity,
            breakout_valid=breakout_valid,
            trap=trap,
            strategy_bias=strategy_bias,
            direction=bias,
            volume_intel=volume_intel or {},
        )

        if confidence < 8:
            return SniperSignal(
                bias="WAIT",
                entry_price=None,
                entry_zone=None,
                stop_loss=None,
                take_profit=None,
                rr_ratio=None,
                confidence_score=confidence,
                setup_type=setup_type,
                state="PREPARE",
                reasons=reasons + ["confidence_below_8"],
                metadata={"market_state": state_name, "substate": substate},
            )

        return SniperSignal(
            bias=bias,
            entry_price=round(float(entry_price), 2),
            entry_zone=[round(float(zone_low), 2), round(float(zone_high), 2)],
            stop_loss=round(float(sl), 2),
            take_profit=[round(float(tp), 2) for tp in tps],
            rr_ratio=round(float(rr), 2),
            confidence_score=confidence,
            setup_type=setup_type,
            state="TRIGGERED",
            reasons=reasons,
            metadata={
                "market_state": state_name,
                "substate": substate,
                "volume_intel": volume_intel or {},
            },
        )

    def register_trade_result(self, pnl: float) -> None:
        self.trades_taken += 1
        if pnl < 0:
            self.last_loss_ts = time.time()


class BinanceRestData:
    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.symbol = symbol.replace("/", "").upper()

    def open_interest(self) -> float:
        if requests is None:
            return 0.0
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": self.symbol},
                timeout=5,
            )
            r.raise_for_status()
            return _safe_float((r.json() or {}).get("openInterest", 0.0))
        except Exception:
            return 0.0

    def funding_rate(self) -> float:
        if requests is None:
            return 0.0
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": self.symbol},
                timeout=5,
            )
            r.raise_for_status()
            return _safe_float((r.json() or {}).get("lastFundingRate", 0.0))
        except Exception:
            return 0.0


class BinanceFuturesStreamClient:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        on_snapshot: Optional[Callable[[MarketSnapshot], None]] = None,
        on_raw: Optional[Callable[[dict], None]] = None,
        throttle_sec: float = 1.0,
    ) -> None:
        self.symbol = symbol.replace("/", "").lower()
        self.on_snapshot = on_snapshot
        self.on_raw = on_raw
        self.throttle_sec = throttle_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_emit = 0.0
        self._lock = threading.Lock()
        self.price = 0.0
        self.orderbook: Dict[str, Any] = {"bids": [], "asks": []}
        self.trades: Deque[dict] = deque(maxlen=200)
        self.candles: Dict[str, List[list]] = {"1m": [], "3m": [], "5m": [], "15m": []}
        self.open_interest = 0.0
        self.funding_rate = 0.0
        self.whale_walls = {"above": [], "below": []}

    def _stream_url(self) -> str:
        streams = [
            f"{self.symbol}@bookTicker",
            f"{self.symbol}@depth@100ms",
            f"{self.symbol}@aggTrade",
            f"{self.symbol}@forceOrder",
            f"{self.symbol}@kline_1m",
            f"{self.symbol}@kline_3m",
            f"{self.symbol}@kline_5m",
            f"{self.symbol}@kline_15m",
            f"{self.symbol}@markPrice@1s",
        ]
        return "wss://fstream.binance.com/stream?streams=" + "/".join(streams)

    def stop(self) -> None:
        self._stop.set()

    def _emit_snapshot(self) -> None:
        now = time.time()
        if now - self._last_emit < self.throttle_sec:
            return
        self._last_emit = now
        snapshot = MarketSnapshot(
            symbol=self.symbol.upper(),
            price=self.price,
            timestamp=now,
            orderbook=self.orderbook,
            trades=list(self.trades),
            candles=self.candles,
            open_interest=self.open_interest,
            funding_rate=self.funding_rate,
            whale_walls=self.whale_walls,
        )
        if self.on_snapshot:
            try:
                self.on_snapshot(snapshot)
            except Exception:
                pass

    def _handle_depth(self, data: dict) -> None:
        try:
            bids = [[_safe_float(p), _safe_float(q)] for p, q in data.get("b", [])]
            asks = [[_safe_float(p), _safe_float(q)] for p, q in data.get("a", [])]
            with self._lock:
                self.orderbook = {"bids": bids, "asks": asks}
        except Exception:
            pass

    def _handle_bookticker(self, data: dict) -> None:
        try:
            bid = _safe_float(data.get("b"))
            ask = _safe_float(data.get("a"))
            if bid > 0 and ask > 0:
                self.price = (bid + ask) / 2.0
                with self._lock:
                    self.orderbook = {
                        "bids": [[bid, _safe_float(data.get("B"), 0.0)]],
                        "asks": [[ask, _safe_float(data.get("A"), 0.0)]],
                    }
        except Exception:
            pass

    def _handle_trade(self, data: dict) -> None:
        try:
            p = _safe_float(data.get("p"))
            q = _safe_float(data.get("q"))
            self.price = p or self.price
            trade = {
                "price": p,
                "amount": q,
                "side": "SELL" if data.get("m") else "BUY",
                "ts": data.get("T", int(time.time() * 1000)),
                "raw": data,
            }
            with self._lock:
                self.trades.append(trade)
        except Exception:
            pass

    def _handle_force_order(self, data: dict) -> None:
        try:
            order = data.get("o", {})
            side = str(order.get("S", "")).upper()
            p = _safe_float(order.get("ap", order.get("p", 0.0)))
            q = _safe_float(order.get("q", 0.0))
            self.price = p or self.price
            trade = {
                "price": p,
                "amount": q,
                "side": side,
                "liquidation": True,
                "ts": data.get("E", int(time.time() * 1000)),
                "raw": data,
            }
            with self._lock:
                self.trades.append(trade)
        except Exception:
            pass

    def _handle_kline(self, data: dict) -> None:
        try:
            k = data.get("k", {})
            interval = str(k.get("i", "")).lower()
            if interval not in ("1m", "3m", "5m", "15m"):
                return
            candle = [
                int(k.get("t", 0)),
                _safe_float(k.get("o")),
                _safe_float(k.get("h")),
                _safe_float(k.get("l")),
                _safe_float(k.get("c")),
                _safe_float(k.get("v")),
            ]
            with self._lock:
                rows = self.candles.setdefault(interval, [])
                if rows and rows[-1][0] == candle[0]:
                    rows[-1] = candle
                else:
                    rows.append(candle)
                    if len(rows) > 500:
                        del rows[:-500]
            self.price = candle[4] or self.price
        except Exception:
            pass

    def _handle_mark_price(self, data: dict) -> None:
        try:
            self.funding_rate = _safe_float(data.get("r", data.get("lastFundingRate", 0.0)))
            self.price = _safe_float(data.get("p", self.price)) or self.price
        except Exception:
            pass

    def _handle_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
            if self.on_raw:
                try:
                    self.on_raw(payload)
                except Exception:
                    pass
            data = payload.get("data", payload)
            stream = str(payload.get("stream", "")).lower()
            if "@bookticker" in stream:
                self._handle_bookticker(data)
            elif "@depth" in stream:
                self._handle_depth(data)
            elif "@aggtrade" in stream:
                self._handle_trade(data)
            elif "@forceorder" in stream:
                self._handle_force_order(data)
            elif "@kline_" in stream:
                self._handle_kline(data)
            elif "@markprice" in stream:
                self._handle_mark_price(data)
            self._emit_snapshot()
        except Exception:
            pass

    def _run(self) -> None:
        if websocket is None:
            return

        def _on_open(ws):
            pass

        def _on_message(ws, message):
            if not self._stop.is_set():
                self._handle_message(message)

        def _on_error(ws, error):
            pass

        def _on_close(ws, *args):
            pass

        backoff = 1.0
        while not self._stop.is_set():
            ws = websocket.WebSocketApp(
                self._stream_url(),
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            try:
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 10.0)

    def start(self) -> None:
        if websocket is None:
            logger.warning("websocket-client missing; live stream disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()


class TelegramAlertSystem:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None, enabled: bool = True) -> None:
        self.token = token or ""
        self.chat_id = str(chat_id or "")
        self.enabled = enabled and bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled or requests is None:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": str(text),
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            r = requests.post(url, json=payload, timeout=10)
            return bool(r.ok)
        except Exception:
            return False

    def send_signal(self, signal: Dict[str, Any]) -> bool:
        try:
            text = json.dumps(signal, ensure_ascii=False, separators=(",", ":"))
            return self.send_message(text)
        except Exception:
            return False


class SniperExecutionEngine:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        strategy_bias_provider: Optional[Callable[[], str]] = None,
        on_signal: Optional[Callable[[SniperSignal], None]] = None,
    ) -> None:
        self.symbol = symbol.replace("/", "").upper()
        self.strategy_bias_provider = strategy_bias_provider or (lambda: "NEUTRAL")
        self.on_signal = on_signal
        self.state_detector = MarketStateDetector()
        self.liquidity_mapper = LiquidityMapper()
        self.breakout_validator = BreakoutValidator()
        self.trap_filter = TrapFilter()
        self.entry_engine = EntryTriggerEngine()
        self.rest = BinanceRestData(self.symbol)
        self.stream = BinanceFuturesStreamClient(symbol=self.symbol, on_snapshot=self._on_snapshot)
        self.latest_snapshot: Optional[MarketSnapshot] = None
        self.latest_decision: Optional[SniperSignal] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()

    def update_snapshot(self, snapshot: Any) -> None:
        if isinstance(snapshot, dict):
            snapshot = MarketSnapshot(
                symbol=str(snapshot.get("symbol", self.symbol)).replace("/", "").upper(),
                price=_safe_float(snapshot.get("price", 0.0)),
                timestamp=_safe_float(snapshot.get("timestamp", time.time())),
                orderbook=snapshot.get("orderbook", {}) or {},
                trades=snapshot.get("trades", []) or [],
                candles=snapshot.get("candles", {}) or {},
                open_interest=_safe_float(snapshot.get("open_interest", 0.0)),
                funding_rate=_safe_float(snapshot.get("funding_rate", 0.0)),
                strategy_bias=str(snapshot.get("strategy_bias", "NEUTRAL")),
                whale_walls=snapshot.get("whale_walls", {}) or {},
                extra={
                    k: v
                    for k, v in snapshot.items()
                    if k
                    not in {
                        "symbol",
                        "price",
                        "timestamp",
                        "orderbook",
                        "trades",
                        "candles",
                        "open_interest",
                        "funding_rate",
                        "strategy_bias",
                        "whale_walls",
                    }
                },
            )
        if isinstance(snapshot, MarketSnapshot):
            self._on_snapshot(snapshot)

    def _collect_volume_intel(self, snapshot: MarketSnapshot) -> Dict[str, Any]:
        candles = snapshot.candles.get("1m", [])
        if len(candles) < 25:
            return {
                "volume_spike": False,
                "volume_explosion": False,
                "volume_strength": 0.0,
                "mtf_confirmation": False,
                "timeframe": "1m",
                "direction_bias": 0.0,
            }
        rows = candles[-25:]
        vols = [_safe_float(c[5]) for c in rows]
        closes = [_safe_float(c[4]) for c in rows]
        curr_vol = vols[-1]
        avg20 = _mean(vols[-21:-1], _mean(vols[:-1], curr_vol))
        spike = curr_vol > avg20 * 1.5 if avg20 > 0 else False
        prev = closes[-2]
        last = closes[-1]
        move = (last - prev) / prev if prev else 0.0
        direction_bias = 1.0 if move > 0 else -1.0 if move < 0 else 0.0
        explosion = spike and abs(move) > 0.0010 and _body_ratio(rows[-1]) > 0.55
        mtf = False
        for tf in ("3m", "5m", "15m"):
            tf_candles = snapshot.candles.get(tf, [])
            if len(tf_candles) >= 25:
                tf_rows = tf_candles[-25:]
                tf_vols = [_safe_float(c[5]) for c in tf_rows]
                tf_curr = tf_vols[-1]
                tf_avg = _mean(tf_vols[-21:-1], _mean(tf_vols[:-1], tf_curr))
                if tf_curr > tf_avg * 1.4:
                    mtf = True
                    break
        strength = 0.0
        if spike:
            strength += 0.45
        if explosion:
            strength += 0.35
        if mtf:
            strength += 0.20
        return {
            "volume_spike": bool(spike),
            "volume_explosion": bool(explosion),
            "volume_strength": round(_clamp(strength, 0.0, 1.0), 4),
            "mtf_confirmation": bool(mtf),
            "timeframe": "1m",
            "direction_bias": round(direction_bias, 4),
            "avg_volume": round(avg20, 4),
            "current_volume": round(curr_vol, 4),
        }

    def _on_snapshot(self, snapshot: MarketSnapshot) -> None:
        with self._lock:
            self.latest_snapshot = snapshot
        market_state = self.state_detector.detect(snapshot)
        liquidity = self.liquidity_mapper.map(snapshot)
        volume_intel = self._collect_volume_intel(snapshot)
        direction = "LONG" if market_state.get("bias", 0.0) > 0 else "SHORT" if market_state.get("bias", 0.0) < 0 else "WAIT"
        breakout_level = _safe_float(
            liquidity.get("nearest_above", {}).get("price", snapshot.price)
            if direction == "LONG"
            else liquidity.get("nearest_below", {}).get("price", snapshot.price)
        )
        breakout_valid = self.breakout_validator.validate(
            snapshot.candles.get("1m", []),
            "LONG" if direction == "LONG" else "SHORT",
            breakout_level,
        )
        trap = self.trap_filter.evaluate(
            snapshot,
            "LONG" if direction == "LONG" else "SHORT",
            breakout_level,
            breakout_valid,
        )
        strategy_bias = str(self.strategy_bias_provider() or "NEUTRAL").upper()
        if strategy_bias not in ("LONG", "SHORT", "NEUTRAL"):
            strategy_bias = "NEUTRAL"
        signal = self.entry_engine.generate(
            snapshot,
            market_state,
            liquidity,
            breakout_valid,
            trap,
            strategy_bias,
            volume_intel=volume_intel,
        )
        signal.metadata.update(
            {
                "market_state": market_state,
                "liquidity": liquidity,
                "breakout_valid": breakout_valid,
                "trap": trap,
                "volume_intel": volume_intel,
                "open_interest": snapshot.open_interest or self.rest.open_interest(),
                "funding_rate": snapshot.funding_rate or self.rest.funding_rate(),
                "strategy_bias": strategy_bias,
            }
        )
        self.latest_decision = signal
        if self.on_signal:
            try:
                self.on_signal(signal)
            except Exception:
                pass

    def evaluate(self) -> Optional[SniperSignal]:
        with self._lock:
            return self.latest_decision

    def register_trade_result(self, pnl: float) -> None:
        self.entry_engine.register_trade_result(pnl)


def _aggregate_rows_any(candles: Any, factor: int) -> List[list]:
    if isinstance(candles, dict):
        for key in ("1m", "primary", "5m", "15m", "1h"):
            if key in candles and candles[key]:
                return _aggregate_rows(candles[key], factor)
        return []
    return _aggregate_rows(candles, factor)


def _tf_from_any(candles: Any, tf: str, fallback: int) -> List[list]:
    if isinstance(candles, dict):
        if tf in candles and candles[tf]:
            return _to_rows(candles[tf])
        if tf == "1m" and candles.get("primary"):
            return _to_rows(candles["primary"])
    return _aggregate_rows_any(candles, fallback)


def compute_composite_score(
    price: float,
    orderbook: dict,
    trades: List[dict],
    candles: Dict[str, Any],
    open_interest: float,
    funding_rate: float,
    volume_intel: Dict[str, Any],
    market_state: Dict[str, Any],
    liquidity_map: Dict[str, Any],
    liquidity_gravity: Dict[str, Any],
    liquidity_sweep: Dict[str, Any],
    liq_track: Dict[str, Any],
    liq_clusters: Dict[str, Any],
    liq_heatmap: Dict[str, Any],
    stop_hunt: Dict[str, Any],
    absorption: Dict[str, Any],
    smart_money: Dict[str, Any],
    oi_spike: Dict[str, Any],
    cascade_probability: float,
    spoof: Dict[str, Any],
    order_flow: Dict[str, Any],
    order_imbalance: Dict[str, Any],
    market_maker: Dict[str, Any],
) -> Dict[str, Any]:
    return institutional_score_engine(
        price=price,
        orderbook=orderbook,
        trades=trades,
        candles=candles,
        open_interest=open_interest,
        funding_rate=funding_rate,
        volume_intel=volume_intel,
        market_state=market_state,
        liquidity_map=liquidity_map,
        liquidity_gravity=liquidity_gravity,
        liquidity_sweep=liquidity_sweep,
        liq_track=liq_track,
        liq_clusters=liq_clusters,
        liq_heatmap=liq_heatmap,
        stop_hunt=stop_hunt,
        absorption=absorption,
        smart_money=smart_money,
        oi_spike=oi_spike,
        cascade_probability=cascade_probability,
        spoof=spoof,
        order_flow=order_flow,
        order_imbalance=order_imbalance,
        market_maker=market_maker,
    )


def update_weights_from_outcome(trade_outcome: dict):
    return update_model_weights(trade_outcome)


__all__ = [
    "predict_liquidity_map",
    "liquidity_gravity_engine",
    "detect_liquidity_sweep",
    "detect_liquidation_clusters",
    "track_liquidations",
    "liquidation_heatmap_engine",
    "get_cascade_probability",
    "funding_trap_detector",
    "detect_stop_hunt",
    "detect_smart_money_absorption",
    "detect_spoofing",
    "_detect_spoofing_details",
    "calculate_liquidity_score",
    "calculate_ml_confidence",
    "get_market_data",
    "compute_volume_spike_engine",
    "analyze_volume_intelligence",
    "order_flow_pressure_engine",
    "order_imbalance_engine",
    "smart_money_detection_engine",
    "smart_money_absorption_engine",
    "stop_hunt_engine",
    "market_maker_position_model",
    "oi_spike_detection",
    "liquidation_stream_processor",
    "strategy_optimization_engine",
    "compute_sma",
    "compute_sma_signal",
    "_sigmoid",
    "heatmap_value_from_cluster",
    "compute_score",
    "institutional_score_engine",
    "detect_entry_trigger",
    "build_trade_plan",
    "detect_liquidity_map",
    "detect_liquidity_gravity",
    "get_liquidation_heatmap",
    "detect_entry_condition",
    "build_trade_plan_for_signal",
    "MarketStateDetector",
    "LiquidityMapper",
    "BreakoutValidator",
    "TrapFilter",
    "MarketSnapshot",
    "SniperSignal",
    "SniperExecutionEngine",
    "BinanceFuturesStreamClient",
    "BinanceRestData",
    "TelegramAlertSystem",
    "run_all_engines",
    "analyze_liquidity_intent",
    "detect_traps",
    "compute_mtf_bias",
    "score_order_block",
    "detect_fvg",
    "analyze_orderflow",
    "compute_liquidity_magnet",
    "detect_market_regime",
    "compute_confluence_score",
    "update_model_weights",
    "update_learning_memory",
    "evaluate_smc_sniper",
    "detect_structure",
    "detect_liquidity",
    "detect_fibonacci_zone",
    "score_setup",
    "build_risk_plan",
    "update_weights_from_outcome",
]
