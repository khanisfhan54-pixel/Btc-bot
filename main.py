# main.py
# main.py – entry point of the trading bot
#!/usr/bin/env python3
from __future__ import annotations

import sys
import os
import json
import time
import math
import numbers
import random
import logging
import statistics
import numpy as np
import threading
import traceback
import socket
import concurrent.futures
import uuid
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple
from trading_utils import safe_float, clamp, validate_alpha
from thread_safe_wrappers import ThreadSafeFeatureEngine, ThreadSafeAlphaPredictor
from venue_basis import VenueBasisNormalizer

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
SIGNAL_ONLY_MODE = os.environ.get("SIGNAL_ONLY_MODE", "true").strip().lower() == "true"

# ADDED: safety layer for live execution
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"

SYMBOL = "BTC/USDT"
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.json")
BACKTEST_RESULT_PATH = os.path.join(os.path.dirname(__file__), "backtest_result.json")
BACKTEST_SEED = int(os.environ.get("BACKTEST_SEED", "42"))
_LOG_TRADE_LOCK = threading.Lock()
RISK_PERCENT_PER_TRADE = float(os.environ.get("RISK_PERCENT_PER_TRADE", "0.5"))
EXCHANGE_MIN_NOTIONAL_USD = float(os.environ.get("EXCHANGE_MIN_NOTIONAL_USD", "10.0"))
EXCHANGE_MAX_NOTIONAL_USD = float(os.environ.get("EXCHANGE_MAX_NOTIONAL_USD", "100000.0"))
MAX_REGIME_STALENESS_SECONDS = float(os.environ.get("MAX_REGIME_STALENESS_SECONDS", "300"))
MAX_FEATURE_STALENESS_SECONDS = float(os.environ.get("MAX_FEATURE_STALENESS_SECONDS", "60"))
BASIS_HALT_THRESHOLD_PCT = float(os.environ.get("BASIS_HALT_THRESHOLD_PCT", "0.5"))
BACKTEST_RISK_PCT = float(os.environ.get("BACKTEST_RISK_PCT", "0.005"))
RECONCILIATION_BLOCK_SECONDS = int(os.environ.get("RECONCILIATION_BLOCK_SECONDS", "300"))
FETCH_TIMEOUT_SECONDS = float(os.environ.get("FETCH_TIMEOUT_SECONDS", "30"))
MAX_CONSECUTIVE_CYCLE_ERRORS = int(os.environ.get("MAX_CONSECUTIVE_CYCLE_ERRORS", "10"))
CIRCUIT_BREAKER_SLEEP_SECONDS = float(os.environ.get("CIRCUIT_BREAKER_SLEEP_SECONDS", "300"))
_reconciliation_blocks: Dict[str, float] = {}
_ORDERBOOK_SNAPSHOTS: Deque[Dict[str, Any]] = deque(maxlen=8)
_ORDERBOOK_SNAPSHOTS_LOCK = threading.RLock()

_COLD_START_LOCK = threading.Lock()
_RECONCILIATION_LOCK = threading.Lock()
_SHARED_FETCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="market-fetch")

_RETRYABLE_EXCHANGE_ERRORS = (Exception,)

def _retry_exchange_call(func, *args, max_retries: int = 3, base_delay: float = 0.5, call_name: str = "exchange_call", **kwargs):
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except _RETRYABLE_EXCHANGE_ERRORS as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = min(base_delay * (2 ** attempt), 8.0)
            logger.warning("[RETRY] %s failed attempt=%d/%d sleeping=%.2fs err=%s", call_name, attempt + 1, max_retries + 1, delay, exc)
            time.sleep(delay)
    raise RuntimeError(f"{call_name} failed after {max_retries + 1} attempts: {last_exc}")

def _fetch_market_snapshot(data_exchange, data_symbol: str, execution_exchange, execution_symbol: str = SYMBOL) -> Dict[str, Any]:
    ts = time.time()
    candles_by_tf = _fetch_multi_tf(data_exchange, data_symbol)
    analysis_orderbook = _retry_exchange_call(fetch_orderbook, data_exchange, data_symbol, call_name="analysis_orderbook")
    execution_orderbook = _retry_exchange_call(fetch_orderbook, execution_exchange, execution_symbol, call_name="execution_orderbook")
    trades = _retry_exchange_call(fetch_recent_trades, data_exchange, data_symbol, call_name="recent_trades")
    return {
        "snapshot_ts": ts,
        "candles_by_tf": candles_by_tf,
        "analysis_orderbook": analysis_orderbook,
        "execution_orderbook": execution_orderbook,
        "trades": trades,
    }


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

ENGINE_IS_FALLBACK: bool
if "execution" in getattr(ExecutionEngine, "__module__", ""):
    ENGINE_IS_FALLBACK = False
else:
    ENGINE_IS_FALLBACK = True
CREDENTIALS_MISSING: bool = False
_cold_start_complete: bool = False

try:
    from queue_fill_model import QueueFillModel
    from toxicity_filter import ToxicityFilter
    from order_router import OrderRouter
    from impact_decay import ImpactDecay
    from position_manager import PositionManager
    from trade_lifecycle_manager import TradeLifecycleManager
except Exception as _new_module_import_err:
    _tb_lines = traceback.format_exc().splitlines()[-3:]
    _failed_mod = getattr(_new_module_import_err, "name", None) or str(_new_module_import_err).split("'")[1] if "'" in str(_new_module_import_err) else "unknown"
    _boot_msg = (
        f"[BOOT] Critical module import failed\n"
        f"timestamp={datetime.now(timezone.utc).isoformat()}\n"
        f"hostname={socket.gethostname()}\n"
        f"module={_failed_mod}\n"
        f"exception={type(_new_module_import_err).__name__}: {_new_module_import_err}\n"
        f"traceback_last_frames={' | '.join(_tb_lines)}"
    )
    logger.critical("%s", _boot_msg, exc_info=True)
    try:
        _tg_thread = threading.Thread(target=send_telegram_message, args=(_boot_msg,), daemon=True)
        _tg_thread.start()
        _tg_thread.join(timeout=10.0)
    except Exception as _tg_exc:
        logger.error("[BOOT] Telegram alert failed during import error: %s", _tg_exc, exc_info=True)
    try:
        sys.stderr.write(_boot_msg + "\n")
    except Exception:
        pass
    raise RuntimeError(_boot_msg) from _new_module_import_err

if False:

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

        def get_correlation_id(self) -> str:
            return ""

try:
    from learning_engine import LEARNING_ENGINE
except Exception:
    LEARNING_ENGINE = None

try:
    from advanced_regime_engine import AdvancedRegimeEngine
except Exception as _regime_import_err:
    logger.warning("advanced_regime_engine import failed: %s", _regime_import_err)
    AdvancedRegimeEngine = None  # type: ignore

try:
    from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha
except Exception as _alpha_import_err:
    logger.warning("alpha_liquidity_sweep_predictor import failed: %s", _alpha_import_err)
    LiquiditySweepAlpha = None  # type: ignore

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

try:
    from capital_allocator import CapitalAllocator
except ImportError as _ca_err:
    logger.critical("[BOOT] Critical module import failed: %s — HALTING", _ca_err, exc_info=True)
    raise

engine           = ExecutionEngine()
feature_engine   = ThreadSafeFeatureEngine(FeatureEngine())
signal_engine    = SignalEngine()
execution_engine = ExecutionLogic(learning_engine=LEARNING_ENGINE)
fill_model       = QueueFillModel()
tox_filter       = ToxicityFilter()
order_router     = OrderRouter()
impact_tracker   = ImpactDecay()
position_manager = PositionManager()
trade_lifecycle  = TradeLifecycleManager()
capital_allocator = CapitalAllocator()
basis_normalizer = VenueBasisNormalizer(halt_threshold_pct=BASIS_HALT_THRESHOLD_PCT)
SIGNAL_PIPELINE_CONFIG: Dict[str, Any] = {
    "enable_regime_engine": True,
    "signal_only_mode": SIGNAL_ONLY_MODE,
    "regime_update_frequency_sec": 1.0,
}
_last_regime_update_ts = 0.0
_regime_context_timestamp: float = 0.0
_last_regime_context: Dict[str, Any] = {"regime": "UNKNOWN", "confidence": 0.0, "features": {}}
_prev_close: Optional[float] = None
_last_valid_features: Optional[dict] = None
_last_valid_features_ts: float = 0.0
_feature_type_error_count: int = 0
_ANALYSIS_STATE_LOCK = threading.Lock()
regime_engine = None
if AdvancedRegimeEngine is not None:
    try:
        regime_engine = AdvancedRegimeEngine()
    except Exception as _regime_init_err:
        logger.warning("AdvancedRegimeEngine init failed; continuing without it: %s", _regime_init_err)
        regime_engine = None
