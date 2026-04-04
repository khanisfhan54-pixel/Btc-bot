# main.py
# main.py – entry point of the trading bot
#!/usr/bin/env python3
from __future__ import annotations

import sys
import os
import json
import time
import math
import random
import logging
import statistics
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET", "")
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

# ADDED: safety layer for live execution
LIVE_TRADING = False

SYMBOL = "BTC/USDT"
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.json")
BACKTEST_RESULT_PATH = os.path.join(os.path.dirname(__file__), "backtest_result.json")

try:
    from feature_engine import FeatureEngine
except Exception as _fe_import_err:
    logger.warning("feature_engine import failed: %s", _fe_import_err)

    class FeatureEngine:
        def update(self, orderbook: dict, trades: list) -> dict:
            bids = (orderbook.get("bids") or [])[:10]
            asks = (orderbook.get("asks") or [])[:10]
            bid_vol = sum(_safe_float(b[1]) for b in bids)
            ask_vol = sum(_safe_float(a[1]) for a in asks)
            total = bid_vol + ask_vol
            return {
                "bid_vol": bid_vol,
                "ask_vol": ask_vol,
                "imbalance": 0.0 if total == 0 else (bid_vol - ask_vol) / total,
                "trade_count": len(trades or []),
            }

try:
    from signal_engine import SignalEngine
except Exception as _se_import_err:
    logger.warning("signal_engine import failed: %s", _se_import_err)

    class SignalEngine:
        def generate(self, features: dict) -> dict:
            return {"signal": "HOLD", "confidence": 0.0, "reason": "fallback"}

try:
    from execution import (
        ExecutionLogic,
        ExecutionEngine,
        calculate_position_size,
        calculate_liquidity_sl_tp,
    )
except Exception as _exec_import_err:
    logger.warning("execution import failed: %s", _exec_import_err)

    class ExecutionLogic:
        def __init__(self, *args, **kwargs):
            pass

        def decide(self, signal_payload: dict, features_payload: dict, snapshot: dict, account_equity: float, meta_result=None) -> dict:
            return {"execute": False, "side": "buy", "sl": 0.0, "tp": 0.0, "position_size": 0.0, "reason": "fallback"}

    class ExecutionEngine:
        def __init__(self, *args, **kwargs):
            pass

        def get_balance(self):
            return 0.0

        def place_market_order(self, *args, **kwargs):
            return {"id": None, "status": "skipped"}

        def place_limit_order(self, *args, **kwargs):
            return {"id": None, "status": "skipped"}

        def place_order_with_sl_tp(self, *args, **kwargs):
            return {"id": None, "status": "skipped"}

    def calculate_position_size(balance, risk_percent, stop_loss_distance):
        try:
            risk_amount = float(balance) * (float(risk_percent) / 100.0)
            d = abs(float(stop_loss_distance))
            return 0.0 if d <= 0 else risk_amount / d
        except Exception:
            return 0.0

    def calculate_liquidity_sl_tp(signal, price, data):
        raise RuntimeError("execution.py is unavailable")

try:
    from telegram_bot import send_telegram_message
except Exception as _tg_import_err:
    logger.warning("telegram_bot import failed: %s", _tg_import_err)

    def send_telegram_message(message: str) -> bool:
        logger.info("TELEGRAM (fallback): %s", message)
        return False

try:
    from queue_fill_model import QueueFillModel
    from toxicity_filter import ToxicityFilter
    from order_router import OrderRouter
    from impact_decay import ImpactDecay
    from position_manager import PositionManager
    from trade_lifecycle_manager import TradeLifecycleManager
except Exception as _new_module_import_err:
    logger.warning("New module import failed (using stubs): %s", _new_module_import_err)

    class QueueFillModel:
        def enrich(self, fp):
            return fp

    class ToxicityFilter:
        def enrich(self, fp):
            return fp

    class OrderRouter:
        def route(self, signal, fp, snapshot):
            return {
                "execute": True,
                "order_type": "market",
                "urgency": "high",
                "reason": "stub_passthrough",
                "details": "",
                "slippage_budget_bps": 0.0,
                "route_confidence": 1.0,
                "fill_prob_dir": 1.0,
                "toxicity_score": 0.0,
                "liq_score": 1.0,
            }

    class ImpactDecay:
        def record_entry(self, *a, **kw):
            pass

        def update(self, *a, **kw):
            return {
                "impact_bps": 0.0,
                "decay_factor": 0.0,
                "residual_impact": 0.0,
                "half_life_s": 0.0,
                "fully_decayed": True,
                "elapsed_s": 0.0,
                "price_move_bps": 0.0,
                "direction": "LONG",
            }

        def reset(self):
            pass

    class PositionManager:
        def has_position(self) -> bool:
            return False

        def on_entry(self, **kw) -> None:
            pass

        def on_exit(self, **kw) -> None:
            pass

        def update(self, price: float, features: dict) -> dict:
            return {"action": "NO_POSITION"}

    class TradeLifecycleManager:
        def update(self, price: float, features: dict) -> dict:
            return {"action": "HOLD", "block_new_entries": False, "risk_scale": 1.0, "reason": "stub"}

        def can_open_new_trade(self, features: dict) -> bool:
            return True

        def on_entry(self, **kw) -> None:
            pass

        def on_exit(self, **kw) -> None:
            pass

        def session_guard(self) -> dict:
            return {"action": "ALLOW", "block_new_entries": False}

try:
    from learning_engine import LEARNING_ENGINE
except Exception:
    LEARNING_ENGINE = None

try:
    from exit_quality_engine import ExitQualityEngine
    exit_quality_engine = ExitQualityEngine()
except Exception as _eq_import_err:
    logger.warning("exit_quality_engine import failed: %s", _eq_import_err)
    exit_quality_engine = None

try:
    from execution_liquidation_engine import ExecutionLiquidationEngine as _ELE
    EXECUTION_LIQUIDATION_ENGINE = _ELE({
        "decay_alpha": 0.5,
        "gamma":       0.1,
        "max_impact":  0.05,
        "liq_buffer":  0.02,
    })
except Exception as _ele_err:
    logger.warning("execution_liquidation_engine import failed: %s", _ele_err)
    EXECUTION_LIQUIDATION_ENGINE = None

try:
    from execution_quality import PRE_TRADE_QUALITY_ENGINE
except Exception as _ptq_err:
    logger.warning("PRE_TRADE_QUALITY_ENGINE import failed: %s", _ptq_err)
    PRE_TRADE_QUALITY_ENGINE = None

engine           = ExecutionEngine()
feature_engine   = FeatureEngine()
signal_engine    = SignalEngine()
execution_engine = ExecutionLogic()
fill_model       = QueueFillModel()
tox_filter       = ToxicityFilter()
order_router     = OrderRouter()
impact_tracker   = ImpactDecay()
position_manager = PositionManager()
trade_lifecycle  = TradeLifecycleManager()

try:
    from engine import (
        run_all_engines,
        analyze_volume_intelligence,
        detect_entry_trigger,
        build_trade_plan,
        compute_score,
        get_cascade_probability,
        MarketStateDetector,
        evaluate_smc_sniper,
        evaluate_meta_filter,
        apply_meta_to_decision,
    )
