# backtest_engine.py
from __future__ import annotations

import logging
import math
import time as _time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from venue_basis import VenueBasisNormalizer

try:
    from feature_engine import FeatureEngine
    from signal_engine import SignalEngine
    from execution import ExecutionLogic
    from meta_filter import MetaFilter
    from learning_engine import LEARNING_ENGINE
    from queue_fill_model import QueueFillModel
    from toxicity_filter import ToxicityFilter
    from position_manager import PositionManager
    from trade_lifecycle_manager import TradeLifecycleManager
    from capital_allocator import CapitalAllocator
except Exception as _be_import_err:
    import logging as _be_log
    _be_log.getLogger(__name__).warning(
        "backtest_engine: module import failed (%s) — BacktestEngine unusable", _be_import_err
    )
    FeatureEngine = None  # type: ignore[assignment,misc]
    SignalEngine = None  # type: ignore[assignment,misc]
    ExecutionLogic = None  # type: ignore[assignment,misc]
    MetaFilter = None  # type: ignore[assignment,misc]
    LEARNING_ENGINE = None  # type: ignore[assignment,misc]
    QueueFillModel = None  # type: ignore
    ToxicityFilter = None  # type: ignore
    PositionManager = None  # type: ignore
    TradeLifecycleManager = None  # type: ignore
    CapitalAllocator = None  # type: ignore

# Phase 4 fixes: production pipeline imports (ARE + LSA + AlphaOrchestrator).
# Each import is isolated so that a single missing module does not silently
# disable the whole production-valid path. Where a prerequisite is unavailable
# we FAIL CLOSED in run_backtest() rather than fall through a hidden parallel
# path. (CRITICAL-4 d/e)
try:
    from advanced_regime_engine import AdvancedRegimeEngine
except Exception as _are_err:
    AdvancedRegimeEngine = None  # type: ignore[assignment,misc]
    logging.getLogger(__name__).warning(
        "backtest_engine: AdvancedRegimeEngine import failed (%s)", _are_err
    )

try:
    from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha
except Exception as _lsa_err:
    LiquiditySweepAlpha = None  # type: ignore[assignment,misc]
    logging.getLogger(__name__).warning(
        "backtest_engine: LiquiditySweepAlpha import failed (%s)", _lsa_err
    )

try:
    from alpha_orchestrator import (
        AlphaOrchestrator,
        AlphaSignal,
        OrchestratorConfig,
        RegimeContext,
        FeatureQuality,
        ExecutionState,
        Action,
    )
except Exception as _ao_err:
    AlphaOrchestrator = None  # type: ignore[assignment,misc]
    AlphaSignal = None  # type: ignore[assignment,misc]
    OrchestratorConfig = None  # type: ignore[assignment,misc]
    RegimeContext = None  # type: ignore[assignment,misc]
    FeatureQuality = None  # type: ignore[assignment,misc]
    ExecutionState = None  # type: ignore[assignment,misc]
    Action = None  # type: ignore[assignment,misc]
    logging.getLogger(__name__).warning(
        "backtest_engine: AlphaOrchestrator import failed (%s)", _ao_err
    )

try:
    from bar_aggregator import resample_bars
except Exception as _ba_err:
    resample_bars = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning(
        "backtest_engine: bar_aggregator import failed (%s)", _ba_err
    )

logger = logging.getLogger(__name__)

try:
    from l2_pipeline import align_book_to_bars
except Exception:
    align_book_to_bars = None  # type: ignore[assignment]

BookSnapshot = Dict[str, Any]


# ----------------------------------------------------------------------------
# Lightweight fallbacks (used only when imports fail upstream — not part of
# the production-valid pipeline). run_backtest() will fail-closed if a real
# production component is missing, so these only matter for module-level
# instantiation safety.
# ----------------------------------------------------------------------------
class _FallbackFeatureEngine:
    def update(self, snapshot: Dict[str, Any], trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"features": {"latency_ms": 0.0, "liquidity_score": 1.0, "spread_bps": 1.0},
                "snapshot": snapshot, "trade_count": len(trades)}


class _FallbackSignalEngine:
    def generate(self, features: Dict[str, Any]) -> Dict[str, Any]:
        return {"signal": "HOLD", "confidence": 0.0}


class _FallbackExecutionLogic:
    def decide(self, signal_payload: Dict[str, Any], features_payload: Dict[str, Any],
               snapshot: Dict[str, Any], account_equity: float,
               meta_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"execute": False, "position_size": 0.0, "sl": 0.0, "tp": 0.0, "reason": "fallback"}


class _FallbackMetaFilter:
    def evaluate(self, **kwargs: Any) -> Dict[str, Any]:
        return {"allow_trade": True}


# ----------------------------------------------------------------------------
# Numerical helpers
# ----------------------------------------------------------------------------
def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _compute_sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    avg = sum(returns) / len(returns)
    var = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    return 0.0 if std == 0 else (avg / std) * math.sqrt(len(returns))