alpha_predictor = None
try:
    from backtest_engine import BacktestEngine, BacktestConfig

    from alpha_orchestrator import (
        AlphaOrchestrator,
        OrchestratorConfig,
        AlphaSignal,
        RegimeContext,
        FeatureQuality,
        ExecutionState,
    )
except Exception as _ao_exc:
    logger.critical("alpha_orchestrator import failed: %s", _ao_exc)
    raise
alpha_orchestrator = AlphaOrchestrator(OrchestratorConfig(signal_weights={"signal_engine": 1.0}))

try:
    from engine import (
        run_all_engines,
        analyze_volume_intelligence,
        get_cascade_probability,
        MarketStateDetector,
        evaluate_meta_filter,
        apply_meta_to_decision,
        get_shared_alpha_predictor,
        _default_alpha,
        SniperExecutionEngine,
    )
except Exception as _e:
    logger.warning("Engines import failed: %s", _e)

    def _default_alpha() -> dict:
        return {
            "direction": "NEUTRAL",
            "confidence": 0.5,
            "prob_above": 0.5,
            "prob_below": 0.5,
            "micro_prob": 0.5,
            "macro_prob": 0.5,
        }

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
                "allow_trade": False,
                "bias": 0.0,
                "volatility": 0.0,
                "compression": 1.0,
                "timeframe_breakdown": {},
                "reason": "engine_import_fallback_fail_closed",
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
            "alpha": _default_alpha(),
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

    class MarketStateDetector:
        def detect(self, *args, **kwargs):
            return {
                "state": "CHOPPY",
                "substate": "CHOPPY",
                "allow_trade": False,
                "bias": 0.0,
                "volatility": 0.0,
                "compression": 1.0,
                "timeframe_breakdown": {},
                "reason": "market_state_fallback_fail_closed",
            }

    def evaluate_meta_filter(*args, **kwargs):
        return {"allow_trade": False, "risk_scale": 0.0, "reason": "engine_unavailable", "meta_state": {"fail_closed": True}}

    def get_shared_alpha_predictor():
        try:
            import engine as _engine_mod
            getter = getattr(_engine_mod, "get_shared_alpha_predictor", None)
            if callable(getter):
                return getter()
            logger.warning("[ALPHA] engine.get_shared_alpha_predictor unavailable in fallback path")
        except Exception as _alpha_getter_exc:
            logger.warning("[ALPHA] failed to resolve shared alpha getter in fallback path: %s", _alpha_getter_exc)
        return None

    def apply_meta_to_decision(decision, meta_result):
        return decision if isinstance(decision, dict) else {}

    SniperExecutionEngine = None  # type: ignore

_signal_pipeline_engine = None
if LiquiditySweepAlpha is not None:
    try:
        shared_alpha = get_shared_alpha_predictor()
        if shared_alpha is None:
            logger.warning("Shared alpha predictor getter returned None; disabling alpha predictor.")
            alpha_predictor = None
        else:
            alpha_predictor = ThreadSafeAlphaPredictor(shared_alpha)
    except Exception as _alpha_shared_err:
        logger.warning("Shared alpha predictor init failed; disabling alpha predictor: %s", _alpha_shared_err)
        alpha_predictor = None
if globals().get("SniperExecutionEngine") is not None:
    try:
        _signal_pipeline_engine = SniperExecutionEngine(
            symbol=SYMBOL.replace("/", ""),
            regime_engine=regime_engine,
            feature_engine=feature_engine,
            predictor=alpha_predictor,
            config=SIGNAL_PIPELINE_CONFIG,
        )
    except Exception as _spe_err:
        logger.warning("Signal pipeline engine init failed (non-fatal): %s", _spe_err)
        _signal_pipeline_engine = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    return safe_float(value, default=default)


def _append_orderbook_snapshot(orderbook: Dict[str, Any], timestamp: Optional[float] = None) -> None:
    if not isinstance(orderbook, dict):
        return

    def _normalize_levels(levels: Any) -> List[List[float]]:
        normalized: List[List[float]] = []
        for lvl in (levels or [])[:20]:
            try:
                p = _safe_float(lvl[0], 0.0)
                q = _safe_float(lvl[1], 0.0)
                if p > 0.0 and q >= 0.0 and np.isfinite(p) and np.isfinite(q):
                    normalized.append([float(p), float(q)])
            except Exception:
                continue
        return normalized

    snapshot = {
        "bids": _normalize_levels(orderbook.get("bids")),
        "asks": _normalize_levels(orderbook.get("asks")),
        "timestamp": float(_safe_float(timestamp, time.time())),
    }
    with _ORDERBOOK_SNAPSHOTS_LOCK:
        _ORDERBOOK_SNAPSHOTS.append(snapshot)


def _get_orderbook_snapshot_history() -> List[Dict[str, Any]]:
    with _ORDERBOOK_SNAPSHOTS_LOCK:
        return list(_ORDERBOOK_SNAPSHOTS)


def _enforce_entry_fee_metadata(fees, fee_type, trade_id=None):
    fees_val = _safe_float(fees, 0.0)

    fee_type_val = str(fee_type).lower().strip() if fee_type is not None else None

    if fee_type_val not in ("quote", "pct"):
        logger.warning(
            "Invalid or missing fee_type in main.py. Defaulting to pct. trade_id=%s",
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
    if not isinstance(orderbook, dict):
        logger.warning("[ORDERBOOK] invalid orderbook type=%s for imbalance; returning 0.0", type(orderbook).__name__)
        return 0.0
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    if not isinstance(bids, list) or not isinstance(asks, list):
        logger.warning("[ORDERBOOK] malformed bids/asks structure for imbalance; returning 0.0")
        return 0.0

    def _sum_side_volume(levels: list) -> float:
        volume = 0.0
        for lvl in levels:
            if not isinstance(lvl, (list, tuple)) or len(lvl) < 2:
                continue
            px = _safe_float(lvl[0], float("nan"))
            sz = _safe_float(lvl[1], float("nan"))
            if not math.isfinite(px) or not math.isfinite(sz) or px <= 0.0 or sz < 0.0:
                continue
            volume += sz
        return volume

    bid_vol = _sum_side_volume(bids)
    ask_vol = _sum_side_volume(asks)
    total = bid_vol + ask_vol
    if total <= 0.0 or not math.isfinite(total):
        logger.warning("[ORDERBOOK] non-finite or empty volume in imbalance; returning 0.0")
        return 0.0
    return (bid_vol - ask_vol) / total


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
    if CREDENTIALS_MISSING:
        logger.debug("[EXCHANGE] connecting without credentials (read-only mode)")
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
    if CREDENTIALS_MISSING:
        logger.debug("[EXCHANGE] connecting without credentials (read-only mode)")
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
    return _retry_exchange_call(exchange.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit, call_name=f"fetch_ohlcv[{timeframe}]")


def fetch_orderbook(exchange, symbol=SYMBOL, limit=50):
    return _retry_exchange_call(exchange.fetch_order_book, symbol, limit=limit, call_name="fetch_orderbook")


def fetch_recent_trades(exchange, symbol=SYMBOL, limit=200):
    return _retry_exchange_call(exchange.fetch_trades, symbol, limit=limit, call_name="fetch_recent_trades")


def _fetch_multi_tf(exchange, symbol: str = SYMBOL) -> Dict[str, list]:
    out: Dict[str, list] = {"1m": [], "5m": [], "15m": [], "1h": []}
    # ccxt enableRateLimit=True is shared across concurrent calls on same exchange instance.
    futures = {
        tf: _SHARED_FETCH_EXECUTOR.submit(fetch_ohlcv, exchange, symbol, tf, 240)
        for tf in ("1m", "5m", "15m", "1h")
    }
    for tf, fut in futures.items():
            try:
                out[tf] = fut.result(timeout=FETCH_TIMEOUT_SECONDS)
            except Exception as exc:
                logger.warning("[FETCH] timeframe=%s fetch failed: %s", tf, exc, exc_info=True)
                out[tf] = []
    if all(not out[tf] for tf in ("1m", "5m", "15m", "1h")):
        logger.error("[FETCH] all timeframe fetches failed")
    return out


def _prune_reconciliation_blocks() -> None:
    now = time.time()
    removed = 0
    with _RECONCILIATION_LOCK:
        for sym, expiry in list(_reconciliation_blocks.items()):
            if float(expiry) <= now:
                _reconciliation_blocks.pop(sym, None)
                removed += 1
    logger.debug("[RECONCILIATION] pruned %d expired block(s)", removed)


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
    with _LOG_TRADE_LOCK:
        tmp_path = TRADE_LOG_PATH + ".tmp"
        try:
            with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            trades = []
        trades.append(entry)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)
        os.replace(tmp_path, TRADE_LOG_PATH)
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