except Exception as _e:
    logger.warning("Engines import failed: %s", _e)

    def run_all_engines(*args, **kwargs):
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
                "zone_count": 0,
            },
            "liquidity_gravity": {
                "gravity_score": 0.0,
                "pull_side": "neutral",
                "pull_price": 0.0,
                "reason": "fallback",
            },
            "liquidity_sweep": {
                "sweep": False,
                "side": "unknown",
                "size_usd": 0.0,
                "trade": None,
                "reason": "fallback",
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
            "liquidation_heatmap": {"heat_score": 0, "color": "green"},
            "stop_hunt": {
                "stop_hunt": False,
                "dominant": "BUY",
                "ratio": 0.0,
                "buy_taker": 0.0,
                "sell_taker": 0.0,
                "spike": False,
            },
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
            "funding_rate": 0.0,
            "orderbook_imbalance": 0.0,
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
        }

    def analyze_volume_intelligence(*args, **kwargs):
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

    def detect_entry_trigger(*args, **kwargs):
        return {"trigger": False, "reason": "fallback", "confidence": 0.0}

    def build_trade_plan(*args, **kwargs):
        return None

    def compute_score(
        sma_signal, ob_imbalance, whale_signal, funding_rate, cascade_probability
    ):
        long_score = round(max(0.0, min(1.0, 0.5 + ob_imbalance * 0.2)), 6)
        short_score = round(max(0.0, min(1.0, 1.0 - long_score)), 6)
        direction = (
            "LONG"
            if long_score > 0.55
            else "SHORT"
            if short_score > 0.55
            else "NEUTRAL"
        )
        return {
            "long_score": long_score,
            "short_score": short_score,
            "direction": direction,
        }

    def get_cascade_probability(*args, **kwargs):
        return 0.0

    def evaluate_smc_sniper(*args, **kwargs):
        return {
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
        }

    class MarketStateDetector:
        def detect(self, *args, **kwargs):
            return {
                "state": "CHOPPY",
                "substate": "CHOPPY",
                "allow_trade": True,
                "bias": 0.0,
                "volatility": 0.0,
                "compression": 1.0,
                "timeframe_breakdown": {},
            }

    def evaluate_meta_filter(*args, **kwargs):
        return {"allow_trade": True, "risk_scale": 1.0, "reason": "engine_unavailable", "meta_state": {}}

    def apply_meta_to_decision(decision, meta_result):
        return decision if isinstance(decision, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _enforce_entry_fee_metadata(fees, fee_type, trade_id=None):
    fees_val = _safe_float(fees, 0.0)

    fee_type_val = str(fee_type).lower().strip() if fee_type is not None else None

    if fee_type_val not in ("quote", "pct"):
        logger.warning(
            "Invalid fee_type in main.py. Defaulting to pct. trade_id=%s",
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


def _spread_pct(orderbook: dict) -> float:
    try:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        best_bid = _safe_float(bids[0][0]) if bids else 0.0
        best_ask = _safe_float(asks[0][0]) if asks else 0.0
        if best_bid <= 0 or best_ask <= 0:
            return 0.0
        mid = (best_bid + best_ask) / 2.0
        return max(0.0, (best_ask - best_bid) / max(mid, 1e-9))
    except Exception:
        return 0.0


def _estimate_volatility_from_ohlcv(ohlcv: list) -> float:
    try:
        closes = [
            float(r[4])
            for r in (ohlcv or [])
            if isinstance(r, (list, tuple)) and len(r) >= 5
        ]
        if len(closes) < 6:
            return 0.0
        rets = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
        return abs(statistics.stdev(rets)) if len(rets) >= 2 else 0.0
    except Exception:
        return 0.0


def _agg_ohlcv(rows: list, factor: int) -> list:
    try:
        if not rows:
            return []
        rows = [r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 6]
        if factor <= 1:
            return list(rows)
        out = []
        chunk = []
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


def _fallback_liquidity_map_from_orderbook(orderbook: dict, price: float) -> dict:
    try:
        bids = (orderbook or {}).get("bids", [])[:12]
        asks = (orderbook or {}).get("asks", [])[:12]
        zones = []
        for i, b in enumerate(bids):
            p = _safe_float(b[0])
            s = _safe_float(b[1])
            if p > 0:
                zones.append(
                    {
                        "side": "bid",
                        "level": i,
                        "price": p,
                        "size": s,
                        "distance_points": abs(price - p),
                        "source": "book",
                    }
                )
        for i, a in enumerate(asks):
            p = _safe_float(a[0])
            s = _safe_float(a[1])
            if p > 0:
                zones.append(
                    {
                        "side": "ask",
                        "level": i,
                        "price": p,
                        "size": s,
                        "distance_points": abs(p - price),
                        "source": "book",
                    }
                )
        if zones:
            biggest = max(zones, key=lambda z: z.get("size", 0.0))
            nearest = min(zones, key=lambda z: z.get("distance_points", 1e18))
        else:
            biggest = {
                "side": "none",
                "price": price,
                "size": 0.0,
                "distance_points": 0.0,
            }
            nearest = biggest
        return {
            "liquidity_map": zones,
            "largest_zone": biggest,
            "nearest_zone": nearest,
            "zone_count": len(zones),
        }
    except Exception:
        return {
            "liquidity_map": [],
            "largest_zone": {
                "side": "none",
                "price": price,
                "size": 0.0,
                "distance_points": 0.0,
            },
            "nearest_zone": {
                "side": "none",
                "price": price,
                "size": 0.0,
                "distance_points": 0.0,
            },
            "zone_count": 0,
        }


def _signal_value(signal: str) -> float:
    return {
        "STRONG_LONG": 1.0,
        "LONG": 0.7,
        "SHORT": -0.7,
        "STRONG_SHORT": -1.0,
        "BUY": 0.7,
        "SELL": -0.7,
        "NEUTRAL": 0.0,
        "HOLD": 0.0,
    }.get(str(signal).upper(), 0.0)


def compute_sma(closes: list, period: int):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def sma_crossover_signal(ohlcv: list, fast: int = 10, slow: int = 30):
    closes = [c[4] for c in ohlcv if isinstance(c, (list, tuple)) and len(c) >= 5]
    if len(closes) < slow + 1:
        return "NEUTRAL", None, None
    sma_fast_now = compute_sma(closes, fast)
    sma_slow_now = compute_sma(closes, slow)
    sma_fast_prev = compute_sma(closes[:-1], fast)
    sma_slow_prev = compute_sma(closes[:-1], slow)
    if None in (sma_fast_now, sma_slow_now, sma_fast_prev, sma_slow_prev):
        return "NEUTRAL", sma_fast_now, sma_slow_now
    if sma_fast_prev < sma_slow_prev and sma_fast_now > sma_slow_now:
        signal = "BUY"
    elif sma_fast_prev > sma_slow_prev and sma_fast_now < sma_slow_now:
        signal = "SELL"
    else:
        signal = "NEUTRAL"
    return signal, sma_fast_now, sma_slow_now


def orderbook_imbalance(orderbook: dict) -> float:
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    bid_vol = sum(_safe_float(b[1]) for b in bids)
    ask_vol = sum(_safe_float(a[1]) for a in asks)
    total = bid_vol + ask_vol
    return 0.0 if total == 0 else (bid_vol - ask_vol) / total


def detect_whale_trades(trades: list, usd_threshold: float = 500_000) -> list:
    whales = []
    for t in trades or []:
        notional = _safe_float(t.get("amount", 0)) * _safe_float(t.get("price", 0))
        if notional >= usd_threshold:
            whales.append(
                {
                    "id": t.get("id"),
                    "side": t.get("side"),
                    "amount": t.get("amount"),
                    "price": t.get("price"),
                    "notional": notional,
                    "datetime": t.get("datetime"),
                }
            )
    return whales


def whale_net_signal(whales: list):
    buy_n = sum(
        _safe_float(w["notional"])
        for w in whales
        if str(w.get("side", "")).lower() == "buy"
    )
    sell_n = sum(
        _safe_float(w["notional"])
        for w in whales
        if str(w.get("side", "")).lower() == "sell"
    )
    total = buy_n + sell_n
    if total == 0:
        return "NEUTRAL", 0.0
    ratio = (buy_n - sell_n) / total
    sig = "BUY" if ratio > 0.2 else "SELL" if ratio < -0.2 else "NEUTRAL"
    return sig, ratio


def get_exchange():
    import ccxt

    return ccxt.binance(
        {
            "apiKey": BINANCE_API_KEY or None,
            "secret": BINANCE_SECRET or None,
            "options": {"defaultType": "future"},
            "enableRateLimit": True,
        }
    )


def get_data_exchange() -> Tuple[Any, str]:
    """
    Return (exchange, symbol) for public market data.
    Tries OKX perpetuals first (accessible from cloud/Replit servers), then Binance.
    OKX swap symbol for BTC/USDT is 'BTC/USDT:USDT'.
    """
    import ccxt

    candidates = [
        (
            "okx",
            {"enableRateLimit": True, "options": {"defaultType": "swap"}},
            "BTC/USDT:USDT",
        ),
    ]
    for name, cfg, sym in candidates:
        try:
            ex = getattr(ccxt, name)(cfg)
            ex.load_markets()
            logger.info(
                "[DATA EXCHANGE] Using %s (%s) for market data", name.upper(), sym
            )
            return ex, sym
        except Exception as exc:
            logger.warning(
                "[DATA EXCHANGE] %s unavailable: %s — trying next", name.upper(), exc
            )
    logger.warning("[DATA EXCHANGE] All fallbacks failed — falling back to Binance")
    return get_exchange(), SYMBOL


# MODIFIED: route legacy telegram helper through telegram_bot.send_telegram_message
def send_telegram(message: str) -> None:
    try:
        send_telegram_message(message)
    except Exception as exc:
        logger.error("Telegram error: %s", exc)


def fetch_ohlcv(exchange, symbol=SYMBOL, timeframe="1h", limit=200):
    return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


def fetch_orderbook(exchange, symbol=SYMBOL, limit=50):
    return exchange.fetch_order_book(symbol, limit=limit)


def fetch_recent_trades(exchange, symbol=SYMBOL, limit=200):
    return exchange.fetch_trades(symbol, limit=limit)


def _fetch_multi_tf(exchange, symbol: str = SYMBOL) -> Dict[str, list]:
    return {
        "1m": fetch_ohlcv(exchange, symbol, timeframe="1m", limit=240),
        "5m": fetch_ohlcv(exchange, symbol, timeframe="5m", limit=240),
        "15m": fetch_ohlcv(exchange, symbol, timeframe="15m", limit=240),
        "1h": fetch_ohlcv(exchange, symbol, timeframe="1h", limit=240),
    }


def _fetch_open_interest(exchange) -> float:
    try:
        raw = SYMBOL.replace("/", "")
        if hasattr(exchange, "fapiPublicGetOpenInterest"):
            oi = exchange.fapiPublicGetOpenInterest({"symbol": raw})
            return _safe_float((oi or {}).get("openInterest", 0.0))
    except Exception:
        pass
    return 0.0


def _fetch_funding_rate(exchange) -> float:
    try:
        fr = exchange.fetch_funding_rate(SYMBOL)
        return _safe_float((fr or {}).get("fundingRate", 0.0))
    except Exception:
        return 0.0


class LiquidationMonitor:
    def __init__(self, symbol: str = "btcusdt", window_seconds: int = 300):
        self.symbol = symbol.lower()
        self.window_seconds = window_seconds
        self._events: Deque[dict] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _prune(self):
        cutoff = time.time() - self.window_seconds
        with self._lock:
            while self._events and self._events[0]["ts"] < cutoff:
                self._events.popleft()

    def get_events(self) -> List[dict]:
        self._prune()
        with self._lock:
            return list(self._events)

    def get_stats(self) -> dict:
        events = self.get_events()
        long_liq = sum(
            _safe_float(e.get("usd", 0.0))
            for e in events
            if str(e.get("side", "")).upper() == "SELL"
        )
        short_liq = sum(
            _safe_float(e.get("usd", 0.0))
            for e in events
            if str(e.get("side", "")).upper() == "BUY"
        )
        return {
            "buy_liq": round(short_liq, 2),
            "sell_liq": round(long_liq, 2),
            "total_liq": round(long_liq + short_liq, 2),
            "last_event": events[-1] if events else None,
        }

    def _on_message(self, ws, raw):
        try:
            data = json.loads(raw)
            order = data.get("o", {})
            side = str(order.get("S", "")).upper()
            qty = _safe_float(order.get("q", 0.0))
            price = _safe_float(order.get("ap", order.get("p", 0.0)))
            usd = qty * price
            ev = {"ts": time.time(), "side": side, "price": price, "usd": usd}
            with self._lock:
                self._events.append(ev)
            if usd >= 500_000:
                send_telegram(
                    f"🔥 *Liquidation Detected*\nSide: {side}\nValue: ${usd:,.0f}\nTime: {datetime.now(timezone.utc).isoformat()}"
                )
        except Exception as exc:
            logger.error("Liquidation message error: %s", exc)

    def _run(self):
        try:
            import websocket
        except Exception as exc:
            logger.error("websocket-client missing: %s", exc)
            return
        url = f"wss://fstream.binance.com/ws/{self.symbol}@forceOrder"
        backoff = 1.0
        while not self._stop.is_set():
            ws = websocket.WebSocketApp(
                url,
                on_message=self._on_message,
                on_error=lambda *_: None,
                on_close=lambda *_: None,
            )
            try:
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 10.0)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()


SIGNAL_STATE = {
    "cycle": 0,
    "last_signal_cycle": -9999,
    "last_trade_cycle": -9999,
    "bias_count": 0,
    "last_direction": None,
    "cooldown_candles": 3,
    "min_signal_every": 30,
}


def get_ai_score(
    ob_imbalance: float,
    buy_vol: float,
    sell_vol: float,
    volatility: float,
    cascade_prob: float,
    sma_signal: str,
    engines_out: dict,
    volume_intel: dict,
) -> dict:
    total_vol = buy_vol + sell_vol + 1e-9
    trade_pressure = (buy_vol - sell_vol) / total_vol
    ob = _clamp(ob_imbalance, -1.0, 1.0)
    cascade = _clamp(cascade_prob, 0.0, 1.0)
    sma_val = _signal_value(sma_signal)

    ofp = _clamp(_safe_float(engines_out.get("order_flow_pressure", 0.0)), -1.0, 1.0)
    oib = _clamp(_safe_float(engines_out.get("order_imbalance", 0.0)), -1.0, 1.0)
    mm_bias = str(engines_out.get("market_maker_bias", "neutral")).lower()
    mm = 0.15 if mm_bias == "bullish" else -0.15 if mm_bias == "bearish" else 0.0
    smart_money = 1.0 if engines_out.get("smart_money_detected") else 0.0
    oi_spike = 1.0 if engines_out.get("oi_spike") else 0.0
    funding_rate = _safe_float(engines_out.get("funding_rate", 0.0))

    vi = volume_intel or {}
    volume_strength = _clamp(_safe_float(vi.get("volume_strength", 0.0)), 0.0, 1.0)
    volume_bias = _clamp(_safe_float(vi.get("direction_bias", 0.0)), -1.0, 1.0)

    raw = 0.0
    raw += ob * 0.20
    raw += trade_pressure * 0.18
    raw += ofp * 0.22
    raw += oib * 0.10
    raw += sma_val * 0.10
    raw += mm * 0.10
    raw += smart_money * 0.06 * (1.0 if volume_bias >= 0 else -1.0)
    raw += oi_spike * 0.04 * (1.0 if volume_bias >= 0 else -1.0)
    raw += volume_bias * volume_strength * 0.10
    raw += (1.0 - cascade) * 0.06
    raw += (-funding_rate * 4.0) * 0.02

    vol_adj = 1.0
    if volatility < 0.008:
        vol_adj = 0.9
    elif volatility > 0.025:
        vol_adj = 0.95

    ai_score = _clamp(raw * vol_adj, -1.0, 1.0)

    agreement = 1.0 - min(1.0, abs(ob - trade_pressure))
    confidence = (
        0.32 * agreement
        + 0.25 * (1.0 - cascade)
        + 0.18 * abs(ofp)
        + 0.08 * abs(oib)
        + 0.05 * smart_money
        + 0.05 * oi_spike
        + 0.07 * volume_strength
    )
    if mm_bias != "neutral":
        confidence += 0.03
    confidence = _clamp(confidence, 0.0, 1.0)

    if abs(ai_score) < 0.02:
        ai_score = 0.0

    return {
        "ai_score": round(ai_score, 6),
        "confidence": round(confidence, 6),
        "components": {
            "ob": ob,
            "trade_pressure": trade_pressure,
            "order_flow_pressure": ofp,
            "order_imbalance": oib,
            "sma_val": sma_val,
            "market_maker_bias": mm_bias,
            "volume_strength": volume_strength,
            "volume_bias": volume_bias,
            "cascade": cascade,
            "funding_rate": funding_rate,
        },
    }


def determine_signal(
    ai_score: float,
    confidence: float,
    volatility: float,
    state: dict,
    engines_out: dict = None,
) -> dict:
    engines_out = engines_out or {}
    notes = []
    strategy = engines_out.get("strategy_adjustment", {}) or {}
    threshold_scale = _clamp(
        _safe_float(strategy.get("threshold_scale", 1.0)), 0.6, 1.6
    )
    confidence_scale = _clamp(
        _safe_float(strategy.get("confidence_scale", 1.0)), 0.6, 1.6
    )
    signal_bias = _clamp(_safe_float(strategy.get("signal_bias", 0.0)), -0.4, 0.4)
    cooldown = int(
        strategy.get("cooldown", state.get("cooldown_candles", 3))
        or state.get("cooldown_candles", 3)
    )

    long_thr = 0.30 * threshold_scale
    strong_thr = 0.50 * threshold_scale
    short_thr = -0.30 * threshold_scale
    strong_short_thr = -0.50 * threshold_scale

    if volatility < 0.008:
        long_thr *= 0.85
        strong_thr *= 0.85
        short_thr *= 0.85
        strong_short_thr *= 0.85
        notes.append("low_vol_relax")
    elif volatility > 0.025:
        notes.append("high_vol_strict")

    if -0.2 < ai_score < 0.2:
        ai_score *= 1.35
        notes.append("deadzone_amplified")

    cycles_since_last = state["cycle"] - state["last_signal_cycle"]
    if cycles_since_last > state.get("min_signal_every", 30):
        relax = max(1.0, cycles_since_last / state.get("min_signal_every", 30))
        long_thr /= relax
        strong_thr /= relax
        short_thr /= relax
        strong_short_thr /= relax
        notes.append(f"force_relax_{relax:.2f}")

    cycles_since_trade = state["cycle"] - state["last_trade_cycle"]
    allow_weak = cycles_since_trade >= cooldown

    ai_score = _clamp(ai_score + signal_bias, -1.0, 1.0)

    sig = "HOLD"
    if ai_score > strong_thr and confidence >= 0.60 * confidence_scale:
        sig = "STRONG_LONG"
    elif ai_score > long_thr:
        sig = "LONG"
    elif ai_score < strong_short_thr and confidence >= 0.60 * confidence_scale:
        sig = "STRONG_SHORT"
    elif ai_score < short_thr:
        sig = "SHORT"
    elif confidence < 0.50 and abs(ai_score) > 0.25 and allow_weak:
        sig = "LONG" if ai_score > 0 else "SHORT"
        notes.append("fallback_low_confidence")

    if sig == "HOLD":
        if state["last_direction"] in ("LONG", "SHORT"):
            state["bias_count"] += 1
        else:
            state["bias_count"] = 0
        if state["bias_count"] >= 40:
            sig = state["last_direction"] if state["last_direction"] else "HOLD"
            notes.append("deadzone_forced")
            state["bias_count"] = 0
    else:
        state["last_direction"] = "LONG" if "LONG" in sig else "SHORT"
        state["bias_count"] = 0

    return {
        "signal": sig,
        "notes": notes,
        "thresholds": {
            "long": long_thr,
            "strong": strong_thr,
            "short": short_thr,
            "strong_short": strong_short_thr,
            "threshold_scale": threshold_scale,
            "confidence_scale": confidence_scale,
        },
        "strategy_adjustment": strategy,
    }


def log_trade(entry: dict) -> None:
    try:
        with open(TRADE_LOG_PATH, "r") as f:
            trades = json.load(f)
    except Exception:
        trades = []
    trades.append(entry)
    with open(TRADE_LOG_PATH, "w") as f:
        json.dump(trades, f, indent=2)
    logger.info("Trade logged: %s", json.dumps(entry))


def build_alert_message(
    result: dict,
    ai_meta: dict,
    current_price: float,
    cascade_prob: float,
    heatmap: dict,
    liq_stats: dict,
    sma_signal: str,
    sma10: float,
    sma30: float,
    whale_signal: str,
    trade_plan: Optional[dict] = None,
    sniper: Optional[dict] = None,
    volume_intel: Optional[dict] = None,
    engines_out: Optional[dict] = None,
    smc_signal: Optional[dict] = None,
) -> str:
    mode_tag = "[DRY RUN]" if DRY_RUN else "[LIVE]"
    heat_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
        heatmap.get("color", "green"), "⚪"
    )
    display_signal = result.get("signal", result.get("direction", "NEUTRAL"))
    lines = [
        f"*{mode_tag} BTCUSDT Institutional Signal*",
        f"Price        : ${current_price:,.2f}",
        f"AI Score     : {ai_meta['ai_score']:.4f} | CONF {ai_meta['confidence']:.2f} → {display_signal}",
        f"Signal       : {display_signal}",
        f"Long score   : {result.get('long_score', 0.0):.4f}",
        f"Short score  : {result.get('short_score', 0.0):.4f}",
        f"SMA signal   : {sma_signal}  (SMA10={sma10:.2f} SMA30={sma30:.2f})"
        if sma10
        else f"SMA signal   : {sma_signal}",
        f"Whale signal : {whale_signal}",
        f"Cascade prob : {cascade_prob:.2%}",
        f"Heatmap      : {heat_emoji} {heatmap['color'].upper()} ({heatmap['heat_score']}/100)",
        f"Liq 5m       : buy=${liq_stats['buy_liq']:,.0f}  sell=${liq_stats['sell_liq']:,.0f}",
        f"Time         : {datetime.now(timezone.utc).isoformat()}",
    ]

    if volume_intel:
        try:
            lines.append(
                f"Volume       : spike={volume_intel.get('volume_spike')} explosion={volume_intel.get('volume_explosion')} strength={_safe_float(volume_intel.get('volume_strength'), 0.0):.2f} mtf={volume_intel.get('mtf_confirmation')}"
            )
        except Exception:
            pass

    if engines_out:
        try:
            lines.append(
                f"OF Pressure  : {_safe_float(engines_out.get('order_flow_pressure'), 0.0):+.3f} | Imbalance: {_safe_float(engines_out.get('order_imbalance'), 0.0):+.3f}"
            )
            lines.append(
                f"Smart Money  : {engines_out.get('smart_money_detected')} | Stop Hunt: {engines_out.get('stop_hunt_detected')} | MM Bias: {engines_out.get('market_maker_bias')}"
            )
            lines.append(
                f"OI Spike     : {engines_out.get('oi_spike')} | Cascade: {_safe_float(engines_out.get('cascade_probability'), 0.0):.2%}"
            )
        except Exception:
            pass

    if sniper:
        try:
            lines.append(
                f"Sniper       : trigger={sniper.get('trigger')} | {sniper.get('reason')}"
            )
        except Exception:
            pass

    if trade_plan:
        try:
            tp = trade_plan.get("tp")
            if isinstance(tp, (list, tuple)):
                tp_text = ", ".join(f"{_safe_float(x):.2f}" for x in tp)
            else:
                tp_text = f"{_safe_float(tp):.2f}"
            lines.append("")
            lines.append(f"🎯 ENTRY: {_safe_float(trade_plan.get('entry')):.2f}")
            lines.append(f"🛑 SL: {_safe_float(trade_plan.get('sl')):.2f}")
            lines.append(f"💰 TP: {tp_text}")
            lines.append(f"📊 RR: {trade_plan.get('rr', trade_plan.get('risk_reward', 0.0))}")
        except Exception:
            pass

    engines = ai_meta.get("engines", {})
    if engines:
        try:
            lines.append(f"[ENGINES] LiquiditySweep: {engines.get('liquidity_sweep')}")
            lines.append(f"[ENGINES] StopHunt: {engines.get('stop_hunt')}")
            lines.append(f"[ENGINES] Absorption: {engines.get('absorption')}")
            lines.append(f"[ENGINES] FundingTrap: {engines.get('funding_trap')}")
            lines.append(f"[ENGINES] Spoof: {engines.get('spoof')}")
        except Exception:
            pass

    if smc_signal and smc_signal.get("signal") in ("LONG", "SHORT"):
        try:
            fib = smc_signal.get("fib_zone") or {}
            lines.append("")
            lines.append(f"SMC Signal   : {smc_signal.get('signal')} | conf={smc_signal.get('confidence')}/10")
            lines.append(f"SMC Reason   : {smc_signal.get('reason')}")
            lines.append(f"SMC Trap     : {smc_signal.get('trap_type')}")
            lines.append(
                f"SMC Fib      : {fib.get('low')} → {fib.get('high')} ({fib.get('timeframe', '15m')})"
            )
        except Exception:
            pass

    return "\n".join(lines)