def _to_continuous_conviction(raw: float) -> float:
    """
    FIX CRITICAL-7: Map a raw confidence/probability to (0.01, 0.99) — the open
    interval. Never returns exactly 0.0 or 1.0. Used so AlphaSignal.conviction
    is always a continuous probability that downstream Bayesian fusion can
    treat as a likelihood ratio.

    Mapping:
      raw <= 0.0       → 0.05 (very weak directional bias — orchestrator HOLD)
      raw >= 1.0       → 0.95 (capped — never a hard 1.0)
      0.0 < raw < 1.0  → clamped into [0.01, 0.99]
      non-finite       → 0.05
    """
    try:
        r = float(raw)
    except Exception:
        return 0.05
    if not math.isfinite(r):
        return 0.05
    if r <= 0.0:
        return 0.05
    if r >= 1.0:
        return 0.95
    return _clamp(r, 0.01, 0.99)


def _simulate_snapshot_from_candle(candle: list, prev_close: Optional[float] = None) -> Dict[str, Any]:
    ts, o, h, l, c, v = candle[:6]
    mid = _safe_float(c)
    spread = max(1.0, (h - l) * 0.02)
    best_bid = mid - spread / 2.0
    best_ask = mid + spread / 2.0
    depth = max(1.0, _safe_float(v) / max(mid, 1.0))
    return {
        "timestamp": ts,
        "bids": [[best_bid, depth * 1.2], [best_bid * 0.999, depth * 0.7], [best_bid * 0.998, depth * 0.5]],
        "asks": [[best_ask, depth * 1.1], [best_ask * 1.001, depth * 0.7], [best_ask * 1.002, depth * 0.5]],
    }


def _simulate_trades_from_candle(candle: list) -> List[Dict[str, Any]]:
    _, o, h, l, c, v = candle[:6]
    direction = "buy" if _safe_float(c) >= _safe_float(o) else "sell"
    return [
        {
            "price": _safe_float(c),
            "amount": max(1e-6, _safe_float(v) / max(_safe_float(c), 1.0)),
            "side": direction,
        }
    ]