def _normalize_trade_id(value: Any) -> str:
    v = str(value or "").strip()
    return v if v else ""


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
    correlation_id: str = "",
) -> str:
    cid = (correlation_id or "")[:12]
    if _reconciliation_blocks.get(SYMBOL, 0.0) > time.time():
        return f"{title}\nCID: {cid or 'n/a'}\nStatus: reconciliation_block_active"
    lines = [title]
    lines.append(f"CID: {cid or 'n/a'}")
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


def _compute_atr_sl_tp(candles: list, current_price: float) -> tuple[Optional[float], Optional[float]]:
    if len(candles) < 15:
        logger.warning("[EXECUTION] Insufficient candles (%d) for ATR — skipping trade", len(candles))
        return None, None
    highs = [float(c[2]) for c in candles[-14:]]
    lows = [float(c[3]) for c in candles[-14:]]
    closes = [float(c[4]) for c in candles[-15:-1]]
    true_ranges = [max(h - l, abs(h - c), abs(l - c)) for h, l, c in zip(highs, lows, closes)]
    atr = sum(true_ranges) / len(true_ranges)
    sl_pct = max(0.003, min(0.015, atr / max(current_price, 1e-9)))
    tp_pct = sl_pct * 2.0
    return sl_pct, tp_pct

def _execute_liquidity_trade(
    execution_signal: str,
    price: float,
    confidence: float,
    candles_by_tf: Dict[str, Any],
    engines_out: Dict[str, Any],
    analysis_price: Optional[float] = None,
    execution_orderbook: Optional[Dict[str, Any]] = None,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_tp_price_space: str = "execution",
    position_size: Optional[float] = None,
    correlation_id: str = "",
) -> Dict[str, Any]:
    """
    Execute a trade signal.

    Uses sl_price / tp_price / position_size from ExecutionLogic.decide() when provided.
    Falls back to computing them from market data when not provided (e.g. legacy path).
    """
    market_data: Optional[Dict[str, Any]] = None
    cid = (correlation_id or "")[:12]
    if _reconciliation_blocks.get(SYMBOL, 0.0) > time.time():
        return {"executed": False, "reason": "reconciliation_block_active", "correlation_id": correlation_id or ""}
    def _seed_val(v):
        if v is None:
            return "NA"
        return f"{round(_safe_float(v), 8):.8f}"

    _trade_seed = "|".join(
        [
            str(correlation_id or "NO_CID"),
            str(execution_signal or ""),
            f"{_safe_float(price):.8f}",
            _seed_val(sl_price),
            _seed_val(tp_price),
            _seed_val(position_size),
            str(correlation_id or "NO_CID"),
        ]
    )
    deterministic_trade_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, _trade_seed))

    execution_ref_price = _safe_float(price, float("nan"))
    analysis_ref_price = _safe_float(analysis_price, execution_ref_price)
    if execution_ref_price <= 0.0 and isinstance(execution_orderbook, dict):
        execution_ref_price = _safe_float((execution_orderbook.get("bids") or [[0.0]])[0][0], 0.0)
    if execution_ref_price <= 0.0 or not math.isfinite(execution_ref_price):
        return {"executed": False, "reason": "basis_unavailable_execution_price", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
    if analysis_ref_price <= 0.0 or not math.isfinite(analysis_ref_price):
        return {"executed": False, "reason": "basis_unavailable_analysis_price", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}

    basis_delta = execution_ref_price - analysis_ref_price
    if not math.isfinite(basis_delta):
        return {"executed": False, "reason": "invalid_basis_delta", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
    if abs(basis_delta) > 1e-8:
        logger.info(
            "[BASIS] analysis_price=%.6f execution_price=%.6f basis_delta=%.6f cid=%s",
            analysis_ref_price,
            execution_ref_price,
            basis_delta,
            cid,
        )

    # --- Resolve SL / TP ---
    if sl_price is None or tp_price is None or _safe_float(sl_price) <= 0 or _safe_float(tp_price) <= 0:
        market_data = _build_execution_market_data(candles_by_tf, engines_out)
        if not market_data:
            logger.info("Skipping execution: liquidity market data missing or sweep not confirmed.")
            return {"executed": False, "reason": "missing_liquidity_data", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
        try:
            sl_price, tp_price = calculate_liquidity_sl_tp(execution_signal, analysis_ref_price, market_data)
        except Exception as exc:
            logger.warning("calculate_liquidity_sl_tp failed: %s", exc)
            atr_sl_tp = _compute_atr_sl_tp(candles_by_tf.get("1h", []), analysis_ref_price)
            if atr_sl_tp[0] is None:
                return {"executed": False, "reason": "no_sl_tp_available", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
            sl_pct, tp_pct = atr_sl_tp
            if execution_signal == "LONG":
                sl_price = analysis_ref_price * (1.0 - sl_pct)
                tp_price = analysis_ref_price * (1.0 + tp_pct)
            else:
                sl_price = analysis_ref_price * (1.0 + sl_pct)
                tp_price = analysis_ref_price * (1.0 - tp_pct)

    try:
        if sl_price is None or tp_price is None:
            return {"executed": False, "reason": "invalid_sl_tp", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}

        sltp_space = str(sl_tp_price_space or "execution").strip().lower()
        if sltp_space not in {"analysis", "execution"}:
            return {"executed": False, "reason": "invalid_sl_tp_price_space", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
        if sltp_space == "analysis":
            logger.info("[BASIS] applying analysis->execution SL/TP conversion once delta=%.6f cid=%s", basis_delta, cid)
            sl_price = _safe_float(sl_price) + basis_delta
            tp_price = _safe_float(tp_price) + basis_delta
        else:
            sl_price = _safe_float(sl_price)
            tp_price = _safe_float(tp_price)
        if not (math.isfinite(sl_price) and math.isfinite(tp_price) and sl_price > 0.0 and tp_price > 0.0):
            return {"executed": False, "reason": "invalid_sl_tp_non_finite", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
        stop_loss_distance = abs(execution_ref_price - _safe_float(sl_price))
        if stop_loss_distance <= 0:
            return {"executed": False, "reason": "invalid_stop_loss_distance", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}

        # --- Resolve position size ---
        if position_size is None or _safe_float(position_size) <= 0:
            balance = _safe_float(engine.get_balance(), 0.0)
            if ENGINE_IS_FALLBACK and balance == 0.0:
                logger.error("[EXECUTION] balance=0.0 from fallback engine — aborting trade cid=%s", cid)
                return {"executed": False, "reason": "fallback_engine_zero_balance", "correlation_id": correlation_id or ""}
            position_size = calculate_position_size(
                balance, risk_percent=RISK_PERCENT_PER_TRADE, stop_loss_distance=stop_loss_distance
            )

        pre_msg = _format_execution_message(
            title="📊 BTC SIGNAL",
            signal=execution_signal,
            confidence=confidence,
            price=execution_ref_price,
            correlation_id=correlation_id,
        )
        send_telegram_message(pre_msg)

        if position_size is None or _safe_float(position_size) <= 0:
            return {"executed": False, "reason": "invalid_position_size", "trade_id": deterministic_trade_id, "correlation_id": correlation_id or ""}
        position_size_usd = _safe_float(position_size) * execution_ref_price
        if not (EXCHANGE_MIN_NOTIONAL_USD <= position_size_usd <= EXCHANGE_MAX_NOTIONAL_USD):
            logger.warning("[EXECUTION] Position size %.2f USD out of exchange bounds [%.2f, %.2f] — skipping trade. cid=%s", position_size_usd, EXCHANGE_MIN_NOTIONAL_USD, EXCHANGE_MAX_NOTIONAL_USD, cid)
            return {"executed": False, "reason": "position_size_out_of_bounds", "correlation_id": correlation_id or ""}

        if not LIVE_TRADING:
            print(
                f"🧪 Paper Trade[{cid}]: {execution_signal} @ {execution_ref_price:.2f}"
                f" | SL={_safe_float(sl_price):.2f} | TP={_safe_float(tp_price):.2f}"
                f" | Size={_safe_float(position_size):.6f}"
            )
            paper_msg = _format_execution_message(
                title="🧪 Paper Trade Executed",
                signal=execution_signal,
                confidence=confidence,
                price=execution_ref_price,
                sl_price=_safe_float(sl_price),
                tp_price=_safe_float(tp_price),
                order_id="paper",
                correlation_id=correlation_id,
            )
            send_telegram_message(paper_msg)
            return {
                "executed": False,
                "paper": True,
                "trade_id": deterministic_trade_id,
                "correlation_id": correlation_id or "",
                "sl": sl_price,
                "tp": tp_price,
                "position_size": _safe_float(position_size),
                "market_data": market_data,
                "analysis_price": analysis_ref_price,
                "execution_price": execution_ref_price,
                "basis_delta": basis_delta,
            }

        side = "buy" if execution_signal == "LONG" else "sell"
        execution_result = engine.place_order_with_sl_tp(
            SYMBOL,
            side,
            position_size,
            sl_price,
            tp_price,
            correlation_id=correlation_id,
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
            price=execution_ref_price,
            sl_price=_safe_float(sl_price),
            tp_price=_safe_float(tp_price),
            order_id=str(order_id) if order_id is not None else "unknown",
            correlation_id=correlation_id,
        )
        send_telegram_message(post_msg)

        return {
            "executed": True,
            "paper": False,
            "trade_id": deterministic_trade_id,
            "correlation_id": correlation_id or "",
            "order_id": order_id,
            "sl": sl_price,
            "tp": tp_price,
            "position_size": position_size,
            "result": execution_result,
            "market_data": market_data,
                "analysis_price": analysis_ref_price,
                "execution_price": execution_ref_price,
                "basis_delta": basis_delta,
        }
    except Exception as exc:
        err_msg = f"❌ Execution Error\nSignal: {execution_signal}\nPrice: {price:,.2f}\nError: {exc}"
        try:
            send_telegram_message(err_msg)
        except Exception:
            pass
        logger.error("Execution failed cid=%s: %s", cid, exc, exc_info=True)
        return {
            "executed": False,
            "reason": str(exc),
            "correlation_id": correlation_id or "",
            "trade_id": deterministic_trade_id,
        }




def _validate_exchange_symbol_format(exchange: Any, symbol: str) -> bool:
    try:
        markets = exchange.load_markets() if hasattr(exchange, "load_markets") else getattr(exchange, "markets", {}) or {}
    except Exception as exc:
        raise RuntimeError(f"[EXCHANGE] Failed to load markets for {getattr(exchange, 'id', 'unknown')}: {exc}") from exc
    if not markets:
        raise RuntimeError(f"[EXCHANGE] Empty market list for {getattr(exchange, 'id', 'unknown')} — cannot validate symbol {symbol}")
    if symbol not in markets:
        raise ValueError(f"Symbol {symbol} is not valid for exchange {getattr(exchange, 'id', 'unknown')}")
    return True

def run_analysis_cycle(
    exchange,
    liq_monitor: Optional[LiquidationMonitor] = None,
    data_exchange: Any = None,
    data_symbol: str = SYMBOL,
) -> dict:
    global _last_regime_update_ts, _last_regime_context, _regime_context_timestamp, _prev_close, _last_valid_features, _last_valid_features_ts, _feature_type_error_count, _cold_start_complete
    result: Dict[str, Any] = {}
    _dex = data_exchange if data_exchange is not None else exchange
    _dsym = data_symbol
    if hasattr(_dex, "id") and hasattr(exchange, "id") and _dex.id != exchange.id:
        logger.warning("[EXCHANGE] Data exchange (%s) differs from execution exchange (%s). Ensure symbol formats are compatible.", _dex.id, exchange.id)
    basis_normalizer.set_venues(getattr(_dex, "id", "analysis"), getattr(exchange, "id", "execution"))
    _validate_exchange_symbol_format(_dex, _dsym)
    _validate_exchange_symbol_format(exchange, SYMBOL)
    try:
        snapshot = _fetch_market_snapshot(_dex, _dsym, exchange, SYMBOL)
        candles_by_tf = snapshot["candles_by_tf"]
        analysis_orderbook = snapshot["analysis_orderbook"]
        execution_orderbook = snapshot["execution_orderbook"]
        trades = snapshot["trades"]
        snapshot_ts = float(snapshot["snapshot_ts"])
        if not (candles_by_tf and isinstance(analysis_orderbook, dict) and isinstance(execution_orderbook, dict) and isinstance(trades, list)):
            logger.warning("[SNAPSHOT] inconsistent payload types; skipping cycle")
            return {"signal_output": {"signal": "HOLD", "confidence": 0.0, "reason": "snapshot_inconsistent"}}
    except Exception as exc:
        logger.error("Data fetch error: %s", exc)
        return {}

    SIGNAL_STATE["cycle"] += 1
    _prune_reconciliation_blocks()

    primary_1m = candles_by_tf.get("1m", [])
    primary_15m = candles_by_tf.get("15m", [])
    ohlcv = primary_1m or primary_15m

    try:
        current_price = float(primary_1m[-1][4])
    except Exception:
        current_price = _safe_float(
            analysis_orderbook.get("bids", [[0]])[0][0] if analysis_orderbook.get("bids") else 0.0
        )

    log_return: Optional[float] = None
    with _ANALYSIS_STATE_LOCK:
        prev_close_local = _prev_close
        if prev_close_local is not None and prev_close_local > 0.0 and current_price > 0.0:
            raw_lr = float(np.log(current_price / prev_close_local))
            if np.isfinite(raw_lr):
                log_return = raw_lr
            else:
                logger.warning("[REGIME] log_return is non-finite (prev=%.4f, curr=%.4f) — skipping regime update this tick", prev_close_local, current_price)
        _prev_close = current_price

    execution_price = _safe_float(
        (execution_orderbook.get("bids") or [[current_price]])[0][0] if execution_orderbook.get("bids") else current_price,
        current_price,
    )
    analysis_mid = 0.5 * (
        _safe_float((analysis_orderbook.get("bids") or [[current_price]])[0][0], current_price)
        + _safe_float((analysis_orderbook.get("asks") or [[current_price]])[0][0], current_price)
    )
    execution_mid = 0.5 * (
        _safe_float((execution_orderbook.get("bids") or [[execution_price]])[0][0], execution_price)
        + _safe_float((execution_orderbook.get("asks") or [[execution_price]])[0][0], execution_price)
    )
    basis_normalizer.seed(analysis_mid=analysis_mid, execution_mid=execution_mid)
    basis_normalizer.update(analysis_mid=analysis_mid, execution_mid=execution_mid)
    basis_status = basis_normalizer.validate()

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
    _append_orderbook_snapshot(analysis_orderbook, timestamp=snapshot_ts)
    snapshot_history = _get_orderbook_snapshot_history()
    if len(snapshot_history) < 3:
        logger.info(
            "[SPOOF] insufficient orderbook snapshots (%d/3); spoof detection is unreliable this cycle",
            len(snapshot_history),
        )

    engines_out = (
        run_all_engines(
            orderbook=analysis_orderbook,
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
            orderbook_snapshots=snapshot_history,
        )
        or {}
    )

    # Base features + advanced regime context (signal-only safe path)
    with _ANALYSIS_STATE_LOCK:
        regime_context: Dict[str, Any] = dict(_last_regime_context)
    update_freq = _safe_float(SIGNAL_PIPELINE_CONFIG.get("regime_update_frequency_sec", 1.0), 1.0)
    if update_freq <= 0:
        update_freq = 1.0
    now_ts = time.time()
    with _ANALYSIS_STATE_LOCK:
        last_regime_update_ts = float(_last_regime_update_ts)
    should_update_regime = (
        bool(SIGNAL_PIPELINE_CONFIG.get("enable_regime_engine", True))
        and regime_engine is not None
        and (now_ts - last_regime_update_ts >= update_freq)
    )
    regime_fail_closed = False
    regime_failure_reason = ""
    if should_update_regime:
        try:
            if log_return is None:
                logger.info("[REGIME] Skipping regime update on first tick (no prev_close yet)")
                reg_out = {}
            else:
                top_bid_depth = sum(_safe_float(b[1]) for b in (analysis_orderbook.get("bids") or [])[:10])
                top_ask_depth = sum(_safe_float(a[1]) for a in (analysis_orderbook.get("asks") or [])[:10])
                trade_volume = sum(
                    _safe_float(t.get("amount", 0.0)) * _safe_float(t.get("price", current_price))
                    for t in (trades or [])
                )
                total_depth = top_bid_depth + top_ask_depth
                imbalance = 0.0 if total_depth <= 0.0 else (top_bid_depth - top_ask_depth) / total_depth
                feature_vector = np.asarray([imbalance, top_bid_depth, trade_volume], dtype=float)
                expected_n = int(getattr(regime_engine, "n_features", feature_vector.shape[0]))
                if feature_vector.shape != (expected_n,):
                    raise ValueError(f"regime_feature_dim_mismatch:{feature_vector.shape} expected ({expected_n},)")
                if not np.all(np.isfinite(feature_vector)):
                    raise ValueError(f"regime_feature_non_finite:{feature_vector}")
                regime_input = {
                    "return": log_return,
                    "features": feature_vector,
                    "price": float(current_price),
                    "volume": float(candles_by_tf.get("1m", [[0,0,0,0,0,0]])[-1][5]) if candles_by_tf.get("1m") else 0.0,
                    "orderbook": analysis_orderbook,
                    "open_interest": float(open_interest) if np.isfinite(float(open_interest)) else 0.0,
                    "funding_rate": float(funding_rate) if np.isfinite(float(funding_rate)) else 0.0,
                }
                reg_out = regime_engine.update(regime_input) or {}
                if not bool(reg_out.get("signal_valid", True)):
                    regime_fail_closed = True
                    regime_failure_reason = str(reg_out.get("feed_status", "regime_invalid"))
            r_metrics = reg_out.get("risk_metrics", {}) if isinstance(reg_out, dict) else {}
            if not isinstance(r_metrics, dict):
                r_metrics = {}
            regime_context = {
                "regime": str(reg_out.get("regime_label", reg_out.get("regime", "UNKNOWN"))),
                "confidence": _safe_float(reg_out.get("confidence", 0.0), 0.0),
                "features": {
                    # Surface meaningful regime-engine fields. Names match what
                    # feature_engine.py expects without lying about semantics:
                    #   volatility_regime -> the engine's regime label (TREND/RANGE/BEAR/TOXIC)
                    #   liquidity_regime  -> execution_mode (trend_follow / range_mean_revert / flat_or_hedge)
                    # Expose feed_status + trend_strength separately so downstream code
                    # can still introspect them without semantic confusion.
                    "volatility_regime": str(reg_out.get("regime_label", "unknown")),
                    "liquidity_regime": str(reg_out.get("execution_mode", "unknown")),
                    "trend_strength": _safe_float(reg_out.get("trend_strength", 0.0), 0.0),
                    "feed_status": str(r_metrics.get("feed_status", "unknown")),
                },
            }
            with _ANALYSIS_STATE_LOCK:
                _last_regime_context = dict(regime_context)
                _last_regime_update_ts = now_ts
                _regime_context_timestamp = time.time()
        except Exception as _re_exc:
            regime_fail_closed = True
            regime_failure_reason = str(_re_exc)
            logger.error("[REGIME] update failed, entering fail-closed mode: %s", _re_exc, exc_info=True)
            with _ANALYSIS_STATE_LOCK:
                regime_context = dict(_last_regime_context)
                regime_ts = _regime_context_timestamp
            staleness = time.time() - regime_ts
            if staleness > MAX_REGIME_STALENESS_SECONDS:
                logger.critical("[REGIME] Context is %.0fs stale (limit %.0fs) — forcing STALE_FALLBACK HALT. No new trades will be opened.", staleness, MAX_REGIME_STALENESS_SECONDS)
                regime_context = {"regime": "STALE_FALLBACK", "confidence": 0.0, "position_size": 0.0, "signal_valid": False, "execution_mode": "halt", "features": {}}

    try:
        snapshot = {
            "bids": analysis_orderbook.get("bids", []),
            "asks": analysis_orderbook.get("asks", []),
            "timestamp": time.time(),
        }
        features = feature_engine.update(snapshot, trades, regime_context=regime_context)
        with _COLD_START_LOCK:
            if features is not None and not _cold_start_complete:
                _cold_start_complete = True
                logger.info("[BOOT] Cold start complete — feature engine warmed up")
        with _ANALYSIS_STATE_LOCK:
            _last_valid_features = dict(features)
            _last_valid_features_ts = time.time()
    except TypeError as _te_exc:
        _feature_type_error_count += 1
        logger.error(
            "[FEATURE] TypeError in update | exc_type=%s exc=%s args=%s tick_ts=%.6f count=%d",
            type(_te_exc).__name__,
            str(_te_exc),
            json.dumps({"snapshot": snapshot, "trades_count": len(trades or []), "regime_context": regime_context}, default=str)[:2000],
            time.time(),
            _feature_type_error_count,
            exc_info=True,
        )
        if _feature_type_error_count > 3:
            logger.critical("[FEATURE] repeated TypeErrors detected; halting execution mode")
            regime_context["execution_mode"] = "halt_feature_errors"
        try:
            features = feature_engine.update(snapshot, trades)
            with _ANALYSIS_STATE_LOCK:
                _last_valid_features = dict(features)
                _last_valid_features_ts = time.time()
        except Exception as _fe_exc:
            logger.warning("[FEATURE] Feature engine failed after TypeError fallback: %s", _fe_exc, exc_info=True)
            features = None
    except Exception as _fe_exc:
        logger.warning("[FEATURE] Feature engine failed: %s", _fe_exc, exc_info=True)
        features = None

    if features is None:
        with _ANALYSIS_STATE_LOCK:
            last_valid_features = dict(_last_valid_features) if isinstance(_last_valid_features, dict) else None
            last_valid_features_ts = _last_valid_features_ts
        feature_staleness = time.time() - last_valid_features_ts
        if last_valid_features is None or feature_staleness > MAX_FEATURE_STALENESS_SECONDS:
            with _COLD_START_LOCK:
                cold_start_complete = _cold_start_complete
            if not cold_start_complete:
                logger.warning("[BOOT] Cold start feature engine failure on tick 1 — no warm-up data available. Returning HOLD.")
            else:
                logger.warning("[FEATURE] No valid features available (staleness=%.1fs, limit=%.1fs) — skipping signal generation", feature_staleness, MAX_FEATURE_STALENESS_SECONDS)
            return {"signal_output": {"signal": "HOLD", "confidence": 0.0, "reason": "no_features"}}
        logger.warning("[FEATURE] Using stale features (%.1fs old) after engine failure", feature_staleness)
        features = dict(last_valid_features)
    features = fill_model.enrich(features)
    features = tox_filter.enrich(features)

    # Normalize to a single raw feature dict for all downstream modules
    feat_dict: Dict[str, Any] = features.get("features", features) if isinstance(features, dict) else {}
    feat_dict["candles"] = candles_by_tf.get("1h", [])

    # Update impact decay using raw features
    impact_status = impact_tracker.update(current_price, feat_dict)
    logger.info("[FEATURE KEYS] %s", sorted(feat_dict.keys()))

    predictor_signal: Dict[str, Any] = {}
    if alpha_predictor is not None:
        try:
            predictor_signal = alpha_predictor.predict(
                {
                    "price": current_price,
                    "close_price": current_price,
                    "curr_book": analysis_orderbook,
                    "prev_book": analysis_orderbook,
                    "timestamp": time.time(),
                    "trades_count": len(trades or []),
                    "atr": max(current_price * 0.001, 1e-8),
                    "ema_fast": current_price,
                    "ema_slow": current_price,
                    "pre_sweep_depth": 0.0,
                    "curr_depth": 0.0,
                    "sweep_time_elapsed": 0.0,
                    "macro_liquidity": engines_out.get("liquidity_map", {}),
                    "macro_market_state": engines_out.get("market_state", {}),
                    "macro_volume_intel": volume_intel,
                },
                regime_context=regime_context,
            ) or {}
        except Exception as _pred_exc:
            logger.warning("[PREDICTOR] predict failed (non-fatal): %s", _pred_exc)
            predictor_signal = {}

    # FINAL GLOBAL SAFETY (never allow NaN/Inf leakage)
    def _sanitize_dict(d: Dict[str, Any], _depth: int = 0) -> Dict[str, Any]:
        out = {}
        for k, v in d.items():
            if _depth > 10:
                if isinstance(v, bool):
                    out[k] = v
                    continue
                if isinstance(v, numbers.Number):
                    out[k] = 0.0
                elif isinstance(v, dict):
                    out[k] = {}
                elif isinstance(v, (list, tuple, set)):
                    out[k] = []
                else:
                    out[k] = str(v)
                continue

            if isinstance(v, bool):
                out[k] = v
            elif isinstance(v, numbers.Number):
                try:
                    fv = float(v)
                    out[k] = fv if math.isfinite(fv) else 0.0
                except Exception:
                    out[k] = 0.0
            elif isinstance(v, dict):
                out[k] = _sanitize_dict(v, _depth + 1)  # recursive safety
            elif isinstance(v, set):
                sanitized_set = []
                for item in v:
                    if isinstance(item, bool):
                        sanitized_set.append(item)
                    elif isinstance(item, numbers.Number):
                        try:
                            fi = float(item)
                            sanitized_set.append(fi if math.isfinite(fi) else 0.0)
                        except Exception:
                            sanitized_set.append(0.0)
                    elif isinstance(item, dict):
                        sanitized_set.append(_sanitize_dict(item, _depth + 1))
                    elif isinstance(item, (list, tuple, set)):
                        sanitized_set.append(
                            _sanitize_dict({"_": list(item)}, _depth + 1).get("_", [])
                        )
                    else:
                        sanitized_set.append(str(item))
                out[k] = sorted(sanitized_set, key=lambda x: repr(x))
            elif isinstance(v, (list, tuple)):
                sanitized_list = []
                for item in list(v):
                    if isinstance(item, bool):
                        sanitized_list.append(item)
                    elif isinstance(item, numbers.Number):
                        try:
                            fi = float(item)
                            sanitized_list.append(fi if math.isfinite(fi) else 0.0)
                        except Exception:
                            sanitized_list.append(0.0)
                    elif isinstance(item, dict):
                        sanitized_list.append(_sanitize_dict(item, _depth + 1))
                    elif isinstance(item, (list, tuple, set)):
                        sanitized_list.append(
                            _sanitize_dict({"_": list(item)}, _depth + 1).get("_", [])
                        )
                    else:
                        sanitized_list.append(str(item))
                out[k] = sanitized_list
            else:
                out[k] = str(v)
        return out

    if SIGNAL_PIPELINE_CONFIG.get("signal_only_mode", SIGNAL_ONLY_MODE):
        logger.warning("[CYCLE] signal_only_mode=True — execution layer is DISABLED")
        signal_value = str(predictor_signal.get("action", "HOLD")).upper()
        if signal_value not in ("BUY", "SELL", "HOLD"):
            signal_value = "HOLD"
        if signal_value == "BUY":
            signal_value = "LONG"
        elif signal_value == "SELL":
            signal_value = "SHORT"
        signal_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal": signal_value,
            "regime": regime_context.get("regime", "UNKNOWN"),
            "confidence": _clamp(
                _safe_float(predictor_signal.get("confidence", 0.0), 0.0),
                0.0,
                1.0
            ),
        }
        logger.info("[SIGNAL_ONLY] %s", signal_payload)
        log_trade({
            "timestamp": signal_payload["timestamp"],
            "symbol": SYMBOL,
            "signal": signal_value,
            "confidence": signal_payload["confidence"],
            "execution_skipped": True,
            "reason": "signal_only_mode",
            "regime": signal_payload["regime"],
        })
        return {
            "signal_output": signal_payload,
            "predictor_output": _sanitize_dict({
                k: (
                    _clamp(_safe_float(v, 0.0), 0.0, 1.0)
                    if k in ("confidence", "prob_above", "prob_below", "micro_prob", "macro_prob")
                    else (v if isinstance(v, bool) else (_safe_float(v, 0.0) if isinstance(v, (int, float)) else v))
                )
                for k, v in (predictor_signal or {}).items()
            }),
            "regime_context": _sanitize_dict({
                "regime": str(regime_context.get("regime", "UNKNOWN")),
                "confidence": _safe_float(regime_context.get("confidence", 0.0), 0.0),
                "features": dict(regime_context.get("features", {}) or {}),
            }),
            "metadata": {"execution_skipped": True},
            "status": "SIGNAL_ONLY",
        }

    # Regime / lifecycle
    lifecycle = trade_lifecycle.update(current_price, feat_dict)
    if not basis_status.ok:
        lifecycle["block_new_entries"] = True
        lifecycle["reason"] = f"{basis_status.reason}:basis={basis_status.basis:.2f},pct={basis_status.basis_pct:.4f}"
        feat_dict["allow_trade"] = False
        logger.critical("[BASIS] trading blocked reason=%s basis=%.4f basis_pct=%.4f", basis_status.reason, basis_status.basis, basis_status.basis_pct)
    if regime_fail_closed or str(regime_context.get("regime", "")).upper() in ("HALTED", "STALE_FALLBACK", "UNCALIBRATED"):
        lifecycle["block_new_entries"] = True
        lifecycle["reason"] = f"regime_blocked:{regime_failure_reason or regime_context.get('regime','unknown')}"
        feat_dict["allow_trade"] = False
    session_guard = trade_lifecycle.session_guard() or {}
    if session_guard.get("block_new_entries"):
        lifecycle["block_new_entries"] = True
        lifecycle["reason"] = session_guard.get("reason", lifecycle.get("reason", "session_guard"))

    logger.info(
        "[REGIME] regime=%s conf=%.3f allow_trade=%s mode=%s scale=%.2f reason=%s",
        feat_dict.get("regime", "unknown"),
        _safe_float(feat_dict.get("regime_confidence", 0.0)),
        feat_dict.get("allow_trade", False),
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
    _pa_correlation_id = str(pos_action.get("correlation_id") or "")
    _pa_cid = _pa_correlation_id[:12]
    logger.info(
        "[POSITION ACTION] cid=%s action=%s price=%.2f new_sl=%.2f size=%.4f reason=%s",
        _pa_cid, _pa_action, current_price, _pa_new_sl, _pa_size, _pa_reason,
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
            "[EXIT] cid=%s side=%s entry=%.2f exit=%.2f pnl=%.4f%% size=%.4f reason=%s",
            _pa_cid, _close_side, _close_entry_price, _close_exit_price,
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

        # LearningEngine trade + exit-quality updates are executed once in PositionManager.close().

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

        # Closed-trade learning must be emitted exactly once from PositionManager.close().

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
            analysis_orderbook, current_price
        )

    engines_out["volume_intelligence"] = volume_intel
    engines_out["volume_spike"] = volume_intel.get("volume_spike", False)
    engines_out["volume_explosion"] = volume_intel.get("volume_explosion", False)
    engines_out["volume_strength"] = volume_intel.get("volume_strength", 0.0)
    engines_out["mtf_confirmation"] = volume_intel.get("mtf_confirmation", False)
    alpha_payload = engines_out.get("alpha", {})
    feat_dict["alpha"] = alpha_payload if isinstance(alpha_payload, dict) else {}
    smc_signal = engines_out.get("smc_signal", {}) or {}

    sma_signal, sma_fast, sma_slow = sma_crossover_signal(ohlcv)
    ob_imb = orderbook_imbalance(analysis_orderbook)
    whales = detect_whale_trades(trades)
    whale_sig, _ = whale_net_signal(whales)
    volatility = _estimate_volatility_from_ohlcv(ohlcv)

    best_bid = _safe_float(
        (analysis_orderbook.get("bids") or [[current_price]])[0][0]
        if analysis_orderbook.get("bids")
        else current_price
    )
    best_ask = _safe_float(
        (analysis_orderbook.get("asks") or [[current_price]])[0][0]
        if analysis_orderbook.get("asks")
        else current_price
    )
    bid_vol_top = sum(_safe_float(b[1]) for b in analysis_orderbook.get("bids", [])[:10])
    ask_vol_top = sum(_safe_float(a[1]) for a in analysis_orderbook.get("asks", [])[:10])

    if open_interest > 0:
        oih = [open_interest * 0.98, open_interest * 0.995, open_interest]
    else:
        oih = []

    cascade_prob = _safe_float(engines_out.get("cascade_probability", 0.0))
    if cascade_prob <= 0.0 and open_interest > 0:
        cascade_prob = get_cascade_probability(
            open_interest=open_interest,
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
    result = {}

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

    if len(feat_dict.get("candles", [])) == 0:
        logger.error("[SIGNAL_ENGINE] missing candles in features payload; forcing HOLD")
        feat_dict["candles"] = candles_by_tf.get("1m", [])[-60:]
    try:
        signal_output = signal_engine.generate(feat_dict)
    except Exception as _signal_exc:
        logger.warning("[SIGNAL_ENGINE] generate failed, forcing HOLD: %s", _signal_exc)
        signal_output = {"signal": "HOLD", "confidence": 0.0, "reason": f"signal_error:{_signal_exc}"}
    result["signal_engine_output"] = signal_output
    direction_map = {"LONG": 1, "BUY": 1, "SHORT": -1, "SELL": -1, "HOLD": 0, "NEUTRAL": 0}
    sig_dir = direction_map.get(str(signal_output.get("signal", "HOLD")).upper(), 0)
    alpha_signals = [
        AlphaSignal(
            source_id="signal_engine",
            direction=sig_dir,
            conviction=_clamp(_safe_float(signal_output.get("confidence", 0.0), 0.0), 0.0, 1.0),
            expected_edge_bps=abs(10.0 * sig_dir),
            timestamp=now_ts if now_ts > 0 else time.time(),
            timeframe="1m",
            correlation_group_id="directional",
        )
    ]
    feature_quality = FeatureQuality(
        staleness_ratio=_clamp(_safe_float(feat_dict.get("staleness_ratio", 0.0), 0.0), 0.0, 1.0),
        missing_data_ratio=_clamp(_safe_float(feat_dict.get("missing_data_ratio", 0.0), 0.0), 0.0, 1.0),
    )
    regime_for_orch = RegimeContext(
        regime_name=str(regime_context.get("regime", "unknown")).lower(),
        volatility_score=_clamp(_safe_float(feat_dict.get("volatility_score", 0.2), 0.2), 0.0, 1.0),
        liquidity_score=_clamp(_safe_float(feat_dict.get("liquidity_score", 0.8), 0.8), 0.0, 1.0),
    )
    exec_state = ExecutionState(
        current_exposure_usd=0.0,
        max_exposure_usd=max(_safe_float(engine.get_balance(), 0.0), 1.0),
        current_drawdown_pct=_clamp(_safe_float(lifecycle.get("drawdown", 0.0), 0.0), 0.0, 1.0),
    )
    try:
        fused_signal = alpha_orchestrator.orchestrate(
            alpha_signals,
            regime_for_orch,
            feature_quality,
            exec_state,
            current_time=now_ts,
        )
    except Exception as _orch_exc:
        logger.error("[ORCHESTRATOR] orchestrate failed; fail-closed HOLD: %s", _orch_exc, exc_info=True)
        lifecycle["block_new_entries"] = True
        lifecycle["reason"] = f"orchestrator_failure:{_orch_exc}"
        feat_dict["allow_trade"] = False
        signal_output = {
            "signal": "HOLD",
            "confidence": 0.0,
            "reason": "orchestrator_failure",
            "meta": {
                "failure_mode": "orchestrator_exception",
                "failure_detail": str(_orch_exc),
                "execution_blocked": True,
            },
        }
    else:
        signal_output = {
            "signal": {1: "LONG", -1: "SHORT", 0: "HOLD"}.get(int(fused_signal.action.value), "HOLD"),
            "confidence": _clamp(_safe_float(getattr(fused_signal, "net_conviction", 0.0), 0.0), 0.0, 1.0),
            "reason": str((fused_signal.meta_info or {}).get("rationale", "orchestrated")),
            "meta": fused_signal.meta_info or {},
        }
    logger.info("[SIGNAL] %s", signal_output)

    learning_params: Dict[str, Any] = {}
    if LEARNING_ENGINE is not None:
        try:
            learning_params = LEARNING_ENGINE.get_adaptive_params() or {}
        except Exception as _lp_exc:
            logger.warning("[LEARNING] get_adaptive_params failed (non-fatal): %s", _lp_exc)
            learning_params = {}
    feat_dict["learning_params"] = learning_params

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

    try:
        router_decision = order_router.route(normalized_signal, feat_dict, execution_orderbook)
    except Exception as _router_exc:
        logger.warning("[ROUTER] route failed, forcing no-exec: %s", _router_exc)
        router_decision = {
            "execute": False,
            "reason": f"router_error:{_router_exc}",
            "order_type": "market",
        }
    result["router_decision"] = router_decision
    critical_input_missing = bool(engines_out.get("open_interest_missing", False))
    if critical_input_missing:
        logger.error("[RISK] Critical input missing (open_interest). Forcing fail-closed execution block.")

    try:
        meta_result = evaluate_meta_filter(
            features=feat_dict,
            signal=signal_output,
            router_decision=router_decision,
            snapshot=execution_orderbook,
            trades=trades,
        )
    except Exception as _meta_exc:
        logger.warning("[META_FILTER] evaluation error (fail-closed): %s", _meta_exc)
        meta_result = {"allow_trade": False, "risk_scale": 0.0, "reason": "eval_error", "meta_state": {"fail_closed": True}}
    if critical_input_missing:
        meta_state = dict(meta_result.get("meta_state", {})) if isinstance(meta_result, dict) else {}
        meta_state["fail_closed"] = True
        meta_state["open_interest_missing"] = True
        meta_result = {
            "allow_trade": False,
            "risk_scale": 0.0,
            "reason": "open_interest_missing",
            "meta_state": meta_state,
        }

    if not meta_result.get("allow_trade", False):
        logger.info(
            "[META_FILTER] BLOCKED reason=%s score=%.3f",
            meta_result.get("reason", "unknown"),
            _safe_float(meta_result.get("meta_state", {}).get("composite_score", 0.0)),
        )
        normalized_signal = "HOLD"

    balance = _safe_float(engine.get_balance(), 0.0)
    try:
        decision = execution_engine.decide(
            signal_payload={
                "signal": normalized_signal,
                "confidence": float(_safe_float(confidence, 0.0)),
            },
            features_payload=feat_dict,
            snapshot=execution_orderbook,
            account_equity=balance,
            meta_result=meta_result,
        )
    except Exception as _decide_exc:
        logger.error("[EXECUTION_LOGIC] decide failed (forcing no-exec): %s", _decide_exc, exc_info=True)
        decision = {
            "execute": False,
            "side": "buy" if normalized_signal == "LONG" else "sell",
            "sl": 0.0,
            "tp": 0.0,
            "position_size": 0.0,
            "reason": f"execution_decide_error:{_decide_exc}",
            "meta_result": meta_result,
            "learning_params": learning_params,
        }

    risk_scale = _safe_float(lifecycle.get("risk_scale", 1.0))
    regime_scale = _safe_float(feat_dict.get("position_scale", 1.0))
    if decision.get("position_size", 0.0):
        decision["position_size"] = (
            _safe_float(decision["position_size"]) * risk_scale * regime_scale
        )

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

    capital_decision: Dict[str, Any] = {
        "capital_scale": 1.0,
        "allow_trading": True,
        "max_exposure": balance,
        "reason": "not_applied",
    }
    if decision.get("execute", False) and normalized_signal in ("LONG", "SHORT"):
        signal_conf_for_alloc = _clamp(_safe_float(signal_output.get("confidence", confidence), 0.0), 0.0, 1.0)
        alloc_regime = {"regime": str(regime_context.get("regime", "UNKNOWN")).upper()}
        capital_decision = capital_allocator.allocate(
            signal_confidence=signal_conf_for_alloc,
            regime_context=alloc_regime,
            current_equity=balance,
            max_risk_pct=BACKTEST_RISK_PCT,
        )

        if not capital_decision.get("allow_trading", True):
            decision["execute"] = False
            decision["reason"] = f"{decision.get('reason','')}|capital_blocked:{capital_decision.get('reason')}"
            normalized_signal = "HOLD"
        elif decision.get("position_size", 0.0) > 0:
            decision["position_size"] = (
                _safe_float(decision["position_size"])
                * _safe_float(capital_decision.get("capital_scale", 1.0))
            )

            max_exposure = _safe_float(capital_decision.get("max_exposure", balance))
            notional = (
                _safe_float(decision.get("position_size", 0.0))
                * _safe_float(current_price)
            )
            if notional > max_exposure:
                decision["position_size"] = max_exposure / max(_safe_float(current_price), 1e-9)
                decision["reason"] = f"{decision.get('reason','')}|clamped_by_capital_allocator"

        if not decision.get("execute", False):
            normalized_signal = "HOLD"

    final_position_size = _safe_float(decision.get("position_size", 0.0))
    if final_position_size < 0:
        logger.warning("[SAFETY] negative position_size corrected: %.6f", final_position_size)
        decision["position_size"] = 0.0
        final_position_size = 0.0

    if not decision.get("execute", False) and normalized_signal != "HOLD":
        logger.warning("[SAFETY] forcing HOLD because execute=False after finalization")
        normalized_signal = "HOLD"

    if final_position_size < 0:
        logger.error("[SAFETY] invalid negative final_position_size %.6f; forcing to zero", final_position_size)
        final_position_size = 0.0
        decision["position_size"] = 0.0
    if decision.get("execute") is False and normalized_signal != "HOLD":
        logger.warning("[SAFETY] forcing HOLD to keep execute=False contract")
        normalized_signal = "HOLD"

    final_decision = decision.copy()
    final_decision["position_size"] = final_position_size

    logger.info(
        "[CAPITAL] scale=%.3f allow=%s max_exp=%.2f reason=%s",
        capital_decision.get("capital_scale"),
        capital_decision.get("allow_trading"),
        capital_decision.get("max_exposure"),
        capital_decision.get("reason"),
    )

    logger.info(
        "[META_FILTER] allow=%s scale=%.2f reason=%s",
        meta_result.get("allow_trade", False),
        _safe_float(meta_result.get("risk_scale", 1.0)),
        meta_result.get("reason", ""),
    )
    logger.info("[ROUTER] %s", router_decision)
    logger.info("[DECISION] %s", final_decision)

    execution_outcome = {
        "executed": False,
        "paper": False,
        "reason": "not_attempted",
    }
    active_correlation_id = trade_lifecycle.get_correlation_id() if hasattr(trade_lifecycle, "get_correlation_id") else ""
    active_cid = active_correlation_id[:12]

    if (
        normalized_signal in ("LONG", "SHORT")
        and final_decision.get("execute")
        and router_decision.get("execute")
        and not position_manager.has_position()
    ):
        try:
            execution_outcome = _execute_liquidity_trade(
                execution_signal=normalized_signal,
                price=execution_price,
                confidence=_safe_float(confidence, 0.0),
                candles_by_tf=candles_by_tf,
                engines_out=engines_out,
                analysis_price=current_price,
                execution_orderbook=execution_orderbook,
                sl_price=final_decision.get("sl") or None,
                tp_price=final_decision.get("tp") or None,
                sl_tp_price_space="execution",
                position_size=final_decision.get("position_size") or None,
                correlation_id=active_correlation_id,
            )
            logger.info(
                "[EXECUTION TRACE] cid=%s signal=%s executed=%s paper=%s reason=%s",
                active_cid,
                normalized_signal,
                execution_outcome.get("executed"),
                execution_outcome.get("paper"),
                execution_outcome.get("reason", ""),
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
                        trade_id=str(eo.get("trade_id") or ""),
                        signal=normalized_signal,
                        confidence=_clamp(_safe_float(confidence, 0.0), 0.0, 1.0),
                        regime=str(feat_dict.get("regime", "unknown")),
                        fees=fees,
                        fee_type=fee_type,
                        features=feat_dict,
                        correlation_id=active_correlation_id,
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
    elif not final_decision.get("execute"):
        execution_outcome["reason"] = final_decision.get("reason", "execution_engine_blocked")
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
            "basis_okx": round(analysis_mid, 6),
            "basis_binance": round(execution_mid, 6),
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
    global CREDENTIALS_MISSING
    import signal as _signal
    print("BTCUSDT Institutional Signal Bot Started")
    logger.info("Mode: %s", "DRY RUN" if DRY_RUN else "LIVE")
    logger.info(f"[BOOT] LIVE_TRADING resolved to: {LIVE_TRADING}")
    logger.info(f"[BOOT] SIGNAL_ONLY_MODE resolved to: {SIGNAL_ONLY_MODE}")
    if LIVE_TRADING and (not BINANCE_API_KEY.strip() or not BINANCE_SECRET.strip()):
        raise RuntimeError("LIVE_TRADING=True but credentials not configured")
    if (not LIVE_TRADING) and (not DRY_RUN) and (not BINANCE_API_KEY.strip() or not BINANCE_SECRET.strip()):
        CREDENTIALS_MISSING = True
        msg = "[BOOT] DRY_RUN=0 and LIVE_TRADING=False but credentials missing. Exchange data fetch may fail. Set BINANCE_API_KEY and BINANCE_SECRET or set DRY_RUN=1 for fully offline mode."
        logger.warning(msg)
        try:
            send_telegram_message(msg)
        except Exception:
            logger.error("[BOOT] Telegram alert failed for credentials warning", exc_info=True)
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

    balance = _safe_float(engine.get_balance(), 0.0)
    if ENGINE_IS_FALLBACK and balance == 0.0:
        if LIVE_TRADING:
            msg = "[BOOT] ExecutionEngine is running in FALLBACK MODE — balance=0.0 cannot be trusted"
            logger.critical(msg)
            try:
                send_telegram_message(msg)
            except Exception:
                logger.error("[BOOT] Telegram alert failed for fallback warning", exc_info=True)
            raise RuntimeError("ExecutionEngine fallback active — cannot validate account balance for live trading")
        logger.warning("[BOOT] ExecutionEngine fallback active in paper-trade mode — position sizing will use balance=0.0")

    liq_monitor = LiquidationMonitor("btcusdt", window_seconds=300)
    _shutdown_requested = threading.Event()
    def _handle_sigterm(signum, frame):
        _ = (signum, frame)
        logger.critical("[SHUTDOWN] SIGTERM received — initiating graceful shutdown")
        try:
            send_telegram_message("[SHUTDOWN] SIGTERM received — initiating graceful shutdown")
        except Exception:
            logger.error("[SHUTDOWN] Telegram alert failed", exc_info=True)
        _shutdown_requested.set()
    _signal.signal(_signal.SIGTERM, _handle_sigterm)
    liq_monitor.start()
    interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    _consecutive_errors = 0

    try:
        while not _shutdown_requested.is_set():
            try:
                run_analysis_cycle(exchange, liq_monitor, data_exchange, data_symbol)
                _consecutive_errors = 0
                logger.info("Cycle complete. Next poll in %ds …", interval)
            except KeyboardInterrupt:
                logger.info("Interrupted. Exiting.")
                _shutdown_requested.set()
                break
            except Exception as exc:
                logger.error("Cycle error: %s", exc, exc_info=True)
                _consecutive_errors += 1
                if _consecutive_errors >= MAX_CONSECUTIVE_CYCLE_ERRORS:
                    msg = f"[CIRCUIT BREAKER] {_consecutive_errors} consecutive cycle failures. Pausing for {CIRCUIT_BREAKER_SLEEP_SECONDS}s before retry. Last error: {exc}"
                    logger.critical(msg)
                    try:
                        send_telegram_message(msg)
                    except Exception:
                        logger.error("[CIRCUIT BREAKER] Telegram send failed", exc_info=True)
                    time.sleep(CIRCUIT_BREAKER_SLEEP_SECONDS)
                    _consecutive_errors = 0
            time.sleep(interval)
    except Exception as fatal_exc:
        logger.critical("[FATAL] run_live crashed: %s", fatal_exc, exc_info=True)
        try:
            send_telegram_message(f"[FATAL] run_live crashed: {fatal_exc}")
        except Exception:
            logger.error("[FATAL] telegram alert failed", exc_info=True)
        _shutdown_requested.set()
    finally:
        if position_manager.has_position():
            msg = f"[SHUTDOWN] Open position detected at shutdown. LIVE_TRADING={LIVE_TRADING}. Manual review required."
            logger.critical(msg)
            try:
                send_telegram_message(msg)
            except Exception:
                logger.error("[SHUTDOWN] Telegram send failed for open position warning", exc_info=True)
        logger.info("[SHUTDOWN] Shutdown sequence complete")
        liq_monitor.stop()


def _compute_sharpe(returns: List[float]) -> float:
    try:
        if len(returns) < 2:
            return 0.0
        avg = statistics.mean(returns)
        st = statistics.stdev(returns)
        return 0.0 if st == 0 else (avg / st) * math.sqrt(len(returns))
    except Exception:
        return 0.0




@dataclass
class BacktestCostModel:
    taker_fee_bps: float = 5.0
    slippage_bps: float = 5.0
    spread_bps: float = 3.0

    @property
    def round_trip_cost_pct(self) -> float:
        return (self.taker_fee_bps + self.slippage_bps + self.spread_bps) * 2 / 10_000

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
    """Backtest path uses the shared execution stack via BacktestEngine for live-parity semantics."""
    logger.info("=== BACKTEST START: %s [%s] ===", symbol, timeframe)
    strict_calibration = os.environ.get("BACKTEST_STRICT_CALIBRATION", "0").strip() == "1"
    if regime_engine is not None and not bool(getattr(regime_engine, "_weights_loaded", False)):
        if strict_calibration:
            logger.critical("[BACKTEST] blocked: regime engine uncalibrated")
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "error": "uncalibrated_regime_engine",
                "execution_mode": "halt",
                "signal_valid": False,
            }
        logger.warning("[BACKTEST] proceeding in explicit non-production uncalibrated mode (BACKTEST_STRICT_CALIBRATION=0)")
    if ohlcv_data is not None:
        ohlcv_all = list(ohlcv_data)
    else:
        try:
            exchange = get_exchange()
            ohlcv_all = fetch_ohlcv(exchange, symbol, timeframe, limit)
        except Exception as exc:
            logger.error("Backtest fetch error: %s", exc)
            return {}

    bt = BacktestEngine(config=BacktestConfig(initial_balance=capital), learning_engine=LEARNING_ENGINE)
    result = bt.run_backtest(ohlcv_all, initial_balance=capital)
    summary = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_analysed": len(ohlcv_all),
        "initial_capital": capital,
        "final_equity": round(capital + _safe_float(result.get("pnl", 0.0)), 2),
        "total_return_pct": round((_safe_float(result.get("pnl", 0.0)) / max(capital, 1e-9)) * 100.0, 4),
        "total_trades": int(result.get("total_trades", 0)),
        "win_rate_pct": round(_safe_float(result.get("win_rate", 0.0)) * 100.0, 2),
        "max_drawdown_pct": round(_safe_float(result.get("max_drawdown", 0.0)) * 100.0, 4),
        "sharpe_ratio": round(_safe_float(result.get("sharpe", 0.0)), 4),
        "trades": result.get("trade_log", []),
    }
    with open(BACKTEST_RESULT_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("=== BACKTEST COMPLETE ===")
    return summary


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "backtest":
        run_backtest()
    else:
        run_live()
    _SHARED_FETCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)