# ADDED: execution helpers
def _normalize_trade_signal(signal: str) -> str:
    s = str(signal or "").upper()
    if s in ("STRONG_LONG", "LONG", "BUY"):
        return "LONG"
    if s in ("STRONG_SHORT", "SHORT", "SELL"):
        return "SHORT"
    return "NONE"


def _build_execution_market_data(candles_by_tf: Dict[str, Any], engines_out: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        candle_rows = candles_by_tf.get("1m") or candles_by_tf.get("15m") or candles_by_tf.get("primary") or []
        rows = [r for r in candle_rows if isinstance(r, (list, tuple)) and len(r) >= 6]
        if not rows:
            return None

        recent_rows = rows[-20:]
        recent_high = max(_safe_float(r[2]) for r in recent_rows)
        recent_low = min(_safe_float(r[3]) for r in recent_rows)

        sweep = engines_out.get("liquidity_sweep") or {}
        sweep_side = str(sweep.get("side", "")).upper()
        sweep_confirmed = bool(sweep.get("sweep", False))

        if sweep_side not in ("BUY", "SELL") or not sweep_confirmed:
            return None

        return {
            "high": recent_high,
            "low": recent_low,
            "liquidity_sweep": {
                "side": sweep_side,
                "sweep": True,
            },
        }
    except Exception:
        return None


def _format_execution_message(
    title: str,
    signal: str,
    price: float,
    confidence: float,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    order_id: Optional[str] = None,
) -> str:
    lines = [title]
    lines.append(f"Side: {signal}")
    lines.append(f"Confidence: {confidence:.2f}")
    lines.append(f"Price: {price:,.2f}")
    if sl_price is not None:
        lines.append(f"SL: {sl_price:,.2f}")
    if tp_price is not None:
        lines.append(f"TP: {tp_price:,.2f}")
    if order_id is not None:
        lines.append(f"Order ID: {order_id}")
    return "\n".join(lines)


def _execute_liquidity_trade(
    execution_signal: str,
    price: float,
    confidence: float,
    candles_by_tf: Dict[str, Any],
    engines_out: Dict[str, Any],
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    position_size: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Execute a trade signal.

    Uses sl_price / tp_price / position_size from ExecutionLogic.decide() when provided.
    Falls back to computing them from market data when not provided (e.g. legacy path).
    """
    market_data: Optional[Dict[str, Any]] = None

    # --- Resolve SL / TP ---
    if sl_price is None or tp_price is None or _safe_float(sl_price) <= 0 or _safe_float(tp_price) <= 0:
        market_data = _build_execution_market_data(candles_by_tf, engines_out)
        if not market_data:
            logger.info("Skipping execution: liquidity market data missing or sweep not confirmed.")
            return {"executed": False, "reason": "missing_liquidity_data"}
        try:
            sl_price, tp_price = calculate_liquidity_sl_tp(execution_signal, price, market_data)
        except Exception as exc:
            logger.warning("calculate_liquidity_sl_tp failed: %s", exc)
            return {"executed": False, "reason": f"sl_tp_error:{exc}"}

    try:
        if sl_price is None or tp_price is None:
            return {"executed": False, "reason": "invalid_sl_tp"}

        stop_loss_distance = abs(price - _safe_float(sl_price))
        if stop_loss_distance <= 0:
            return {"executed": False, "reason": "invalid_stop_loss_distance"}

        # --- Resolve position size ---
        if position_size is None or _safe_float(position_size) <= 0:
            balance = _safe_float(engine.get_balance(), 0.0)
            position_size = calculate_position_size(
                balance, risk_percent=1, stop_loss_distance=stop_loss_distance
            )

        pre_msg = _format_execution_message(
            title="📊 BTC SIGNAL",
            signal=execution_signal,
            confidence=confidence,
            price=price,
        )
        send_telegram_message(pre_msg)

        if position_size is None or _safe_float(position_size) <= 0:
            return {"executed": False, "reason": "invalid_position_size"}

        if not LIVE_TRADING:
            print(
                f"🧪 Paper Trade: {execution_signal} @ {price:.2f}"
                f" | SL={_safe_float(sl_price):.2f} | TP={_safe_float(tp_price):.2f}"
                f" | Size={_safe_float(position_size):.6f}"
            )
            paper_msg = _format_execution_message(
                title="🧪 Paper Trade Executed",
                signal=execution_signal,
                confidence=confidence,
                price=price,
                sl_price=_safe_float(sl_price),
                tp_price=_safe_float(tp_price),
                order_id="paper",
            )
            send_telegram_message(paper_msg)
            return {
                "executed": False,
                "paper": True,
                "sl": sl_price,
                "tp": tp_price,
                "position_size": _safe_float(position_size),
                "market_data": market_data,
            }

        side = "buy" if execution_signal == "LONG" else "sell"
        execution_result = engine.place_order_with_sl_tp(
            SYMBOL,
            side,
            position_size,
            sl_price,
            tp_price,
        )

        order_id = None
        if isinstance(execution_result, dict):
            order_id = (
                execution_result.get("id")
                or execution_result.get("orderId")
                or execution_result.get("order_id")
            )
        elif execution_result is not None:
            order_id = getattr(execution_result, "id", None) or getattr(execution_result, "orderId", None)

        post_msg = _format_execution_message(
            title="🚀 Trade Executed",
            signal=execution_signal,
            confidence=confidence,
            price=price,
            sl_price=_safe_float(sl_price),
            tp_price=_safe_float(tp_price),
            order_id=str(order_id) if order_id is not None else "unknown",
        )
        send_telegram_message(post_msg)

        return {
            "executed": True,
            "paper": False,
            "order_id": order_id,
            "sl": sl_price,
            "tp": tp_price,
            "position_size": position_size,
            "result": execution_result,
            "market_data": market_data,
        }
    except Exception as exc:
        err_msg = f"❌ Execution Error\nSignal: {execution_signal}\nPrice: {price:,.2f}\nError: {exc}"
        try:
            send_telegram_message(err_msg)
        except Exception:
            pass
        logger.error("Execution failed: %s", exc, exc_info=True)
        return {"executed": False, "reason": str(exc)}


def run_analysis_cycle(
    exchange,
    liq_monitor: Optional[LiquidationMonitor] = None,
    data_exchange: Any = None,
    data_symbol: str = SYMBOL,
) -> dict:
    _dex = data_exchange if data_exchange is not None else exchange
    _dsym = data_symbol
    try:
        candles_by_tf = _fetch_multi_tf(_dex, _dsym)
        orderbook = fetch_orderbook(_dex, _dsym)
        trades = fetch_recent_trades(_dex, _dsym)
    except Exception as exc:
        logger.error("Data fetch error: %s", exc)
        return {}

    SIGNAL_STATE["cycle"] += 1

    primary_1m = candles_by_tf.get("1m", [])
    primary_15m = candles_by_tf.get("15m", [])
    ohlcv = primary_1m or primary_15m

    try:
        current_price = float(primary_1m[-1][4])
    except Exception:
        current_price = _safe_float(
            orderbook.get("bids", [[0]])[0][0] if orderbook.get("bids") else 0.0
        )

    open_interest = _fetch_open_interest(exchange)
    funding_rate = _fetch_funding_rate(exchange)

    volume_intel = analyze_volume_intelligence(
        exchange=exchange,
        symbol=SYMBOL,
        primary_ohlcv=primary_1m or primary_15m,
        trades=trades,
        use_exchange=False,
    )

    liq_events = liq_monitor.get_events() if liq_monitor else []

    engines_out = (
        run_all_engines(
            orderbook=orderbook,
            trades=trades,
            price=current_price,
            exchange=exchange,
            symbol=SYMBOL,
            cascade_prob=0.0,
            recent_candles=candles_by_tf,
            open_interest=open_interest,
            funding_rate=funding_rate,
            liquidation_events=liq_events,
            performance={},
            volume_intelligence=volume_intel,
            orderbook_snapshots=[orderbook],
        )
        or {}
    )

    # Base features
    features = feature_engine.update(orderbook, trades)
    features = fill_model.enrich(features)
    features = tox_filter.enrich(features)

    # Normalize to a single raw feature dict for all downstream modules
    feat_dict: Dict[str, Any] = features.get("features", features) if isinstance(features, dict) else {}

    # Update impact decay using raw features
    impact_status = impact_tracker.update(current_price, feat_dict)
    logger.info("[FEATURE KEYS] %s", sorted(feat_dict.keys()))

    # Regime / lifecycle
    lifecycle = trade_lifecycle.update(current_price, feat_dict)
    session_guard = trade_lifecycle.session_guard() or {}
    if session_guard.get("block_new_entries"):
        lifecycle["block_new_entries"] = True
        lifecycle["reason"] = session_guard.get("reason", lifecycle.get("reason", "session_guard"))

    logger.info(
        "[REGIME] regime=%s conf=%.3f allow_trade=%s mode=%s scale=%.2f reason=%s",
        feat_dict.get("regime", "unknown"),
        _safe_float(feat_dict.get("regime_confidence", 0.0)),
        feat_dict.get("allow_trade", True),
        feat_dict.get("trade_mode", "balanced"),
        _safe_float(feat_dict.get("position_scale", 1.0)),
        feat_dict.get("regime_reasons", []),
    )
    logger.info(
        "[LIFECYCLE] action=%s block=%s risk_scale=%.3f reason=%s",
        lifecycle.get("action"),
        lifecycle.get("block_new_entries"),
        _safe_float(lifecycle.get("risk_scale", 1.0)),
        lifecycle.get("reason"),
    )

    # Update exit-quality excursion tracking BEFORE position_manager clears state
    if exit_quality_engine is not None and position_manager.has_position():
        try:
            exit_quality_engine.update(current_price)
        except Exception as _eq_upd_err:
            logger.debug("[EXIT_QUALITY] update failed: %s", _eq_upd_err)

    # Position manager update on every cycle + real execution wiring
    pos_action = (
        position_manager.update(current_price, feat_dict)
        if position_manager.has_position()
        else {"action": "NO_POSITION"}
    )
    _pa_action     = pos_action.get("action", "NO_POSITION")
    _pa_reason     = pos_action.get("reason", "")
    _pa_new_sl     = _safe_float(pos_action.get("new_sl", 0.0))
    _pa_reduce_pct = _safe_float(pos_action.get("reduce_size_pct", 0.5))
    _pa_size       = _safe_float(
        pos_action.get("size")
        or (position_manager.position.size if position_manager.has_position() else 0.0)
    )
    logger.info(
        "[POSITION ACTION] action=%s price=%.2f new_sl=%.2f size=%.4f reason=%s",
        _pa_action, current_price, _pa_new_sl, _pa_size, _pa_reason,
    )

    if _pa_action == "CLOSE":
        # ── PositionManager.close() is the single source of truth for PnL.
        #    No PnL computation here — read directly from pos_action.
        _close_entry_price = _safe_float(pos_action.get("entry_price", 0.0))
        _close_exit_price  = _safe_float(pos_action.get("exit_price", current_price))
        _close_side        = str(pos_action.get("side") or "LONG").upper()
        _close_size        = _safe_float(pos_action.get("size", 0.0))
        _close_confidence  = _safe_float(feat_dict.get("confidence", 0.0))
        _real_pnl_pct      = _safe_float(pos_action.get("pnl_pct", 0.0))

        logger.info(
            "[EXIT] side=%s entry=%.2f exit=%.2f pnl=%.4f%% size=%.4f reason=%s",
            _close_side, _close_entry_price, _close_exit_price,
            _real_pnl_pct * 100, _close_size, _pa_reason,
        )

        # Block new entries this cycle — position is being closed
        lifecycle["block_new_entries"] = True
        if LIVE_TRADING:
            try:
                _close_res = engine.close_position(SYMBOL)
                logger.info("[POSITION ACTION] close_position result: %s", _close_res)
            except Exception as _ce:
                logger.error("[POSITION ACTION] close_position failed: %s", _ce)
        else:
            logger.info(
                "[POSITION ACTION] DRY RUN — would close full position %.4f @ %.2f reason=%s",
                _close_size, _close_exit_price, _pa_reason,
            )

        _eq_metrics: Dict[str, Any] = {}
        if exit_quality_engine is not None:
            try:
                _eq_metrics = exit_quality_engine.on_exit(
                    exit_price=_close_exit_price,
                    reason=_pa_reason,
                    regime=str(feat_dict.get("regime", "unknown")),
                    features=feat_dict,
                )
                if _eq_metrics:
                    logger.info(
                        "[EXIT_QUALITY] score=%.3f class=%s mfe_pct=%.4f mae_pct=%.4f eff=%.3f reason=%s",
                        _eq_metrics.get("exit_quality_score", 0.0),
                        _eq_metrics.get("exit_classification", "?"),
                        _eq_metrics.get("mfe_pct", 0.0),
                        _eq_metrics.get("mae_pct", 0.0),
                        _eq_metrics.get("exit_efficiency", 0.0),
                        _pa_reason,
                    )
            except Exception as _eq_exit_err:
                logger.warning("[EXIT_QUALITY] on_exit failed (non-fatal): %s", _eq_exit_err)

        if LEARNING_ENGINE is not None and _eq_metrics:
            try:
                LEARNING_ENGINE.record_exit_quality(
                    mfe_pct=_safe_float(_eq_metrics.get("mfe_pct", 0.0)),
                    mae_pct=_safe_float(_eq_metrics.get("mae_pct", 0.0)),
                    exit_quality_score=_safe_float(_eq_metrics.get("exit_quality_score", 0.5)),
                    exit_classification=str(_eq_metrics.get("exit_classification", "unknown")),
                    holding_seconds=_safe_float(_eq_metrics.get("holding_seconds", 0.0)),
                    reason=_pa_reason,
                    regime=str(feat_dict.get("regime", "unknown")),
                    exit_efficiency=_safe_float(_eq_metrics.get("exit_efficiency", 0.0)),
                    realized_pnl=_safe_float(_eq_metrics.get("realized_pnl", 0.0)),
                    peak_pnl=_safe_float(_eq_metrics.get("peak_pnl", 0.0)),
                    confidence=_safe_float(_eq_metrics.get("confidence", 0.0)),
                    side=_close_side,
                )
            except Exception as _eq_le_err:
                logger.warning("[EXIT_QUALITY] record_exit_quality failed (non-fatal): %s", _eq_le_err)

        # ── Execution quality → learning engine ──────────────────────────
        if LEARNING_ENGINE is not None:
            try:
                _exec_q = pos_action.get("exec_quality") or {}
                _exec_q_score = _safe_float(_exec_q.get("score", 0.5))
                logger.info(
                    "[EXECUTION QUALITY] score=%.4f impact_1s=%s impact_3s=%s impact_score=%.4f",
                    _exec_q_score,
                    str(_exec_q.get("impact_1s")) if _exec_q.get("impact_1s") is not None else "N/A",
                    str(_exec_q.get("impact_3s")) if _exec_q.get("impact_3s") is not None else "N/A",
                    _safe_float(_exec_q.get("impact_score", 1.0)),
                )
                LEARNING_ENGINE.record_execution_quality(
                    score=_exec_q_score,
                    slippage_bps=_safe_float(_exec_q.get("slippage_bps", 0.0)),
                    latency_ms=_safe_float(_exec_q.get("latency_ms", 0.0)),
                    spread_bps=_safe_float(_exec_q.get("spread_bps", 0.0)),
                    side=_close_side,
                    reason=_pa_reason,
                )
            except Exception as _exec_q_err:
                logger.warning("[EXEC_QUALITY] record_execution_quality failed (non-fatal): %s", _exec_q_err)

        if LEARNING_ENGINE is not None:
            try:
                LEARNING_ENGINE.record_closed_trade(
                    signal=_close_side,
                    side=_close_side,
                    entry_price=_close_entry_price,
                    exit_price=_close_exit_price,
                    size=_close_size,
                    entry_ts=None,
                    exit_ts=datetime.now(timezone.utc).isoformat(),
                    confidence=_close_confidence,
                    features_entry=feat_dict,
                    features_exit=feat_dict,
                    reason=_pa_reason,
                    exit_type="forced_close",
                    fees=0.0,
                    pnl_override=_real_pnl_pct,
                    mfe_pct=_safe_float(_eq_metrics.get("mfe_pct")) if _eq_metrics else None,
                    mae_pct=_safe_float(_eq_metrics.get("mae_pct")) if _eq_metrics else None,
                )
                logger.info(
                    "[LEARNING] trade recorded side=%s entry=%.2f exit=%.2f pnl=%.4f size=%.4f reason=%s",
                    _close_side, _close_entry_price, _close_exit_price,
                    _real_pnl_pct, _close_size, _pa_reason,
                )
            except Exception as _le_close_err:
                logger.warning("[LEARNING] record_closed_trade failed (non-fatal): %s", _le_close_err)

        try:
            trade_lifecycle.on_exit(pnl_pct=_real_pnl_pct, reason=_pa_reason)
        except Exception as _tlm_exit_err:
            logger.warning("[LIFECYCLE] on_exit failed: %s", _tlm_exit_err)
        try:
            impact_tracker.reset()
        except Exception as _itr_err:
            logger.debug("[IMPACT_DECAY] reset failed (non-fatal): %s", _itr_err)

    elif _pa_action == "PARTIAL_TAKE_PROFIT":
        if LIVE_TRADING:
            try:
                _pt_res = engine.partial_close_position(SYMBOL, _pa_reduce_pct)
                logger.info("[POSITION ACTION] partial_close result: %s", _pt_res)
            except Exception as _pe:
                logger.error("[POSITION ACTION] partial_close_position failed: %s", _pe)
        else:
            logger.info(
                "[POSITION ACTION] DRY RUN — would partial close %.0f%% of %.4f @ %.2f",
                _pa_reduce_pct * 100, _pa_size, current_price,
            )

    elif _pa_action == "MOVE_SL_TO_BE":
        if LIVE_TRADING and _pa_new_sl > 0:
            try:
                _sl_res = engine.move_stop_loss(SYMBOL, _pa_new_sl)
                logger.info("[POSITION ACTION] move_stop_loss result: %s", _sl_res)
            except Exception as _sle:
                logger.error("[POSITION ACTION] move_stop_loss failed: %s", _sle)
        else:
            logger.info(
                "[POSITION ACTION] DRY RUN — would move SL to %.2f (break-even) reason=%s",
                _pa_new_sl, _pa_reason,
            )

    elif _pa_action == "TRAIL_STOP":
        if LIVE_TRADING and _pa_new_sl > 0:
            try:
                _ts_res = engine.trail_stop(SYMBOL, _pa_new_sl)
                logger.info("[POSITION ACTION] trail_stop result: %s", _ts_res)
            except Exception as _tse:
                logger.error("[POSITION ACTION] trail_stop failed: %s", _tse)
        else:
            logger.info(
                "[POSITION ACTION] DRY RUN — would trail stop to %.2f reason=%s",
                _pa_new_sl, _pa_reason,
            )

    # Hard fail-safe gates before signal generation
    latency_ms = _safe_float(feat_dict.get("latency_ms", 0.0))
    liquidity_score = _safe_float(feat_dict.get("liquidity_score", 1.0))
    spread_bps = _safe_float(feat_dict.get("spread_bps", 0.0))
    if latency_ms > 3000 or liquidity_score < 0.2 or spread_bps > 25:
        logger.warning(
            "[FAILSAFE] blocking trading latency_ms=%.0f liquidity_score=%.3f spread_bps=%.2f",
            latency_ms,
            liquidity_score,
            spread_bps,
        )
        feat_dict["allow_trade"] = False
        lifecycle["block_new_entries"] = True

    if not engines_out.get("liquidity_map"):
        engines_out["liquidity_map"] = _fallback_liquidity_map_from_orderbook(
            orderbook, current_price
        )

    engines_out["volume_intelligence"] = volume_intel
    engines_out["volume_spike"] = volume_intel.get("volume_spike", False)
    engines_out["volume_explosion"] = volume_intel.get("volume_explosion", False)
    engines_out["volume_strength"] = volume_intel.get("volume_strength", 0.0)
    engines_out["mtf_confirmation"] = volume_intel.get("mtf_confirmation", False)
    smc_signal = engines_out.get("smc_signal", {}) or {}

    sma_signal, sma_fast, sma_slow = sma_crossover_signal(ohlcv)
    ob_imb = orderbook_imbalance(orderbook)
    whales = detect_whale_trades(trades)
    whale_sig, _ = whale_net_signal(whales)
    volatility = _estimate_volatility_from_ohlcv(ohlcv)

    best_bid = _safe_float(
        (orderbook.get("bids") or [[current_price]])[0][0]
        if orderbook.get("bids")
        else current_price
    )
    best_ask = _safe_float(
        (orderbook.get("asks") or [[current_price]])[0][0]
        if orderbook.get("asks")
        else current_price
    )
    bid_vol_top = sum(_safe_float(b[1]) for b in orderbook.get("bids", [])[:10])
    ask_vol_top = sum(_safe_float(a[1]) for a in orderbook.get("asks", [])[:10])

    if open_interest > 0:
        oih = [open_interest * 0.98, open_interest * 0.995, open_interest]
    else:
        oih = [900_000.0, 1_000_000.0]

    cascade_prob = _safe_float(engines_out.get("cascade_probability", 0.0))
    if cascade_prob <= 0.0:
        cascade_prob = get_cascade_probability(
            open_interest=max(open_interest, 1_000_000.0),
            oi_history=oih,
            liquidation_cluster=_safe_float(
                (liq_monitor.get_stats()["total_liq"] if liq_monitor else 0.0)
                or 50_000.0
            ),
            bid=best_bid,
            ask=best_ask,
            buy_volume=bid_vol_top,
            sell_volume=ask_vol_top,
            funding_rate=funding_rate,
            whale_flag=bool(whales),
        )

    heatmap = engines_out.get("liquidation_heatmap") or {
        "heat_score": 0,
        "color": "green",
    }
    result = compute_score(
        sma_signal=sma_signal,
        ob_imbalance=ob_imb,
        whale_signal=whale_sig,
        funding_rate=funding_rate,
        cascade_probability=cascade_prob,
    )

    ai_meta = get_ai_score(
        ob_imbalance=ob_imb,
        buy_vol=sum(
            _safe_float(t.get("amount", 0.0))
            * _safe_float(t.get("price", current_price))
            for t in trades
            if str(t.get("side", "")).lower() == "buy"
        ),
        sell_vol=sum(
            _safe_float(t.get("amount", 0.0))
            * _safe_float(t.get("price", current_price))
            for t in trades
            if str(t.get("side", "")).lower() == "sell"
        ),
        volatility=volatility,
        cascade_prob=cascade_prob,
        sma_signal=sma_signal,
        engines_out=engines_out,
        volume_intel=volume_intel,
    )
    ai_meta["engines"] = engines_out

    ai_score = ai_meta["ai_score"]
    confidence = ai_meta["confidence"]

    sig_decision = determine_signal(
        ai_score, confidence, volatility, SIGNAL_STATE, engines_out=engines_out
    )

    institutional = engines_out.get("composite", {}) or {}

    signal_output = signal_engine.generate(feat_dict)
    result["signal_engine_output"] = signal_output

    final_signal = "HOLD"
    execution_direction = "HOLD"
    sniper: dict = {}
    trade_plan = None

    if signal_output.get("signal") in ("LONG", "SHORT", "HOLD"):
        final_signal = signal_output["signal"]
        execution_direction = final_signal
        confidence = _safe_float(signal_output.get("confidence", confidence))

    if lifecycle.get("block_new_entries"):
        logger.info("[LIFECYCLE] entry blocked: %s", lifecycle.get("reason", "blocked"))
        final_signal = "HOLD"
        execution_direction = "HOLD"

    if final_signal in ("LONG", "SHORT") and not trade_lifecycle.can_open_new_trade(feat_dict):
        logger.info("[LIFECYCLE] can_open_new_trade=False")
        final_signal = "HOLD"
        execution_direction = "HOLD"

    normalized_signal = _normalize_trade_signal(final_signal)

    router_decision = order_router.route(normalized_signal, feat_dict, orderbook)
    result["router_decision"] = router_decision

    try:
        meta_result = evaluate_meta_filter(
            features=feat_dict,
            signal=signal_output,
            router_decision=router_decision,
            snapshot=orderbook,
            trades=trades,
        )
    except Exception as _meta_exc:
        logger.warning("[META_FILTER] evaluation error (fallback allow): %s", _meta_exc)
        meta_result = {"allow_trade": True, "risk_scale": 1.0, "reason": "eval_error", "meta_state": {}}

    if not meta_result.get("allow_trade", True):
        logger.info(
            "[META_FILTER] BLOCKED reason=%s score=%.3f",
            meta_result.get("reason", "unknown"),
            _safe_float(meta_result.get("meta_state", {}).get("composite_score", 0.0)),
        )
        normalized_signal = "HOLD"

    balance = _safe_float(engine.get_balance(), 0.0)
    decision = execution_engine.decide(
        signal_payload={
            "signal": normalized_signal,
            "confidence": float(_safe_float(confidence, 0.0)),
        },
        features_payload=feat_dict,
        snapshot=orderbook,
        account_equity=balance,
        meta_result=meta_result,
    )

    risk_scale = _safe_float(lifecycle.get("risk_scale", 1.0))
    regime_scale = _safe_float(feat_dict.get("position_scale", 1.0))
    if decision.get("position_size", 0.0):
        decision["position_size"] = _safe_float(decision["position_size"]) * risk_scale * regime_scale

    # ── Execution-quality position sizing and trade blocking ──────────
    _exec_adj: Dict[str, Any] = {
        "size_multiplier": 1.0,
        "allow_trading":   True,
        "avg_score":       0.5,
        "sample_count":    0,
        "reason":          "engine_unavailable",
    }
    if LEARNING_ENGINE is not None:
        try:
            _exec_adj = LEARNING_ENGINE.get_execution_adjustment()
        except Exception as _ea_err:
            logger.warning("[EXECUTION CONTROL] get_execution_adjustment failed (non-fatal): %s", _ea_err)

    _exec_size_mult  = _safe_float(_exec_adj.get("size_multiplier", 1.0))
    _exec_allow      = bool(_exec_adj.get("allow_trading", True))
    _exec_avg_score  = _safe_float(_exec_adj.get("avg_score", 0.5))

    logger.info(
        "[EXECUTION CONTROL] size_mult=%.2f allow_trading=%s avg_score=%.4f samples=%d reason=%s",
        _exec_size_mult,
        _exec_allow,
        _exec_avg_score,
        int(_exec_adj.get("sample_count", 0)),
        _exec_adj.get("reason", ""),
    )

    if not _exec_allow and normalized_signal in ("LONG", "SHORT"):
        logger.warning(
            "[EXECUTION CONTROL] TRADE BLOCKED — exec quality too low (avg=%.4f). "
            "Forcing HOLD.",
            _exec_avg_score,
        )
        normalized_signal = "HOLD"
    elif _exec_size_mult != 1.0 and decision.get("position_size", 0.0):
        decision["position_size"] = _safe_float(decision["position_size"]) * _exec_size_mult
        logger.info(
            "[EXECUTION CONTROL] size adjusted: base=%.6f mult=%.2f final=%.6f",
            _safe_float(decision["position_size"]) / _exec_size_mult,
            _exec_size_mult,
            _safe_float(decision["position_size"]),
        )

    # ── Liquidation engine + pre-trade quality gate ──────────────────
    _liq_plan: list = []
    _liq_style  = "PASSIVE"
    _liq_pressure = 0.0
    if EXECUTION_LIQUIDATION_ENGINE is not None and normalized_signal in ("LONG", "SHORT"):
        try:
            _mid_px = _safe_float(
                feat_dict.get("mid_price") or feat_dict.get("close", 0.0)
            )
            _sl_px  = _safe_float(decision.get("sl_price") or (
                _mid_px * 0.97 if normalized_signal == "LONG" else _mid_px * 1.03
            ))
            _liq_mkt  = {
                "mid":       _mid_px,
                "spread":    _safe_float(feat_dict.get("spread_bps", 12.5)) / 10_000.0 * _mid_px,
                "vol_24h":   _safe_float(feat_dict.get("vol_24h", 1_000_000.0)),
                "bid_depth": _safe_float(feat_dict.get("bid_depth", 5.0)),
            }
            _liq_acct = {
                "inventory": _safe_float(decision.get("position_size", 0.1)),
                "liq_price": _sl_px,
            }
            _liq_plan     = EXECUTION_LIQUIDATION_ENGINE.execute_step(_liq_mkt, _liq_acct)
            _liq_pressure = EXECUTION_LIQUIDATION_ENGINE.compute_liquidation_pressure(
                _mid_px, _sl_px, _liq_mkt["vol_24h"]
            )
            _liq_style = EXECUTION_LIQUIDATION_ENGINE.compute_execution_style(
                _liq_acct["inventory"], _liq_pressure, _liq_mkt["bid_depth"]
            )
            logger.info(
                "[LIQ_ENGINE] style=%s pressure=%.4f levels=%d",
                _liq_style, _liq_pressure, len(_liq_plan),
            )
        except Exception as _liq_exc:
            logger.warning("[LIQ_ENGINE] non-fatal: %s", _liq_exc)

    _ptq_result: Dict[str, Any] = {
        "execute": True, "quality_score": 0.5,
        "reason": "engine_unavailable", "order_type": "MARKET",
        "position_size_multiplier": 1.0,
    }
    if PRE_TRADE_QUALITY_ENGINE is not None and normalized_signal in ("LONG", "SHORT"):
        try:
            _ptq_result = PRE_TRADE_QUALITY_ENGINE.evaluate(
                features=feat_dict,
                signal={"confidence": _safe_float(confidence, 0.0)},
                meta=meta_result,
                liquidation={"pressure": _liq_pressure},
                order_plan=_liq_plan,
            )
            if not _ptq_result.get("execute", True):
                logger.warning(
                    "[PRE_TRADE_QUALITY] BLOCKED reason=%s quality=%.3f",
                    _ptq_result.get("reason", "unknown"),
                    _ptq_result.get("quality_score", 0.0),
                )
                normalized_signal = "HOLD"
            else:
                _ptq_mult = _safe_float(_ptq_result.get("position_size_multiplier", 1.0))
                if _ptq_mult != 1.0 and decision.get("position_size"):
                    decision["position_size"] = _safe_float(decision["position_size"]) * _ptq_mult
                logger.info(
                    "[PRE_TRADE_QUALITY] score=%.3f order_type=%s size_mult=%.2f",
                    _ptq_result.get("quality_score", 0.5),
                    _ptq_result.get("order_type", "MARKET"),
                    _ptq_mult,
                )
        except Exception as _ptq_exc:
            logger.warning("[PRE_TRADE_QUALITY] non-fatal: %s", _ptq_exc)

    logger.info(
        "[META_FILTER] allow=%s scale=%.2f reason=%s",
        meta_result.get("allow_trade", True),
        _safe_float(meta_result.get("risk_scale", 1.0)),
        meta_result.get("reason", ""),
    )
    logger.info("[ROUTER] %s", router_decision)
    logger.info("[DECISION] %s", decision)

    execution_outcome = {
        "executed": False,
        "paper": False,
        "reason": "not_attempted",
    }

    if (
        normalized_signal in ("LONG", "SHORT")
        and decision.get("execute")
        and router_decision.get("execute")
        and not position_manager.has_position()
    ):
        try:
            execution_outcome = _execute_liquidity_trade(
                execution_signal=normalized_signal,
                price=current_price,
                confidence=_safe_float(confidence, 0.0),
                candles_by_tf=candles_by_tf,
                engines_out=engines_out,
                sl_price=decision.get("sl") or None,
                tp_price=decision.get("tp") or None,
                position_size=decision.get("position_size") or None,
            )

            eo = execution_outcome or {}

            if eo.get("executed") or eo.get("paper"):
                pos_size = _safe_float(eo.get("position_size", 0.0)) or 0.001

                impact_tracker.record_entry(
                    entry_price=current_price,
                    position_size=pos_size,
                    features_payload=feat_dict,
                    direction=normalized_signal,
                )

                try:
                    fees, fee_type = _enforce_entry_fee_metadata(
                        eo.get("fees"),
                        eo.get("fee_type"),
                        eo.get("trade_id"),
                    )
                    position_manager.on_entry(
                        side=normalized_signal,
                        entry_price=current_price,
                        size=pos_size,
                        sl=_safe_float(eo.get("sl", 0.0)),
                        tp=_safe_float(eo.get("tp", 0.0)),
                        fees=fees,
                        fee_type=fee_type,
                        features=feat_dict,
                    )
                except Exception as _pm_err:
                    logger.warning("[POSITION_MANAGER] on_entry failed: %s", _pm_err)

                try:
                    trade_lifecycle.on_entry(
                        side=normalized_signal,
                        entry_price=current_price,
                        size=pos_size,
                        features=feat_dict,
                    )
                except Exception as _tlm_err:
                    logger.warning("[LIFECYCLE] on_entry failed: %s", _tlm_err)

                if exit_quality_engine is not None:
                    try:
                        exit_quality_engine.on_entry(
                            side=normalized_signal,
                            entry_price=current_price,
                            confidence=_safe_float(confidence, 0.0),
                            regime=str(feat_dict.get("regime", "unknown")),
                            size=pos_size,
                        )
                    except Exception as _eq_entry_err:
                        logger.warning("[EXIT_QUALITY] on_entry failed: %s", _eq_entry_err)

        except Exception as exc:
            logger.error("Execution block failed: %s", exc, exc_info=True)
            execution_outcome = {"executed": False, "reason": str(exc)}
    elif not decision.get("execute"):
        execution_outcome["reason"] = decision.get("reason", "execution_engine_blocked")
    elif not router_decision.get("execute"):
        execution_outcome["reason"] = f"router_blocked:{router_decision.get('reason', 'unknown')}"

    result["execution"] = execution_outcome
    result["impact_status"] = impact_status

    log_trade(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": SYMBOL,
            "price": current_price,
            "direction": final_signal,
            "execution_direction": execution_direction,
            "ai_score": ai_score,
            "confidence": confidence,
            "long_score": result.get("long_score"),
            "short_score": result.get("short_score"),
            "dry_run": DRY_RUN,
            "sma_signal": sma_signal,
            "sma10": round(sma_fast, 2) if sma_fast else None,
            "sma30": round(sma_slow, 2) if sma_slow else None,
            "ob_imbalance": round(ob_imb, 6),
            "whale_signal": whale_sig,
            "cascade_prob": round(cascade_prob, 6),
            "heat_score": heatmap.get("heat_score", 0),
            "heat_color": heatmap.get("color", "green"),
            "liq_buy": (liq_monitor.get_stats() if liq_monitor else {"buy_liq": 0})[
                "buy_liq"
            ],
            "liq_sell": (
                liq_monitor.get_stats() if liq_monitor else {"sell_liq": 0}
            )["sell_liq"],
            "engines": engines_out,
            "sniper": sniper,
            "trade_plan": trade_plan,
            "volume_intel": volume_intel,
            "signal_notes": sig_decision.get("notes", []),
            "thresholds": sig_decision.get("thresholds", {}),
            "smc_signal": smc_signal,
            "execution": execution_outcome,
        }
    )

    return result


def run_live() -> None:
    print("BTCUSDT Institutional Signal Bot Started")
    logger.info("Mode: %s", "DRY RUN" if DRY_RUN else "LIVE")
    logger.info("LIVE_TRADING: %s", LIVE_TRADING)
    try:
        exchange = get_exchange()
    except Exception as exc:
        logger.error("Failed to initialise exchange: %s", exc)
        return

    try:
        data_exchange, data_symbol = get_data_exchange()
    except Exception as exc:
        logger.warning(
            "[DATA EXCHANGE] Fallback init failed, using Binance: %s", exc
        )
        data_exchange, data_symbol = exchange, SYMBOL

    liq_monitor = LiquidationMonitor("btcusdt", window_seconds=300)
    liq_monitor.start()
    interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

    while True:
        try:
            run_analysis_cycle(exchange, liq_monitor, data_exchange, data_symbol)
            logger.info("Cycle complete. Next poll in %ds …", interval)
        except KeyboardInterrupt:
            logger.info("Interrupted. Exiting.")
            liq_monitor.stop()
            break
        except Exception as exc:
            logger.error("Cycle error: %s", exc, exc_info=True)
        time.sleep(interval)


def _compute_sharpe(returns: List[float]) -> float:
    try:
        if len(returns) < 2:
            return 0.0
        avg = statistics.mean(returns)
        st = statistics.stdev(returns)
        return 0.0 if st == 0 else (avg / st) * math.sqrt(len(returns))
    except Exception:
        return 0.0


def _generate_backtest_candles(
    n: int = 500,
    start_price: float = 84_000.0,
    seed: int = 42,
) -> list:
    rng = random.Random(seed)
    candles = []
    price = start_price
    t = 1_700_000_000_000

    phase_idx = 0
    phase_defs = [
        ("COMPRESSION", 60),
        ("TRENDING_UP", 80),
        ("EXPANSION", 30),
        ("COMPRESSION", 50),
        ("TRENDING_DOWN", 60),
        ("COMPRESSION", 40),
        ("EXPANSION", 25),
        ("TRENDING_UP", 70),
        ("COMPRESSION", 55),
        ("TRENDING_UP", 50),
    ]
    phases = []
    while len(phases) < n:
        name, length = phase_defs[phase_idx % len(phase_defs)]
        phases.extend([name] * length)
        phase_idx += 1
    phases = phases[:n]

    wick_counter = 0
    for i, phase in enumerate(phases):
        o = price
        if phase == "COMPRESSION":
            drift = rng.uniform(-3.0, 3.0)
            noise = rng.gauss(0, 4)
            c = price + drift + noise
            wick_counter += 1
            if wick_counter % 20 == 0:
                h = max(o, c) + rng.uniform(900, 1400)
                l = min(o, c) - rng.uniform(900, 1400)
            else:
                h = max(o, c) + rng.uniform(15, 35)
                l = min(o, c) - rng.uniform(15, 35)
            v = rng.uniform(55_000, 75_000)
        elif phase == "TRENDING_UP":
            drift = rng.uniform(60, 160)
            noise = rng.gauss(0, 30)
            c = price + drift + noise
            h = max(o, c) + rng.uniform(40, 100)
            l = min(o, c) - rng.uniform(20, 60)
            v = rng.uniform(80_000, 150_000)
        elif phase == "TRENDING_DOWN":
            drift = rng.uniform(-160, -60)
            noise = rng.gauss(0, 30)
            c = price + drift + noise
            h = max(o, c) + rng.uniform(20, 60)
            l = min(o, c) - rng.uniform(40, 100)
            v = rng.uniform(80_000, 150_000)
        else:
            drift = rng.choice([-1, 1]) * rng.uniform(200, 500)
            noise = rng.gauss(0, 100)
            c = price + drift + noise
            h = max(o, c) + rng.uniform(200, 500)
            l = min(o, c) - rng.uniform(200, 500)
            v = rng.uniform(200_000, 500_000)

        c = max(c, price * 0.80)
        c = min(c, price * 1.20)
        h = max(h, max(o, c))
        l = min(l, min(o, c))
        candles.append(
            [t, round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v, 2)]
        )
        price = c
        t += 3_600_000

    return candles


def run_backtest(
    symbol: str = SYMBOL,
    timeframe: str = "1h",
    limit: int = 500,
    fast_sma: int = 10,
    slow_sma: int = 30,
    capital: float = 10_000.0,
    ohlcv_data: list = None,
) -> dict:
    print("BTCUSDT Institutional Signal Bot Started")
    logger.info("=== BACKTEST START: %s [%s] ===", symbol, timeframe)
    if ohlcv_data is not None:
        ohlcv_all = list(ohlcv_data)
    else:
        try:
            exchange = get_exchange()
            ohlcv_all = fetch_ohlcv(exchange, symbol, timeframe, limit)
        except Exception as exc:
            logger.error("Backtest fetch error: %s", exc)
            return {}

    if len(ohlcv_all) < slow_sma + 2:
        logger.error("Not enough candles (%d) for backtest", len(ohlcv_all))
        return {}

    trades_log = []
    position = None
    equity = capital
    peak_equity = capital
    max_drawdown = 0.0
    returns = []
    local_state = {
        "cycle": 0,
        "last_signal_cycle": -9999,
        "last_trade_cycle": -9999,
        "bias_count": 0,
        "last_direction": None,
        "cooldown_candles": 3,
        "min_signal_every": 30,
    }
    msd = MarketStateDetector()

    for i in range(slow_sma + 1, len(ohlcv_all)):
        local_state["cycle"] += 1
        window = ohlcv_all[: i + 1]
        current_price = _safe_float(window[-1][4])
        ts = datetime.fromtimestamp(window[-1][0] / 1000, tz=timezone.utc).isoformat()

        sma_signal, _, _ = sma_crossover_signal(window, fast_sma, slow_sma)
        volatility = _estimate_volatility_from_ohlcv(window)

        candle_vol = _safe_float(window[-1][5])
        _trade_side_bt = (
            "BUY" if current_price >= _safe_float(window[-1][1]) else "SELL"
        )
        _base_size = max(1.0, candle_vol / max(current_price, 1.0))
        _rand_jitter = random.uniform(0.80, 1.20)
        if _trade_side_bt == "BUY":
            _bid_mul = random.uniform(1.5, 3.0) * _rand_jitter
            _ask_mul = 1.0
        else:
            _bid_mul = 1.0
            _ask_mul = random.uniform(1.5, 3.0) * _rand_jitter
        synthetic_orderbook = {
            "bids": [
                [current_price - 300.0, _base_size * _bid_mul],
                [current_price - 500.0, _base_size * _bid_mul * 0.5],
                [current_price - 750.0, _base_size * _bid_mul * 0.3],
            ],
            "asks": [
                [current_price + 300.0, _base_size * _ask_mul],
                [current_price + 500.0, _base_size * _ask_mul * 0.5],
                [current_price + 750.0, _base_size * _ask_mul * 0.3],
            ],
        }
        synthetic_trades = [
            {
                "price": current_price,
                "amount": max(candle_vol / max(current_price, 1.0), 1e-6),
                "side": _trade_side_bt,
            }
        ]

        volume_intel = analyze_volume_intelligence(
            exchange=None,
            symbol=symbol,
            primary_ohlcv=window[-120:],
            trades=synthetic_trades,
            use_exchange=False,
        )

        engines_out = run_all_engines(
            orderbook=synthetic_orderbook,
            trades=synthetic_trades,
            price=current_price,
            exchange=None,
            symbol=symbol,
            cascade_prob=0.0,
            recent_candles=window,
            open_interest=1_000_000.0 + _safe_float(window[-1][5]) * 10.0,
            funding_rate=0.0,
            liquidation_events=[],
            performance={},
            volume_intelligence=volume_intel,
            orderbook_snapshots=[synthetic_orderbook],
            market_state_detector=msd,
        )

        ob_imb = orderbook_imbalance(synthetic_orderbook)
        cascade_prob = _safe_float(engines_out.get("cascade_probability", 0.0))
        whales = detect_whale_trades(synthetic_trades)
        whale_sig, _ = whale_net_signal(whales)

        result = compute_score(
            sma_signal=sma_signal,
            ob_imbalance=ob_imb,
            whale_signal=whale_sig,
            funding_rate=0.0,
            cascade_probability=cascade_prob,
        )

        ai_meta = get_ai_score(
            ob_imbalance=ob_imb,
            buy_vol=sum(
                _safe_float(t["amount"]) * _safe_float(t["price"])
                for t in synthetic_trades
                if str(t.get("side", "")).lower() == "buy"
            ),
            sell_vol=sum(
                _safe_float(t["amount"]) * _safe_float(t["price"])
                for t in synthetic_trades
                if str(t.get("side", "")).lower() == "sell"
            ),
            volatility=volatility,
            cascade_prob=cascade_prob,
            sma_signal=sma_signal,
            engines_out=engines_out,
            volume_intel=volume_intel,
        )
        ai_meta["engines"] = engines_out

        ai_score = ai_meta["ai_score"]
        confidence = ai_meta["confidence"]
        sig_decision = determine_signal(
            ai_score, confidence, volatility, local_state, engines_out=engines_out
        )
        final_signal = sig_decision["signal"]

        execution_direction = result["direction"]
        if final_signal in ("LONG", "STRONG_LONG"):
            execution_direction = "LONG"
        elif final_signal in ("SHORT", "STRONG_SHORT"):
            execution_direction = "SHORT"

        liquidity_map = engines_out.get("liquidity_map", {})
        sniper = detect_entry_trigger(
            price=current_price,
            liquidity_map=liquidity_map,
            engines=engines_out,
            ai_score=ai_score,
            confidence=confidence,
            volume_intel=volume_intel,
        )

        trade_plan = None
        if final_signal != "HOLD":
            trade_plan = build_trade_plan(
                price=current_price,
                direction=execution_direction,
                liquidity_map=liquidity_map,
            )

        _ms = engines_out.get("market_state", {})
        _lm_zones = (
            (liquidity_map.get("liquidity_map") or [])
            if isinstance(liquidity_map, dict)
            else []
        )
        _nearest_dist = (
            round(
                abs(
                    min(
                        _lm_zones,
                        key=lambda z: abs(_safe_float(z.get("price")) - current_price),
                        default={"price": current_price},
                    ).get("price", current_price)
                    - current_price
                ),
                2,
            )
            if _lm_zones
            else -1
        )
        _ob_imb_disp = round(
            _safe_float(engines_out.get("orderbook_imbalance", 0.0)), 4
        )
        _sweep = bool((engines_out.get("liquidity_sweep") or {}).get("sweep"))
        _trigger = sniper.get("trigger", False)
        print(
            f"[C{local_state['cycle']:04d}] {ts[:16]} | {_ms.get('state', '?'):12s} allow={_ms.get('allow_trade', '?')} | "
            f"ai={ai_score:+.3f} conf={confidence:.2f} | sig={final_signal:13s} | "
            f"liq_dist={_nearest_dist:6} sweep={_sweep} | imb={_ob_imb_disp:+.4f} | "
            f"{'>>> PREPARE <<<' if final_signal != 'HOLD' else ''}"
            f"{'>>> TRIGGERED <<<' if _trigger else ''}"
        )

        if final_signal != "HOLD" and sniper.get("trigger") and position is None:
            if (
                local_state["cycle"] - local_state["last_trade_cycle"]
                >= local_state["cooldown_candles"]
            ):
                entry = current_price
                if trade_plan:
                    _plan_entry = _safe_float(trade_plan.get("entry", entry))
                    _plan_sl = _safe_float(trade_plan["sl"])
                    _tp_raw = trade_plan["tp"]
                    _plan_tp = _safe_float(
                        _tp_raw[2]
                        if isinstance(_tp_raw, list) and len(_tp_raw) >= 3
                        else _tp_raw
                    )
                    _risk = max(abs(_plan_entry - _plan_sl), 50.0)
                    _reward = max(abs(_plan_tp - _plan_entry), _risk * 2.0)
                    if execution_direction == "LONG":
                        sl = entry - _risk
                        tp = entry + _reward
                    else:
                        sl = entry + _risk
                        tp = entry - _reward
                else:
                    sl = entry * (0.995 if execution_direction == "LONG" else 1.005)
                    tp = entry * (1.010 if execution_direction == "LONG" else 0.990)
                position = {
                    "side": execution_direction,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "entry_ts": ts,
                }
                local_state["last_trade_cycle"] = local_state["cycle"]

        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]
            hit_sl = current_price <= sl if side == "LONG" else current_price >= sl
            hit_tp = current_price >= tp if side == "LONG" else current_price <= tp
            exit_on_flip = (side == "LONG" and execution_direction == "SHORT") or (
                side == "SHORT" and execution_direction == "LONG"
            )
            if hit_sl or hit_tp or exit_on_flip:
                pnl_pct = (
                    ((current_price - entry) / entry)
                    if side == "LONG"
                    else ((entry - current_price) / entry)
                )
                pnl = equity * pnl_pct * 0.5
                equity += pnl
                returns.append(pnl_pct)
                peak_equity = max(peak_equity, equity)
                max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
                trades_log.append(
                    {
                        "entry_ts": position["entry_ts"],
                        "exit_ts": ts,
                        "side": side,
                        "entry_price": round(entry, 2),
                        "exit_price": round(current_price, 2),
                        "pnl": round(pnl, 4),
                        "pnl_pct": round(pnl_pct * 100, 4),
                        "equity": round(equity, 4),
                        "ai_score": ai_score,
                        "confidence": confidence,
                        "final_signal": final_signal,
                        "trade_plan": trade_plan,
                    }
                )
                position = None

    total_trades = len(trades_log)
    wins = sum(1 for t in trades_log if _safe_float(t.get("pnl", 0.0)) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0
    total_return = (equity - capital) / capital * 100.0
    sharpe = _compute_sharpe(returns)

    summary = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_analysed": len(ohlcv_all),
        "initial_capital": capital,
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return, 4),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "trades": trades_log,
    }
    with open(BACKTEST_RESULT_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("=== BACKTEST COMPLETE ===")
    logger.info(
        "Trades: %d | Win rate: %.2f%% | Return: %.2f%% | Max DD: %.2f%% | Sharpe: %.3f",
        total_trades,
        win_rate,
        total_return,
        max_drawdown * 100,
        sharpe,
    )
    return summary


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "backtest":
        run_backtest()
    else:
        run_live()