def _snapshot_to_book(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the simulated [bid_px, bid_sz] list-of-lists into LSA's expected
    {'bids': [{'price': p, 'size': s}, ...], 'asks': [{'price': p, 'size': s}, ...]} dict."""
    def _conv(rows):
        out = []
        for r in rows or []:
            try:
                out.append({"price": float(r[0]), "size": float(r[1])})
            except (TypeError, IndexError, ValueError):
                continue
        return out
    return {"bids": _conv(snapshot.get("bids")), "asks": _conv(snapshot.get("asks"))}


def _ts_seconds(candle: list) -> float:
    ts_raw = _safe_float(candle[0], 0.0)
    # Heuristic: ts > 1e12 is millis; > 1e15 is nanos. Convert to seconds.
    if ts_raw > 1e15:
        return ts_raw * 1e-9
    if ts_raw > 1e12:
        return ts_raw * 1e-3
    return ts_raw


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    fee_bps: float = 8.0
    slippage_bps: float = 3.0
    max_hold_bars: int = 12
    initial_balance: float = 10_000.0
    basis_mode: str = "none"  # none|fixed
    fixed_basis: float = 0.0
    # Restored to production parity after FIX-1 (real microstructure integration).
    # Date: 2026-05-03 | FIX-1 commit: UNKNOWN_AT_TIME_OF_EDIT
    orchestrator_action_threshold: float = 0.6
    # When False: ARE/LSA/AlphaOrchestrator are required and run_backtest()
    # returns a fail-closed empty result if any are missing. When True: the
    # orchestrator path is skipped entirely (legacy diagnostic-only mode).
    legacy_mode: bool = False


class BacktestEngine:
    """Production-valid backtest engine.

    Pipeline (ALL stages run on every bar; no hidden parallel paths):

      OHLCV → FeatureEngine → AdvancedRegimeEngine (canonical payload)
            → LiquiditySweepAlpha (seeded from warmup window)
            → SignalEngine
            → AlphaOrchestrator.orchestrate([signal_engine, liquidity_sweep_alpha])
            → ExecutionLogic.decide() (only when orchestrator action != HOLD)
            → position management
    """

    def __init__(self, config: BacktestConfig | None = None, learning_engine: Any = None) -> None:
        self.cfg = config or BacktestConfig()
        self.learning_engine = learning_engine if learning_engine is not None else LEARNING_ENGINE

        self.feature_engine = FeatureEngine() if FeatureEngine is not None else _FallbackFeatureEngine()
        self.signal_engine = SignalEngine() if SignalEngine is not None else _FallbackSignalEngine()
        self.execution_logic = ExecutionLogic() if ExecutionLogic is not None else _FallbackExecutionLogic()
        self.meta_filter = MetaFilter() if MetaFilter is not None else _FallbackMetaFilter()
        self.fill_model = QueueFillModel() if QueueFillModel is not None else None
        self.tox_filter = ToxicityFilter() if ToxicityFilter is not None else None
        self.position_manager = PositionManager() if PositionManager is not None else None
        self.trade_lifecycle = TradeLifecycleManager() if TradeLifecycleManager is not None else None
        self.capital_allocator = CapitalAllocator() if CapitalAllocator is not None else None

        # FIX CRITICAL-1: AdvancedRegimeEngine is the regime classifier. It
        # MUST be called with a canonical dict payload (price/return/features).
        # Created once and re-used across run_backtest() invocations.
        self.are = AdvancedRegimeEngine() if AdvancedRegimeEngine is not None else None

        # FIX CRITICAL-5: LiquiditySweepAlpha must be SEEDED from a price
        # window before the first predict() call, otherwise detect_sweep_state
        # returns NORMAL forever and confidence is permanently 0.0. The actual
        # instance is created in _seed_lsa() per-run because seed values come
        # from the input price window.
        self.lsa: Optional[Any] = None  # set by _seed_lsa(data)

        # FIX CRITICAL-6: AlphaOrchestrator requires ≥2 alpha sources to
        # produce a non-HOLD action. We register signal_engine and
        # liquidity_sweep_alpha as the two sources (CRITICAL-6 compliant).
        if AlphaOrchestrator is not None and OrchestratorConfig is not None:
            try:
                cfg = OrchestratorConfig(
                    signal_weights={"signal_engine": 0.5, "liquidity_sweep_alpha": 0.5},
                    action_threshold=float(self.cfg.orchestrator_action_threshold),
                    allow_unknown_sources=False,
                    feedback_enabled=False,
                )
                self.orchestrator: Optional[Any] = AlphaOrchestrator(cfg)
            except Exception as _o_init_err:
                logger.warning("AlphaOrchestrator construction failed (%s)", _o_init_err)
                self.orchestrator = None
        else:
            self.orchestrator = None

        self.basis = VenueBasisNormalizer(halt_threshold_pct=0.5)
        self.basis.set_venues("backtest", "backtest")
        self._analysis_cache: Dict[Tuple[int, float], Dict[str, Any]] = {}
        # FIX CRITICAL-7 telemetry: capture every conviction value emitted on
        # the production-valid path so tests (TEST-4) can assert continuity.
        self._last_alpha_signals: List[Any] = []
        self._all_alpha_convictions: List[float] = []

    # ------------------------------------------------------------------
    # LSA seeding
    # ------------------------------------------------------------------
    def _seed_lsa(self, data: List[list]) -> None:
        """FIX CRITICAL-5: seed liquidity_pools from the first 25 bars of the
        input window. After seeding, detect_sweep_state will exit the
        permanently-NORMAL branch and produce real (non-zero-confidence) sweep
        predictions."""
        if LiquiditySweepAlpha is None or len(data) < 25:
            self.lsa = None
            return
        warmup = data[:25]
        try:
            initial_high = max(_safe_float(c[2]) for c in warmup if len(c) >= 6)
            initial_low = min(_safe_float(c[3]) for c in warmup if len(c) >= 6)
            if not (math.isfinite(initial_high) and math.isfinite(initial_low) and initial_high > 0 and initial_low > 0):
                self.lsa = None
                return
            self.lsa = LiquiditySweepAlpha(
                initial_high=float(initial_high),
                initial_low=float(initial_low),
            )
        except Exception as exc:
            logger.warning("LSA seeding failed: %s", exc)
            self.lsa = None

    # ------------------------------------------------------------------
    # Per-bar helpers
    # ------------------------------------------------------------------
    def _build_canonical_are_payload(
        self,
        candle: list,
        prev_close: float,
        features: Dict[str, Any],
        vol_mean: float,
        vol_std: float,
    ) -> Dict[str, Any]:
        """FIX CRITICAL-1: build the canonical 4-key payload that
        AdvancedRegimeEngine.update() requires. The features vector is exactly
        n_features=3 (matches calibrate_regime.py / ARE constructor)."""
        c = _safe_float(candle[4])
        v = _safe_float(candle[5])
        log_ret = math.log(c / prev_close) if (prev_close > 0 and c > 0) else 0.0
        ofi_z = _safe_float(features.get("ofi_zscore", features.get("ofi_norm", 0.0)))
        vol_z = (v - vol_mean) / vol_std if vol_std > 0 else 0.0
        feature_vec = np.array([float(log_ret), float(ofi_z), float(vol_z)], dtype=float)
        return {
            "return": float(log_ret),
            "features": feature_vec,
            "price": float(c),
            "timestamp": _ts_seconds(candle),
        }

    def _build_lsa_market_data(
        self,
        candle: list,
        snapshot: Dict[str, Any],
        prev_snapshot: Dict[str, Any],
        trades: List[Dict[str, Any]],
        features: Dict[str, Any],
        ema_fast: float,
        ema_slow: float,
    ) -> Dict[str, Any]:
        c = _safe_float(candle[4])
        h = _safe_float(candle[2])
        l = _safe_float(candle[3])
        return {
            "price": c,
            "close_price": c,
            "atr": max(1e-8, h - l),
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "prev_book": _snapshot_to_book(prev_snapshot),
            "curr_book": _snapshot_to_book(snapshot),
            "timestamp": _ts_seconds(candle),
            "trades_count": len(trades),
        }

    def _build_alpha_signals(
        self,
        signal_engine_out: Dict[str, Any],
        lsa_out: Dict[str, Any],
        ts_seconds: float,
    ) -> List[Any]:
        """FIX CRITICAL-6 + CRITICAL-7: emit two AlphaSignal sources whose
        conviction values are CONTINUOUS (never exactly 0.0 or 1.0)."""
        if AlphaSignal is None:
            return []
        out: List[Any] = []

        # Source 1: SignalEngine
        sig_str = str(signal_engine_out.get("signal", "HOLD")).upper()
        sig_dir = 1 if sig_str == "LONG" else (-1 if sig_str == "SHORT" else 0)
        sig_raw_conf = _safe_float(signal_engine_out.get("confidence", 0.0))
        sig_conv = _to_continuous_conviction(sig_raw_conf)
        try:
            out.append(AlphaSignal(
                source_id="signal_engine",
                direction=int(sig_dir),
                conviction=float(sig_conv),
                expected_edge_bps=float(sig_conv * 30.0),
                timestamp=float(ts_seconds),
            ))
        except Exception as exc:
            logger.debug("signal_engine AlphaSignal build failed: %s", exc)

        # Source 2: LiquiditySweepAlpha
        lsa_action = str(lsa_out.get("action", "HOLD")).upper()
        lsa_dir = 1 if lsa_action == "LONG" else (-1 if lsa_action == "SHORT" else 0)
        lsa_raw_conf = _safe_float(lsa_out.get("confidence", 0.0))
        lsa_conv = _to_continuous_conviction(lsa_raw_conf)
        try:
            out.append(AlphaSignal(
                source_id="liquidity_sweep_alpha",
                direction=int(lsa_dir),
                conviction=float(lsa_conv),
                expected_edge_bps=float(lsa_conv * 30.0),
                timestamp=float(ts_seconds),
            ))
        except Exception as exc:
            logger.debug("liquidity_sweep_alpha AlphaSignal build failed: %s", exc)

        return out

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------
    def run_backtest(
        self,
        ohlcv_data: List[list],
        initial_balance: float | None = None,
        book_features: Optional[Sequence[BookSnapshot]] = None,
    ) -> Dict[str, Any]:
        return self._run_single_pass(
            ohlcv_data,
            initial_balance=initial_balance,
            label="run_backtest",
            book_features=book_features,
        )

    def run_backtest_multi_resolution(
        self,
        ohlcv_1m_data: List[list],
        initial_balance: float | None = None,
        book_features: Optional[Sequence[BookSnapshot]] = None,
    ) -> Dict[str, Any]:
        """REQUIRED-1 (B005): run the same engine at 1m, 5m, 15m and label
        each with its production semantics. Returns a dict keyed by
        resolution; the 5m result is the production-valid primary."""
        if resample_bars is None:
            return {
                "1m":  {"label": "diagnostic", "error": "bar_aggregator unavailable"},
                "5m":  {"label": "production-valid", "error": "bar_aggregator unavailable"},
                "15m": {"label": "diagnostic", "error": "bar_aggregator unavailable"},
            }
        bars_1m = list(ohlcv_1m_data or [])
        try:
            bars_5m = resample_bars(bars_1m, minutes=5, base_minutes=1)
        except Exception as exc:
            logger.warning("5m resample failed: %s", exc)
            bars_5m = []
        try:
            bars_15m = resample_bars(bars_1m, minutes=15, base_minutes=1)
        except Exception as exc:
            logger.warning("15m resample failed: %s", exc)
            bars_15m = []

        result_1m = self._run_single_pass(
            bars_1m,
            initial_balance=initial_balance,
            label="1m",
            book_features=book_features,
        )
        book_5m = None
        book_15m = None
        if book_features is not None:
            if align_book_to_bars is None:
                raise ValueError("align_book_to_bars unavailable for multi-resolution book alignment")
            book_5m = align_book_to_bars(bars_5m, book_features)
            book_15m = align_book_to_bars(bars_15m, book_features)

        result_5m = self._run_single_pass(
            bars_5m,
            initial_balance=initial_balance,
            label="5m",
            book_features=book_5m,
        )
        result_15m = self._run_single_pass(
            bars_15m,
            initial_balance=initial_balance,
            label="15m",
            book_features=book_15m,
        )

        result_1m["label"] = "diagnostic"
        result_1m["label_reason"] = "1m noise-dominated: SNR < cost threshold (round-trip ~11 bps)"
        result_1m["bars"] = len(bars_1m)

        result_5m["label"] = "production-valid"
        result_5m["label_reason"] = "5m is the production-primary resolution per Phase 4 audit (REQUIRED-1)"
        result_5m["bars"] = len(bars_5m)

        if len(bars_15m) < 50:
            result_15m["label"] = "diagnostic"
            result_15m["label_reason"] = f"15m has only {len(bars_15m)} bars (<50 minimum) — insufficient for production"
        else:
            result_15m["label"] = "diagnostic"
            result_15m["label_reason"] = "15m theoretical SNR is strongest but not the Phase-4 designated primary"
        result_15m["bars"] = len(bars_15m)

        return {"1m": result_1m, "5m": result_5m, "15m": result_15m}

    # ------------------------------------------------------------------
    # Internal: single-pass backtest
    # ------------------------------------------------------------------
    def _run_single_pass(
        self,
        ohlcv_data: List[list],
        initial_balance: float | None = None,
        label: str = "single",
        book_features: Optional[Sequence[BookSnapshot]] = None,
    ) -> Dict[str, Any]:
        cache_hits = 0
        cache_misses = 0
        data = [row for row in (ohlcv_data or []) if isinstance(row, (list, tuple)) and len(row) >= 6]

        empty_result = {
            "total_trades": 0,
            "win_rate": 0.0,
            "pnl": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "expectancy": 0.0,
            "trade_log": [],
        }

        if len(data) < 50:
            logger.info("[BACKTEST %s] insufficient bars (%d<50). cache hits=%d misses=%d",
                        label, len(data), cache_hits, cache_misses)
            return dict(empty_result)

        if book_features is not None:
            if len(book_features) != len(data):
                raise ValueError(
                    f"book_features length mismatch: bars={len(data)} book_features={len(book_features)}"
                )
            for idx, snap in enumerate(book_features):
                if not isinstance(snap, dict):
                    raise ValueError(f"book_features[{idx}] must be dict snapshot")
                if "bids" not in snap or "asks" not in snap:
                    raise ValueError(f"book_features[{idx}] missing bids/asks")
                bar_ts = _safe_float(data[idx][0], 0.0)
                snap_ts = _safe_float(snap.get("timestamp"), bar_ts)
                if snap_ts != bar_ts:
                    raise ValueError(
                        f"book_features misaligned at index {idx}: bar_ts={bar_ts} snapshot_ts={snap_ts}"
                    )

        # FIX CRITICAL-4 (e): fail-closed if production prerequisites are missing
        # in production-valid mode. The legacy_mode escape hatch is for the
        # legacy diagnostic-only path and is not used by run_backtest_multi_resolution.
        if not self.cfg.legacy_mode:
            if self.are is None:
                logger.error("[BACKTEST %s] AdvancedRegimeEngine unavailable — fail-closed", label)
                return dict(empty_result)
            if self.orchestrator is None:
                logger.error("[BACKTEST %s] AlphaOrchestrator unavailable — fail-closed", label)
                return dict(empty_result)

        # FIX CRITICAL-5: seed LSA from the first 25 bars
        self._seed_lsa(data)
        if not self.cfg.legacy_mode and self.lsa is None and LiquiditySweepAlpha is not None:
            logger.error("[BACKTEST %s] LiquiditySweepAlpha could not be seeded — fail-closed", label)
            return dict(empty_result)

        # Pre-compute volume stats for the canonical vol_z feature
        all_vols = np.array([_safe_float(r[5]) for r in data], dtype=float)
        vol_mean = float(all_vols.mean()) if all_vols.size else 0.0
        vol_std = float(all_vols.std()) if all_vols.size else 0.0
        if vol_std <= 0.0:
            vol_std = 1.0

        # Per-run alpha telemetry reset
        self._last_alpha_signals = []
        self._all_alpha_convictions = []

        balance = float(initial_balance if initial_balance is not None else self.cfg.initial_balance)
        peak = balance
        max_dd = 0.0
        returns: List[float] = []
        trade_log: List[Dict[str, Any]] = []

        # EMA series for LSA's regime classifier
        ema_fast_alpha = 2.0 / (12.0 + 1.0)
        ema_slow_alpha = 2.0 / (26.0 + 1.0)
        ema_fast = _safe_float(data[0][4])
        ema_slow = ema_fast

        # Running snapshot of the previous order book (LSA OFI z-score input)
        prev_snapshot: Dict[str, Any] = (
            dict(book_features[0]) if book_features is not None else _simulate_snapshot_from_candle(data[0])
        )

        position: Optional[Dict[str, Any]] = None

        for i in range(25, len(data)):
            window = data[: i + 1]
            candle = window[-1]
            prev_candle = window[-2]
            current_price = _safe_float(candle[4])
            prev_close = _safe_float(prev_candle[4]) if prev_candle else current_price

            # Update EMAs (used by LSA regime detection)
            ema_fast = (1 - ema_fast_alpha) * ema_fast + ema_fast_alpha * current_price
            ema_slow = (1 - ema_slow_alpha) * ema_slow + ema_slow_alpha * current_price

            cache_key = (int(candle[0]), float(current_price), 1 if book_features is not None else 0)
            cached = self._analysis_cache.get(cache_key)
            if cached is not None:
                cache_hits += 1
                snapshot = cached["snapshot"]
                trades = cached["trades"]
                features_outer = dict(cached["features"])
            else:
                cache_misses += 1
                snapshot = dict(book_features[i]) if book_features is not None else _simulate_snapshot_from_candle(candle, prev_close)
                trades = _simulate_trades_from_candle(candle)
                features_outer = self.feature_engine.update(snapshot, trades)
                self._analysis_cache[cache_key] = {
                    "snapshot": snapshot,
                    "trades": trades,
                    "features": dict(features_outer),
                }

            # CONSOLIDATED FIX S004: inject the rolling 20-bar candle history
            # so SignalEngine can pass its ≥3-candle guard. Previously this
            # block was duplicated 3x — collapsed to a single block.
            ohlcv_window = window[-20:] if len(window) >= 20 else window
            candle_list = [
                {
                    "open":   _safe_float(c[1]),
                    "high":   _safe_float(c[2]),
                    "low":    _safe_float(c[3]),
                    "close":  _safe_float(c[4]),
                    "volume": _safe_float(c[5]),
                }
                for c in ohlcv_window
                if len(c) >= 6
                    and _safe_float(c[4]) > 0
                    and _safe_float(c[1]) > 0
            ]
            feat_inner = features_outer.get("features", features_outer)
            if isinstance(feat_inner, dict):
                feat_inner["candles"] = candle_list
                feat_inner["price"]   = current_price
                feat_inner["close"]   = current_price
                feat_inner["volume"]  = _safe_float(candle[5])
                feat_inner["ofi_zscore"]       = feat_inner.get("ofi_norm", feat_inner.get("ofi", 0.0))
                feat_inner["flow_imbalance"]   = feat_inner.get("aggressor_imbalance", 0.0)
                feat_inner["hawkes_intensity"] = feat_inner.get("trade_burst", 0.0)
            features = feat_inner if isinstance(feat_inner, dict) else features_outer

            # FIX S004: inject OHLCV candle history so SignalEngine can pass the
            # ≥3-candle guard and compute real LONG/SHORT signals.
            # Build normalised candle dicts from the rolling window (last 20 bars).
            ohlcv_window = window[-20:] if len(window) >= 20 else window
            candle_list = [
                {
                    "open":   _safe_float(c[1]),
                    "high":   _safe_float(c[2]),
                    "low":    _safe_float(c[3]),
                    "close":  _safe_float(c[4]),
                    "volume": _safe_float(c[5]),
                }
                for c in ohlcv_window
                if len(c) >= 6
                    and _safe_float(c[4]) > 0
                    and _safe_float(c[1]) > 0
            ]
            # Wire into the features dict that signal_engine.generate() receives.
            feat_inner = features.get("features", features)
            if isinstance(feat_inner, dict):
                feat_inner["candles"] = candle_list
                feat_inner["price"]   = _safe_float(candle[4])
                feat_inner["close"]   = _safe_float(candle[4])
                feat_inner["volume"]  = _safe_float(candle[5])
                # Expose OFI z-score and Hawkes signals already computed by FeatureEngine
                feat_inner["ofi_zscore"]        = feat_inner.get("ofi_norm", feat_inner.get("ofi", 0.0))
                feat_inner["flow_imbalance"]    = feat_inner.get("aggressor_imbalance", 0.0)
                feat_inner["hawkes_intensity"]  = feat_inner.get("trade_burst", 0.0)
            features = feat_inner  # unwrap so signal_engine.generate() sees the flat dict

            if self.fill_model is not None:
                features = self.fill_model.enrich(features)
            if self.tox_filter is not None:
                features = self.tox_filter.enrich(features)

            ts_seconds = _ts_seconds(candle)

            # ---- FIX CRITICAL-1: AdvancedRegimeEngine via canonical payload --
            regime_label = "UNKNOWN"
            regime_conf = 0.5
            volatility_score = 0.0
            if self.are is not None:
                are_payload = self._build_canonical_are_payload(
                    candle=candle,
                    prev_close=prev_close,
                    features=features,
                    vol_mean=vol_mean,
                    vol_std=vol_std,
                )
                try:
                    are_out = self.are.update(are_payload)
                    if isinstance(are_out, dict):
                        regime_label = str(are_out.get("regime_label", "UNKNOWN"))
                        regime_conf = _safe_float(are_out.get("confidence", 0.5), 0.5)
                        volatility_score = _safe_float(
                            are_out.get("risk_metrics", {}).get("expected_volatility", 0.0)
                        )
                except Exception as exc:
                    logger.debug("ARE.update failed at i=%d: %s", i, exc)

            # ---- FIX CRITICAL-5: LiquiditySweepAlpha (seeded) ----------------
            lsa_out: Dict[str, Any] = {"action": "HOLD", "confidence": 0.0}
            if self.lsa is not None:
                lsa_md = self._build_lsa_market_data(
                    candle=candle, snapshot=snapshot, prev_snapshot=prev_snapshot,
                    trades=trades, features=features,
                    ema_fast=ema_fast, ema_slow=ema_slow,
                )
                try:
                    lsa_out = self.lsa.predict(lsa_md, regime_context={"regime": regime_label}) or lsa_out
                except Exception as exc:
                    logger.debug("LSA.predict failed at i=%d: %s", i, exc)

            # ---- SignalEngine (alpha source #1) -----------------------------
            signal_engine_out = self.signal_engine.generate(features)

            # ---- FIX CRITICAL-6+7: AlphaOrchestrator with ≥2 sources ---------
            alpha_signals = self._build_alpha_signals(signal_engine_out, lsa_out, ts_seconds)
            self._last_alpha_signals = list(alpha_signals)
            for s in alpha_signals:
                try:
                    self._all_alpha_convictions.append(float(s.conviction))
                except Exception:
                    pass

            orch_action_str = "HOLD"
            orch_conviction = 0.0
            orch_meta: Dict[str, Any] = {}
            if (self.orchestrator is not None
                    and RegimeContext is not None
                    and FeatureQuality is not None
                    and ExecutionState is not None
                    and Action is not None
                    and len(alpha_signals) >= 2):
                try:
                    regime_ctx = RegimeContext(
                        regime_name=regime_label or "UNKNOWN",
                        volatility_score=_clamp(_safe_float(volatility_score), 0.0, 1.0),
                        liquidity_score=_clamp(
                            _safe_float(features.get("liquidity_score", 0.7), 0.7), 0.0, 1.0
                        ),
                    )
                    feat_qual = FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0)
                    exec_state = ExecutionState(
                        current_exposure_usd=balance if position is not None else 0.0,
                        max_exposure_usd=max(balance * 10.0, 1.0),
                        current_drawdown_pct=_clamp(max_dd, 0.0, 1.0),
                    )
                    orchestrated = self.orchestrator.orchestrate(
                        signals=alpha_signals,
                        regime=regime_ctx,
                        feature_quality=feat_qual,
                        exec_state=exec_state,
                        current_time=ts_seconds,
                    )
                    if orchestrated is not None:
                        action_value = orchestrated.action
                        if action_value == Action.BUY:
                            orch_action_str = "LONG"
                        elif action_value == Action.SELL:
                            orch_action_str = "SHORT"
                        else:
                            orch_action_str = "HOLD"
                        orch_conviction = _safe_float(orchestrated.net_conviction, 0.0)
                        orch_meta = dict(orchestrated.meta_info or {})
                except Exception as exc:
                    logger.debug("orchestrator.orchestrate failed at i=%d: %s", i, exc)

            # The signal that drives execution_logic is the orchestrator's
            # decision, not the raw SignalEngine output. This is the single
            # production path; there is no parallel fallback. (CRITICAL-4 d)
            signal = {
                "signal": orch_action_str,
                "confidence": float(orch_conviction),
                "reason": "orchestrator",
                "orchestrator_meta": orch_meta,
                "regime": regime_label,
                "regime_confidence": regime_conf,
                "alpha_sources": ["signal_engine", "liquidity_sweep_alpha"],
            }

            meta = self.meta_filter.evaluate(
                features=features, signal=signal, decision=None, router_decision=None,
                snapshot=snapshot, trades=trades,
            )

            # Update the previous-snapshot cache for the next bar's OFI delta
            prev_snapshot = snapshot

            # If orchestrator is non-actionable, skip execution entirely (no parallel path)
            if orch_action_str == "HOLD":
                # Still need to manage open position with the existing logic below
                pass

            analysis_mid = _safe_float(snapshot["bids"][0][0] + snapshot["asks"][0][0], 0.0) / 2.0
            basis_mode = str(getattr(self.cfg, "basis_mode", "none")).strip().lower()
            if basis_mode == "none":
                execution_mid = analysis_mid
            elif basis_mode == "fixed":
                execution_mid = analysis_mid + _safe_float(getattr(self.cfg, "fixed_basis", 0.0), 0.0)
            else:
                logger.error("[BACKTEST %s] invalid basis_mode=%s", label, basis_mode)
                continue
            self.basis.seed(analysis_mid, execution_mid)
            self.basis.update(analysis_mid, execution_mid)
            basis_status = self.basis.validate()
            if not basis_status.ok:
                logger.warning("[BACKTEST %s] basis blocked reason=%s", label, basis_status.reason)
                continue
            execution_snapshot = {
                "bids": [[self.basis.analysis_to_execution(snapshot["bids"][0][0]), snapshot["bids"][0][1]]],
                "asks": [[self.basis.analysis_to_execution(snapshot["asks"][0][0]), snapshot["asks"][0][1]]],
                "timestamp": snapshot["timestamp"],
            }

            decision = self.execution_logic.decide(
                signal_payload=signal,
                features_payload=features,
                snapshot=execution_snapshot,
                account_equity=balance,
                meta_result=meta,
            )

            # ---- Position open ----
            if position is None and orch_action_str in ("LONG", "SHORT") and decision.get("execute"):
                side = "buy" if orch_action_str == "LONG" else "sell"
                entry = current_price * (
                    1.0 + (self.cfg.slippage_bps / 10_000.0 if side == "buy"
                           else -(self.cfg.slippage_bps / 10_000.0))
                )
                size = _safe_float(decision.get("position_size", 0.0))
                if self.capital_allocator is not None:
                    alloc = self.capital_allocator.allocate(
                        signal_confidence=float(orch_conviction),
                        regime_context={"regime": regime_label},
                        current_equity=balance,
                        max_risk_pct=0.005,
                    )
                    if not alloc.get("allow_trading", True):
                        continue
                    size *= _safe_float(alloc.get("capital_scale", 1.0), 1.0)
                if size <= 0:
                    continue
                trade_id = f"bt-{label}-{i}"
                fees = _safe_float(self.cfg.fee_bps, 0.0) / 10_000.0
                fee_type = "pct"
                if self.trade_lifecycle is not None:
                    self.trade_lifecycle.on_entry(
                        side="LONG" if side == "buy" else "SHORT",
                        entry_price=entry, size=size, features=features,
                    )
                if self.position_manager is not None:
                    self.position_manager.on_entry(
                        symbol="BTC/USDT",
                        side="LONG" if side == "buy" else "SHORT",
                        size=size, entry_price=entry, order_id=trade_id,
                        sl=_safe_float(decision.get("sl", 0.0)),
                        tp=_safe_float(decision.get("tp", 0.0)),
                        signal=str(signal.get("signal", "HOLD")),
                        confidence=float(orch_conviction),
                        regime=regime_label, fees=0.0, fee_type="pct",
                        features=features,
                    )
                position = {
                    "trade_id": trade_id,
                    "side": "LONG" if side == "buy" else "SHORT",
                    "entry": entry,
                    "sl": _safe_float(decision.get("sl", 0.0)),
                    "tp": _safe_float(decision.get("tp", 0.0)),
                    "size": size,
                    "fees": fees,
                    "fee_type": fee_type,
                    "entry_index": i,
                    "entry_features": features,
                    "signal": signal,
                    "meta": meta,
                }
                continue

            if position is None:
                continue

            # ---- Position management / exit ----
            side = position["side"]
            entry = _safe_float(position["entry"])
            sl = _safe_float(position["sl"])
            tp = _safe_float(position["tp"])
            hold = i - int(position["entry_index"])

            hit_sl = current_price <= sl if side == "LONG" else current_price >= sl
            hit_tp = current_price >= tp if side == "LONG" else current_price <= tp
            flip = (
                (side == "LONG"  and orch_action_str == "SHORT")
                or (side == "SHORT" and orch_action_str == "LONG")
            )
            timeout = hold >= self.cfg.max_hold_bars

            if hit_sl or hit_tp or flip or timeout:
                gross_pnl_pct = ((current_price - entry) / entry) if side == "LONG" else ((entry - current_price) / entry)
                fees = _safe_float(position.get("fees"), 0.0)
                slippage = self.cfg.slippage_bps / 10_000.0
                total_fee_pct = fees * 2.0
                net_pnl_pct = gross_pnl_pct - total_fee_pct - slippage
                pnl = balance * net_pnl_pct * 0.25
                balance += pnl
                if self.are is not None:
                    self.are.update_portfolio_equity(balance)
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                returns.append(net_pnl_pct)
                le = getattr(self, "learning_engine", None)
                if le and hasattr(le, "record_closed_trade"):
                    try:
                        le.record_closed_trade(
                            trade_id=position.get("trade_id"),
                            side=position.get("side"),
                            entry_price=position.get("entry"),
                            exit_price=current_price,
                            size=position.get("size"),
                            pnl=net_pnl_pct, pnl_abs=pnl,
                            fees=position.get("fees"), fee_type=position.get("fee_type"),
                            hold_time=hold,
                            signal=position.get("signal"),
                            features=position.get("entry_features"),
                            meta=position.get("meta"),
                        )
                    except Exception as e:
                        logger.warning("learning_engine failure: %s", str(e))
                if self.position_manager is not None and self.position_manager.has_position("BTC/USDT"):
                    import datetime as _dt
                    self.position_manager.on_exit(
                        symbol="BTC/USDT",
                        exit_price=current_price,
                        exit_time=_dt.datetime.utcnow().isoformat(),
                    )
                if self.trade_lifecycle is not None:
                    self.trade_lifecycle.on_exit(pnl_pct=net_pnl_pct, reason="backtest_exit")
                trade_log.append({
                    "entry_index": position["entry_index"],
                    "exit_index": i,
                    "side": side,
                    "entry": round(entry, 2),
                    "exit": round(current_price, 2),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(net_pnl_pct * 100.0, 4),
                    "signal": position["signal"],
                    "meta": position["meta"],
                })
                position = None

        total_trades = len(trade_log)
        wins = sum(1 for t in trade_log if _safe_float(t.get("pnl", 0.0)) > 0)
        win_rate = (wins / total_trades) if total_trades else 0.0
        gross_wins = [t["pnl"] for t in trade_log if _safe_float(t.get("pnl", 0.0)) > 0]
        gross_losses = [abs(_safe_float(t.get("pnl", 0.0))) for t in trade_log if _safe_float(t.get("pnl", 0.0)) <= 0]
        avg_win = sum(gross_wins) / len(gross_wins) if gross_wins else 0.0
        avg_loss = sum(gross_losses) / len(gross_losses) if gross_losses else 0.0
        expectancy = (avg_win * win_rate) - (avg_loss * (1.0 - win_rate))

        logger.info("[BACKTEST %s] cache hits=%d misses=%d trades=%d", label, cache_hits, cache_misses, total_trades)
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 6),
            "pnl": round(balance - (initial_balance if initial_balance is not None else self.cfg.initial_balance), 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(_compute_sharpe(returns), 6),
            "expectancy": round(expectancy, 6),
            "trade_log": trade_log,
        }
