# backtest_engine.py
from __future__ import annotations

import json
import logging
import math
import os
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
    from engine import create_backtest_magnet_predictor
except Exception as _magnet_err:
    create_backtest_magnet_predictor = None  # type: ignore[assignment,misc]
    logging.getLogger(__name__).warning(
        "backtest_engine: isolated LiquidityMagnetPredictor factory import failed (%s)", _magnet_err
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


def _extract_funding_rate(features: Dict[str, Any]) -> float:
    raw = features.get("funding_rate_8h", features.get("funding_rate", 0.0))
    rate = _safe_float(raw, 0.0)
    return rate if math.isfinite(rate) else 0.0


def calculate_funding_payment(
    *,
    side: str,
    entry_price: float,
    size: float,
    funding_rate: float,
    bar_interval_hours: float,
    funding_interval_hours: float,
) -> float:
    """Return funding cashflow for one bar. Positive means equity credit."""
    notional = max(0.0, abs(size) * max(entry_price, 0.0))
    if notional <= 0.0 or funding_rate == 0.0:
        return 0.0
    interval_fraction = max(0.0, bar_interval_hours) / max(funding_interval_hours, 1e-12)
    direction = -1.0 if str(side).upper() == "LONG" else 1.0
    return float(direction * notional * funding_rate * interval_fraction)


def calculate_trade_pnl(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    fee_pct: float,
    slippage_pct: float,
    funding_pnl: float = 0.0,
) -> Tuple[float, float]:
    """Return absolute PnL and net return on entry notional for a qty-sized trade."""
    qty = abs(size)
    if qty <= 0.0 or entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0, 0.0
    gross = (exit_price - entry_price) * qty if str(side).upper() == "LONG" else (entry_price - exit_price) * qty
    entry_notional = entry_price * qty
    exit_notional = exit_price * qty
    fees_abs = max(0.0, fee_pct) * (entry_notional + exit_notional)
    slippage_abs = max(0.0, slippage_pct) * exit_notional
    pnl_abs = gross - fees_abs - slippage_abs + funding_pnl
    return float(pnl_abs), float(pnl_abs / max(entry_notional, 1e-12))


def simulate_queue_fill(
    *,
    side: str,
    remaining_qty: float,
    features: Dict[str, Any],
) -> Tuple[float, float]:
    """Queue-aware deterministic fill quantity and fill fraction for one bar."""
    qty = max(0.0, float(remaining_qty))
    if qty <= 0.0:
        return 0.0, 0.0
    direction = str(side).lower()
    prob_key = "fill_prob_long" if direction == "buy" else "fill_prob_short"
    fill_prob = _safe_float(features.get(prob_key, features.get("fill_probability", features.get("fill_prob", 1.0))), 1.0)
    confidence = _safe_float(features.get("fill_confidence", 1.0), 1.0)
    fraction = _clamp(fill_prob * confidence, 0.0, 1.0)
    if direction == "buy":
        displayed_qty = _safe_float(features.get("top_ask_qty", features.get("ask_depth_n", qty)), qty)
    else:
        displayed_qty = _safe_float(features.get("top_bid_qty", features.get("bid_depth_n", qty)), qty)
    liquidity_cap = max(0.0, displayed_qty)
    fill_qty = min(qty, qty * fraction, liquidity_cap if liquidity_cap > 0.0 else 0.0)
    return float(fill_qty), float(0.0 if qty <= 0.0 else fill_qty / qty)

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
    funding_interval_hours: float = 8.0
    bar_interval_hours: float = 5.0 / 60.0
    queue_fill_timeout_bars: int = 3
    basis_mode: str = "none"  # none|fixed
    fixed_basis: float = 0.0
    # Phase 4: orchestrator action threshold (lower than production default of
    # 0.6 because backtest convictions are clamped to (0.01, 0.99) and
    # synthetic data does not produce extreme convictions).
    orchestrator_action_threshold: float = 0.60  # FIX-2: raised from 0.30 per audit
    # When False: ARE/LSA/AlphaOrchestrator are required and run_backtest()
    # returns a fail-closed empty result if any are missing. When True: the
    # orchestrator path is skipped entirely (legacy diagnostic-only mode).
    legacy_mode: bool = False


class BacktestEngine:
    """Production-valid backtest engine.

    Pipeline (ALL stages run on every bar; no hidden parallel paths):

      OHLCV → FeatureEngine → AdvancedRegimeEngine (canonical payload)
            → LiquiditySweepAlpha (seeded from warmup window)
            → SignalEngine + LiquidityMagnetPredictor (isolated canonical backtest instance)
            → AlphaOrchestrator.orchestrate([signal_engine, liquidity_sweep_alpha, liquidity_magnet_alpha])
            → ExecutionLogic.decide() (only when orchestrator action != HOLD)
            → position management
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        learning_engine: Any = None,
        *,
        signal_only: bool = False,
    ) -> None:
        import os as _os  # AUDIT FIX ISSUE-A
        if _os.environ.get("BTCBOT_LIVE_MODE") == "1":
            raise RuntimeError(
                "BacktestEngine must not be instantiated while BTCBOT_LIVE_MODE=1. "
                "Unset BTCBOT_LIVE_MODE before running backtests."
            )
        _cfg = config or BacktestConfig()
        if signal_only:
            _cfg = BacktestConfig(**{**_cfg.__dict__, "legacy_mode": True})
        self.cfg = _cfg
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

        # FIX-6 (REGIME_ENGINE_AUDIT 2026-04-23): load calibration-time
        # feature normalization (feature_mean / feature_std) so that the
        # canonical ARE payload is on the SAME scale used to fit NHHMM/SJM.
        # Without this, runtime z-scores drift away from the calibration
        # distribution and emission probabilities collapse to BEAR ~99% of
        # bars. _load_calibration_norms is fail-closed: if the .npz is
        # missing the keys, _build_canonical_are_payload refuses to emit
        # the bar (sets _invalid=True) rather than silently using the
        # wrong scale.
        self._calibration_feature_mean: Optional[np.ndarray] = None
        self._calibration_feature_std: Optional[np.ndarray] = None
        self._load_calibration_norms()

        # FIX CRITICAL-5: LiquiditySweepAlpha must be SEEDED from a price
        # window before the first predict() call, otherwise detect_sweep_state
        # returns NORMAL forever and confidence is permanently 0.0. The actual
        # instance is created in _seed_lsa() per-run because seed values come
        # from the input price window.
        self.lsa: Optional[Any] = None  # set by _seed_lsa(data)

        # Fix: backtest validation previously omitted liquidity_magnet_alpha,
        # creating silent non-parity with live routing. Root cause: only two
        # sources were registered here. After: include the magnet source; if
        # production-equivalent liquidity zones are unavailable in replay,
        # run_backtest marks the result NON-PRODUCTION-VALID instead of
        # fabricating parity.
        if AlphaOrchestrator is not None and OrchestratorConfig is not None:
            try:
                cfg = OrchestratorConfig(
                    signal_weights={
                        "signal_engine": 0.5,
                        "liquidity_sweep_alpha": 0.5,
                        "liquidity_magnet_alpha": 0.5,
                    },
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

        self.magnet_predictor: Optional[Any] = None

        self.basis = VenueBasisNormalizer(halt_threshold_pct=0.5)
        self.basis.set_venues("backtest", "backtest")
        self._analysis_cache: Dict[Tuple[int, float], Dict[str, Any]] = {}
        # FIX CRITICAL-7 telemetry: capture every conviction value emitted on
        # the production-valid path so tests (TEST-4) can assert continuity.
        self._last_alpha_signals: List[Any] = []
        self._all_alpha_convictions: List[float] = []

        # FIX M-1 APPLIED — pre-compute per-minute aggTrades counts so the
        # LSA market_data carries a REAL volume proxy instead of a synthetic
        # one (len(trades) was a tiny per-bar slice, not the true count).
        self._agg_trades_counts: Dict[int, int] = self._load_agg_trades_counts()

    def _load_agg_trades_counts(self) -> Dict[int, int]:
        """
        Returns {timestamp_minute_floor_ms -> int count} from the aggTrades
        CSV. Supports both Binance Vision schema (`transact_time`) and the
        REST-shaped schema (`T`). Fail-soft: returns {} if the file is
        missing or unreadable so the backtest still runs (with the legacy
        len(trades) fallback inside _build_lsa_market_data).
        # FIX M-1 APPLIED
        """
        try:
            import os
            import pandas as pd
            path = os.path.join("data", "aggTrades_dec2023.csv")
            if not os.path.exists(path):
                return {}
            df = pd.read_csv(path)
            # Support both schemas — Binance Vision (`transact_time`) and the
            # REST endpoint shape (`T`). Both are unix-millisecond integers.
            if "T" in df.columns:
                ts_col = "T"
            elif "transact_time" in df.columns:
                ts_col = "transact_time"
            else:
                return {}
            df["minute"] = (df[ts_col].astype("int64") // 60000) * 60000
            return df.groupby("minute").size().astype(int).to_dict()
        except Exception as exc:
            try:
                logger.warning("FIX M-1: failed to preload aggTrades counts (%s)", exc)
            except Exception as _swallowed_exc:
                logger.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)
            return {}

    # ------------------------------------------------------------------
    # FIX-6: calibration normalization loader
    # ------------------------------------------------------------------
    def _load_calibration_norms(self) -> None:
        """Load feature_mean / feature_std written by calibrate_regime.py.
        Fail-closed: missing keys → leave norms as None and let
        _build_canonical_are_payload reject the bar."""
        try:
            w = np.load("weights/advanced_regime_weights.npz")
            if "feature_mean" in w.files and "feature_std" in w.files:
                fm = np.asarray(w["feature_mean"], dtype=float)
                fs = np.asarray(w["feature_std"], dtype=float)
                if fm.shape == (3,) and fs.shape == (3,) and np.all(np.isfinite(fm)) and np.all(np.isfinite(fs)) and np.all(fs > 0):
                    self._calibration_feature_mean = fm
                    self._calibration_feature_std = fs
                    logger.info(
                        "FIX-6: loaded calibration norms feature_mean=%s feature_std=%s",
                        np.round(fm, 6).tolist(), np.round(fs, 6).tolist(),
                    )
                else:
                    logger.error(
                        "FIX-6: invalid feature_mean/feature_std shape or values "
                        "(fm.shape=%s fs.shape=%s)", fm.shape, fs.shape,
                    )
            else:
                logger.error(
                    "FIX-6 NOT_APPLIED: weights .npz missing feature_mean/feature_std "
                    "— canonical ARE payload will be rejected. Re-run calibrate_regime.py."
                )
        except Exception as exc:
            logger.exception("FIX-6: failed to load calibration norms: %s", exc)
            self._calibration_feature_mean = None
            self._calibration_feature_std = None

    def _calibration_provenance_non_production(self) -> bool:
        """Return True when calibration sidecar marks weights non-production."""
        path = os.path.join("weights", "calibration_provenance.json")
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return bool(payload.get("production_valid") is False)
        except Exception as exc:
            logger.warning("failed to read calibration provenance sidecar (%s)", exc)
            return False

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
        """FIX CRITICAL-1 + FIX-6 (REGIME_ENGINE_AUDIT 2026-04-23): build the
        canonical 4-key payload that AdvancedRegimeEngine.update() requires
        and apply CALIBRATION-time normalization (not runtime running stats)
        to the feature vector. The features vector is exactly n_features=3
        (matches calibrate_regime.py / ARE constructor).

        Fail-closed: if calibration norms are missing, return a payload
        marked _invalid=True (zero feature vector) rather than silently using
        the wrong scale.
        """
        c = _safe_float(candle[4])
        v = _safe_float(candle[5])
        _ = (vol_mean, vol_std)
        log_ret = math.log(c / prev_close) if (prev_close > 0 and c > 0) else 0.0
        ofi_z_raw = _safe_float(features.get("ofi_zscore", features.get("ofi_norm", 0.0)))
        # Use the un-normalized replay feature vector here. Calibration-time
        # feature_mean/feature_std below are the single source of normalization;
        # applying a rolling volume z-score first double-normalizes replay bars.
        vol_z_raw = v
        raw = np.array([float(log_ret), float(ofi_z_raw), float(vol_z_raw)], dtype=float)

        fm = self._calibration_feature_mean
        fs = self._calibration_feature_std
        if fm is not None and fs is not None:
            normalized = (raw - fm) / np.where(fs > 0, fs, 1.0)
            return {
                "return": float(log_ret),
                "features": normalized.astype(float, copy=False),
                "price": float(c),
                "timestamp": _ts_seconds(candle),
            }
        logger.error(
            "FIX-6: calibration feature_mean/feature_std missing — refusing payload"
        )
        return {
            "return": 0.0,
            "features": np.zeros(3, dtype=float),
            "price": float(c),
            "timestamp": _ts_seconds(candle),
            "_invalid": True,
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

        # FIX M-1 APPLIED — replace the synthetic len(trades) volume proxy
        # with the real per-minute aggTrades count. Fall back to len(trades)
        # only if the bar has no minute bucket in the preloaded map. Track hit
        # quality so synthetic/non-aligned OHLCV cannot masquerade as LSA parity.
        trades_count_from_real = False
        try:
            bar_ts_ms = int(_safe_float(candle[0]))
            bar_minute = (bar_ts_ms // 60000) * 60000
            map_count = int(self._agg_trades_counts.get(bar_minute, 0))
            self._lsa_trade_count_bars = getattr(self, "_lsa_trade_count_bars", 0) + 1
            if map_count > 0:
                self._lsa_trade_count_real_bars = getattr(self, "_lsa_trade_count_real_bars", 0) + 1
                trades_count = map_count
                trades_count_from_real = True
            else:
                self._lsa_trade_count_zero_map_bars = getattr(self, "_lsa_trade_count_zero_map_bars", 0) + 1
                trades_count = len(trades)
            zero_ratio = (
                getattr(self, "_lsa_trade_count_zero_map_bars", 0)
                / max(getattr(self, "_lsa_trade_count_bars", 0), 1)
            )
            if (
                not getattr(self, "_lsa_trade_count_warning_logged", False)
                and getattr(self, "_lsa_trade_count_bars", 0) >= 10
                and zero_ratio > 0.80
            ):
                logger.warning(
                    "[BACKTEST] aggTrades count map not aligned with replay bars: "
                    "%.1f%% of checked bars had no real count; using synthetic len(trades) fallback",
                    zero_ratio * 100.0,
                )
                self._lsa_trade_count_warning_logged = True
        except Exception:
            self._lsa_trade_count_bars = getattr(self, "_lsa_trade_count_bars", 0) + 1
            self._lsa_trade_count_zero_map_bars = getattr(self, "_lsa_trade_count_zero_map_bars", 0) + 1
            trades_count = len(trades)

        market_data: Dict[str, Any] = {
            "price": c,
            "close_price": c,
            "atr": max(1e-8, h - l),
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "prev_book": _snapshot_to_book(prev_snapshot),
            "curr_book": _snapshot_to_book(snapshot),
            "timestamp": _ts_seconds(candle),
            "trades_count": trades_count,
            "trades_count_from_real": trades_count_from_real,
        }

        # FIX H-2 APPLIED — propagate the LSA-tracked liquidity pools into the
        # macro_liquidity payload so predict_sweep() can score the macro side
        # instead of falling back to a 0.5/0.5 prior on every bar.
        try:
            if self.lsa is not None and getattr(self.lsa, "liquidity_pools", None) is not None:
                pools = self.lsa.liquidity_pools
                macro_liquidity = {
                    "high_pool": pools.get("high"),
                    "low_pool": pools.get("low"),
                    "pool_count": len([
                        v for v in pools.values() if v is not None
                    ]),
                }
                market_data["macro_liquidity"] = macro_liquidity
        except Exception as _swallowed_exc:
            # Observability/integration must never break the trading path.
            logger.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)

        return market_data

    def _build_magnet_prediction(
        self,
        *,
        features: Dict[str, Any],
        lsa_market_data: Dict[str, Any],
        current_price: float,
        ts_seconds: float,
        regime_label: str,
        atr: float,
    ) -> Tuple[Dict[str, Any], bool]:
        """Build replay magnet output and report whether parity inputs exist.

        Fix issue/root cause/proof: production validation silently excluded the
        magnet because live liquidity-zone inputs were not guaranteed in replay.
        This function includes the alpha on the path and returns False when the
        replay lacks production-origin liquidity_zones, causing an explicit
        NON-PRODUCTION-VALID label rather than a false parity claim.
        """
        candidates = features.get("liquidity_zones")
        parity_inputs = isinstance(candidates, list) and len(candidates) > 0
        if not parity_inputs:
            macro = lsa_market_data.get("macro_liquidity", {}) if isinstance(lsa_market_data, dict) else {}
            candidates = []
            high_pool = _safe_float(macro.get("high_pool"), 0.0) if isinstance(macro, dict) else 0.0
            low_pool = _safe_float(macro.get("low_pool"), 0.0) if isinstance(macro, dict) else 0.0
            if high_pool > 0.0:
                candidates.append({"price": high_pool, "side": "above", "type": "replay_high_pool", "age_bars": 0.0, "base_strength": 1.0})
            if low_pool > 0.0:
                candidates.append({"price": low_pool, "side": "below", "type": "replay_low_pool", "age_bars": 0.0, "base_strength": 1.0})

        if self.magnet_predictor is None and create_backtest_magnet_predictor is not None:
            self.magnet_predictor = create_backtest_magnet_predictor()
        if self.magnet_predictor is None:
            return {"zone_side": "none", "confidence": 0.0, "sweep_likelihood_estimate": 0.0}, False
        try:
            return self.magnet_predictor.predict(
                candidates=candidates if isinstance(candidates, list) else [],
                current_price=current_price,
                current_time=ts_seconds,
                market_state={
                    "regime": regime_label,
                    "volatility": _safe_float(features.get("volatility", features.get("expected_volatility", features.get("atr_pct", 0.0))), 0.0),
                    "trend_direction": "up" if _safe_float(features.get("imbalance", 0.0), 0.0) >= 0.0 else "down",
                    "atr": max(_safe_float(atr, 0.0), 1e-8),
                },
                stop_hunt_data={"probability": _safe_float(features.get("stop_hunt_probability", 0.5), 0.5)},
                volume_intel={"liquidity_score": _safe_float(features.get("liquidity_score", 1.0), 1.0)},
            ), parity_inputs
        except Exception as exc:
            logger.debug("LiquidityMagnetPredictor replay predict failed: %s", exc)
            return {"zone_side": "none", "confidence": 0.0, "sweep_likelihood_estimate": 0.0}, False

    def _build_alpha_signals(
        self,
        signal_engine_out: Dict[str, Any],
        lsa_out: Dict[str, Any],
        ts_seconds: float,
        magnet_out: Optional[Dict[str, Any]] = None,
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

        # Source 3: LiquidityMagnetPredictor. Regression protection for the
        # backtest-inclusion finding: the alpha is always represented on the
        # orchestrator path. If inputs are unavailable, the predictor emits a
        # neutral signal and run_backtest labels the result NON-PRODUCTION-VALID.
        magnet_out = magnet_out if isinstance(magnet_out, dict) else {}
        zone_side = str(magnet_out.get("zone_side", "none")).lower()
        magnet_dir = 1 if zone_side == "above" else (-1 if zone_side == "below" else 0)
        magnet_raw_conf = _safe_float(magnet_out.get("confidence", 0.0), 0.0)
        magnet_conv = _to_continuous_conviction(magnet_raw_conf)
        try:
            out.append(AlphaSignal(
                source_id="liquidity_magnet_alpha",
                direction=int(magnet_dir),
                conviction=float(magnet_conv),
                expected_edge_bps=float(magnet_conv * 25.0),
                timestamp=float(ts_seconds),
            ))
        except Exception as exc:
            logger.debug("liquidity_magnet_alpha AlphaSignal build failed: %s", exc)

        return out

    # ------------------------------------------------------------------
    # AUDIT FIX ISSUE-C: L2 timestamp alignment validation
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_l2_timestamp_alignment(
        l2_csv_path: str,
        expected_start_ms: int,
        expected_end_ms: int,
    ) -> Dict[str, Any]:
        """Validate that an L2 depth CSV covers the expected replay window.

        Returns a dict with keys:
          valid (bool)      — True only when the first row falls inside the expected range
          first_ts_ms (int) — first timestamp found in the file (or -1 on error)
          reason (str)      — human-readable explanation
          label (str)       — 'PRODUCTION-VALID' or 'NON-PRODUCTION-VALID: ...'
        """
        result: Dict[str, Any] = {
            "valid": False,
            "first_ts_ms": -1,
            "reason": "",
            "label": "",
        }
        try:
            import csv as _csv
            with open(l2_csv_path, "r", encoding="utf-8") as fh:
                reader = _csv.reader(fh)
                header = next(reader, None)
                if header is None:
                    result["reason"] = f"L2 file '{l2_csv_path}' is empty."
                    result["label"] = "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched"
                    logger.warning("[BACKTEST][L2_VALIDATE] %s", result["reason"])
                    return result
                first_row = next(reader, None)
                if first_row is None:
                    result["reason"] = f"L2 file '{l2_csv_path}' has header but no data rows."
                    result["label"] = "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched"
                    logger.warning("[BACKTEST][L2_VALIDATE] %s", result["reason"])
                    return result
                # Try the first column as timestamp (ms)
                try:
                    first_ts = int(float(first_row[0]))
                except (ValueError, IndexError):
                    result["reason"] = (
                        f"L2 file '{l2_csv_path}' first-row timestamp not parseable: {first_row[:2]}"
                    )
                    result["label"] = "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched"
                    logger.warning("[BACKTEST][L2_VALIDATE] %s", result["reason"])
                    return result
                result["first_ts_ms"] = first_ts
                if not (expected_start_ms <= first_ts <= expected_end_ms):
                    result["reason"] = (
                        f"L2 file '{l2_csv_path}' timestamp {first_ts} is OUTSIDE "
                        f"the expected window [{expected_start_ms}, {expected_end_ms}]. "
                        "This is a date-mismatched file — OFI features will be synthetic."
                    )
                    result["label"] = "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched"
                    logger.warning("[BACKTEST][L2_VALIDATE] %s", result["reason"])
                    return result
                result["valid"] = True
                result["reason"] = (
                    f"L2 file '{l2_csv_path}' first_ts_ms={first_ts} is within window."
                )
                result["label"] = "PRODUCTION-VALID"
                logger.info("[BACKTEST][L2_VALIDATE] %s", result["reason"])
        except FileNotFoundError:
            result["reason"] = f"L2 file '{l2_csv_path}' not found — OFI features will be synthetic."
            result["label"] = "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched"
            logger.warning("[BACKTEST][L2_VALIDATE] %s", result["reason"])
        except Exception as exc:
            result["reason"] = f"L2 file '{l2_csv_path}' validation error: {exc}"
            result["label"] = "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched"
            logger.warning("[BACKTEST][L2_VALIDATE] %s", result["reason"])
        return result


    def _run_calibration_pass(
        self,
        ohlcv_data: List[list],
        ema_fast: float,
        ema_slow: float,
    ) -> dict:
        data = [row for row in (ohlcv_data or []) if isinstance(row, (list, tuple)) and len(row) >= 6]
        if self.lsa is None or len(data) < 2:
            return {"fitted": False, "n_samples": 0, "brier_score": float("nan")}
        split_idx = int(len(data) * 0.6)
        split_idx = max(26, min(split_idx, len(data) - 1))
        raw_probs: List[float] = []
        realized_labels: List[int] = []
        prev_snapshot: Dict[str, Any] = _simulate_snapshot_from_candle(data[0])
        ema_fast_alpha = 2.0 / (12.0 + 1.0)
        ema_slow_alpha = 2.0 / (26.0 + 1.0)
        ef = float(ema_fast)
        es = float(ema_slow)

        for i in range(25, split_idx):
            candle = data[i]
            prev_candle = data[i - 1]
            current_price = _safe_float(candle[4])
            prev_close = _safe_float(prev_candle[4]) if prev_candle else current_price
            ef = (1 - ema_fast_alpha) * ef + ema_fast_alpha * current_price
            es = (1 - ema_slow_alpha) * es + ema_slow_alpha * current_price
            snapshot = _simulate_snapshot_from_candle(candle, prev_close)
            trades = _simulate_trades_from_candle(candle)
            lsa_md = self._build_lsa_market_data(
                candle=candle, snapshot=snapshot, prev_snapshot=prev_snapshot,
                trades=trades, features={}, ema_fast=ef, ema_slow=es,
            )
            prev_snapshot = snapshot
            try:
                lsa_out = self.lsa.predict(lsa_md, regime_context={"regime": "UNKNOWN"}) or {}
                raw_prob = _safe_float(lsa_out.get("micro_prob", 0.5), 0.5)
                label = 1 if _safe_float(data[i + 1][4]) > _safe_float(data[i][4]) else 0
                raw_probs.append(raw_prob)
                realized_labels.append(label)
            except Exception as exc:
                logger.debug("calibration pass predict failed at i=%d: %s", i, exc)
        pairs = list(zip(raw_probs, realized_labels))
        if pairs:
            self.lsa.calibrate(pairs)
        status = self.lsa.get_calibration_status()
        logger.info("[CALIBRATION] fitted=%s n_samples=%d brier=%.4f", status.get("fitted"), int(status.get("n_samples", 0)), float(status.get("brier_score", float('nan'))))
        return status

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------
    def run_backtest(
        self,
        ohlcv_data: List[list],
        initial_balance: float | None = None,
        book_features: Optional[Sequence[Any]] = None,   # FIX-1
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
        book_features_1m: Optional[Sequence[Any]] = None,   # FIX-1
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

        # FIX-1: realign book to coarser resolutions when 1m book features
        # are provided. The 1m series may not line up directly with 5m/15m
        # bar closes, so we pick the latest 1m snapshot at-or-before each
        # coarser bar close (same semantics as align_book_to_bars).
        book_5m = self._realign_book(book_features_1m, bars_1m, bars_5m) if book_features_1m else None
        book_15m = self._realign_book(book_features_1m, bars_1m, bars_15m) if book_features_1m else None

        result_1m = self._run_single_pass(
            bars_1m, initial_balance=initial_balance, label="1m",
            book_features=book_features_1m,
        )
        result_5m = self._run_single_pass(
            bars_5m, initial_balance=initial_balance, label="5m",
            book_features=book_5m,
        )
        result_15m = self._run_single_pass(
            bars_15m, initial_balance=initial_balance, label="15m",
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
    # FIX-1: re-align 1m book features to a coarser bar set
    # ------------------------------------------------------------------
    @staticmethod
    def _realign_book(
        book_1m: Optional[Sequence[Any]],
        bars_1m: Sequence[Sequence],
        bars_target: Sequence[Sequence],
    ) -> Optional[List[Any]]:
        if not book_1m or not bars_1m or not bars_target:
            return None
        try:
            from data_tools.l2_to_backtest import align_book_to_bars
        except Exception:
            return None
        snaps = [s for s in book_1m if s is not None]
        if not snaps:
            return None
        return align_book_to_bars(bars_target, snaps)

    def run_walk_forward_validation(
        self,
        ohlcv_data: List[list],
        n_splits: int = 5,
        purge_bars: int = 10,
        min_train_bars: int = 100,
        min_test_bars: int = 50,
    ) -> Dict[str, Any]:
        data = [row for row in (ohlcv_data or []) if isinstance(row, (list, tuple)) and len(row) >= 6]
        fold_results: List[Dict[str, Any]] = []
        n = len(data)
        for k in range(max(0, int(n_splits))):
            train_end = int(n * (k + 1) / (n_splits + 1))
            test_start = train_end + int(purge_bars)
            test_end = int(n * (k + 2) / (n_splits + 1))
            if train_end < min_train_bars or (test_end - test_start) < min_test_bars:
                continue
            test_slice = data[test_start:test_end]
            if not test_slice:
                continue
            eng = BacktestEngine(config=BacktestConfig(legacy_mode=True))
            res = eng._run_single_pass(test_slice, label=f"wf_{k}")
            fold_results.append({
                "fold": k, "train_start": 0, "train_end": train_end,
                "test_start": test_start, "test_end": test_end,
                "sharpe": _safe_float(res.get("sharpe", 0.0)),
                "win_rate": _safe_float(res.get("win_rate", 0.0)),
                "max_drawdown": _safe_float(res.get("max_drawdown", 0.0)),
                "total_trades": int(res.get("total_trades", 0)),
            })
        if not fold_results:
            logger.warning("[WALK_FORWARD] insufficient data; no folds executed")
            return {"n_splits_requested": n_splits, "n_splits_executed": 0, "purge_bars": purge_bars, "fold_results": [], "mean_sharpe": 0.0, "std_sharpe": 0.0, "mean_win_rate": 0.0, "mean_max_drawdown": 0.0, "wf_label": "WALK_FORWARD_INSUFFICIENT_DATA"}
        sharps = [f["sharpe"] for f in fold_results]
        wins = [f["win_rate"] for f in fold_results]
        dds = [f["max_drawdown"] for f in fold_results]
        out = {"n_splits_requested": n_splits, "n_splits_executed": len(fold_results), "purge_bars": purge_bars, "fold_results": fold_results, "mean_sharpe": float(np.mean(sharps)), "std_sharpe": float(np.std(sharps)), "mean_win_rate": float(np.mean(wins)), "mean_max_drawdown": float(np.mean(dds)), "wf_label": "WALK_FORWARD_COMPLETE"}
        logger.info("[WALK_FORWARD] n_folds=%d mean_sharpe=%.4f std_sharpe=%.4f mean_win_rate=%.4f", len(fold_results), out["mean_sharpe"], out["std_sharpe"], out["mean_win_rate"])
        return out

    # ------------------------------------------------------------------
    # Internal: single-pass backtest
    # ------------------------------------------------------------------
    def _run_single_pass(
        self,
        ohlcv_data: List[list],
        initial_balance: float | None = None,
        label: str = "single",
        book_features: Optional[Sequence[Any]] = None,   # FIX-1
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
            "bars_skipped_signal_invalid": 0,
            "bars_skipped_execution_halted": 0,
            "real_trades_count_pct": 0.0,
        }

        if len(data) < 50:
            logger.info("[BACKTEST %s] insufficient bars (%d<50). cache hits=%d misses=%d",
                        label, len(data), cache_hits, cache_misses)
            return dict(empty_result)

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

        # FIX-1: real L1 book features (per-bar) — only honored when the
        # caller passes a sequence aligned 1-to-1 with the input bar set.
        # When provided, every bar with a non-None snapshot uses real
        # spread / imbalance / OFI z-score instead of the synthetic
        # _simulate_snapshot_from_candle values. Bars with None fall back
        # to the synthetic path (preserved for parity with prior behavior).
        use_real_book = (
            book_features is not None and len(book_features) == len(data)
        )
        if book_features is not None and not use_real_book:
            logger.warning(
                "[BACKTEST %s] book_features length %d != data length %d — "
                "ignoring and using synthetic snapshots",
                label, len(book_features), len(data),
            )
        if use_real_book:
            n_real = sum(1 for s in book_features if s is not None)
            logger.info(
                "[BACKTEST %s] FIX-1 using real book features: %d/%d bars",
                label, n_real, len(data),
            )

        # Feature-parquet runs supply per-bar L1 snapshots directly. Do not probe
        # legacy L2 depth CSV filenames on this path; BookTicker is L1-only and
        # the adapter must not depend on any true-L2 file existing.
        if use_real_book:
            _l2_validation: Dict[str, Any] = {
                "valid": True,
                "label": "FEATURE-PARQUET-L1-ONLY",
                "reason": "L2 depth validation skipped for supplied L1 feature parquet snapshots.",
            }
        else:
            # AUDIT FIX ISSUE-C: validate L2 depth file timestamp alignment.
            # Dec-2023 replay window in Binance epoch ms.
            _DEC2023_START_MS = 1_701_388_800_000
            _DEC2023_END_MS   = 1_704_067_199_000
            _l2_candidates = [
                "data/bookDepth.csv",
                "data/bookDepth_L2.csv",
                "data/bookDepth_clean.csv",
            ]
            _l2_validation = {
                "valid": False,
                "label": "NON-PRODUCTION-VALID: l2_data_missing_or_mismatched",
            }
            for _l2_path in _l2_candidates:
                _v = self._validate_l2_timestamp_alignment(
                    _l2_path, _DEC2023_START_MS, _DEC2023_END_MS
                )
                if _v["valid"]:
                    _l2_validation = _v
                    break
        _l2_valid = _l2_validation["valid"]

        # AUDIT FIX ISSUE-C+PART6: compute BACKTEST_LABEL once per run.
        _non_production_conditions: Dict[str, bool] = {
            "l2_data_missing_or_mismatched": not _l2_valid,
            "orchestration_bypassed": False,      # enforced by Issue D
            "regime_pipeline_uncalibrated": False, # weights loaded by __init__
            "magnet_inputs_unavailable_or_non_parity": False,
            "weights_trained_on_synthetic": self._calibration_provenance_non_production(),
            "synthetic_trade_counts": False,
        }
        _magnet_non_parity_seen = False
        # Backtest boundary: create clean run-local magnet state so zone memory
        # from prior replay/live contexts cannot influence this run. Live keeps
        # using engine.get_shared_magnet_predictor(); this isolated factory does
        # not touch the production singleton.
        self.magnet_predictor = create_backtest_magnet_predictor() if create_backtest_magnet_predictor is not None else None
        _backtest_label = (
            "PRODUCTION-VALID"
            if not any(_non_production_conditions.values())
            else "NON-PRODUCTION-VALID: " + ", ".join(
                k for k, v in _non_production_conditions.items() if v
            )
        )
        logger.info("[BACKTEST %s] %s", label, _backtest_label)

        # Volume normalization must be past/current-only. A full-sample mean/std
        # would leak future bars into production-valid backtests.
        rolling_vol_window = 50

        # Per-run alpha telemetry reset
        self._last_alpha_signals = []
        self._all_alpha_convictions = []

        # Per-run LSA aggTrades-count parity telemetry.
        self._lsa_trade_count_bars = 0
        self._lsa_trade_count_real_bars = 0
        self._lsa_trade_count_zero_map_bars = 0
        self._lsa_trade_count_warning_logged = False

        bars_skipped_signal_invalid = 0
        bars_skipped_execution_halted = 0

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

        if self.cfg.legacy_mode and self.lsa is not None and len(data) >= 100:
            try:
                cal_status = self._run_calibration_pass(data, ema_fast, ema_slow)
                logger.info("[BACKTEST %s] diagnostic calibration pass complete: %s", label, cal_status)
            except Exception as exc:
                logger.warning("[BACKTEST %s] diagnostic calibration pass failed: %s", label, exc)

        # Running snapshot of the previous order book (LSA OFI z-score input)
        prev_snapshot: Dict[str, Any] = _simulate_snapshot_from_candle(data[0])

        position: Optional[Dict[str, Any]] = None
        pending_order: Optional[Dict[str, Any]] = None
        _bars_with_insufficient_signals: int = 0

        for i in range(25, len(data)):
            window = data[: i + 1]
            candle = window[-1]
            prev_candle = window[-2]
            current_price = _safe_float(candle[4])
            prev_close = _safe_float(prev_candle[4]) if prev_candle else current_price

            # Update EMAs (used by LSA regime detection)
            ema_fast = (1 - ema_fast_alpha) * ema_fast + ema_fast_alpha * current_price
            ema_slow = (1 - ema_slow_alpha) * ema_slow + ema_slow_alpha * current_price

            # FIX-1: build the per-bar snapshot from the real L1 book when
            # provided. Caching is bypassed on real-book bars because two
            # bars with identical (ts, close) can still have different L1
            # snapshots; we never want a stale cached synthetic snapshot to
            # override a fresh real one. Trades remain synthetic for now —
            # FeatureEngine still gets a full snapshot+trades pair.
            real_snap = book_features[i] if use_real_book else None
            if real_snap is not None:
                cache_misses += 1
                snapshot = {
                    "timestamp": int(candle[0]),
                    "bids": [[float(real_snap.bid_price), float(real_snap.bid_qty)]],
                    "asks": [[float(real_snap.ask_price), float(real_snap.ask_qty)]],
                }
                trades = _simulate_trades_from_candle(candle)
                features_outer = self.feature_engine.update(snapshot, trades)
                # Override the synthesized OFI/imbalance/spread with real L1.
                feat_inner_seed = features_outer.get("features", features_outer)
                if isinstance(feat_inner_seed, dict):
                    feat_inner_seed["ofi_zscore"] = float(real_snap.ofi_z)
                    feat_inner_seed["ofi_norm"]   = float(getattr(real_snap, "ofi_norm", real_snap.imbalance))
                    feat_inner_seed["imbalance"]  = float(real_snap.imbalance)
                    feat_inner_seed["spread_bps"] = float(real_snap.spread_bps)
                    feat_inner_seed["bid_price"]  = float(real_snap.bid_price)
                    feat_inner_seed["ask_price"]  = float(real_snap.ask_price)
                    feat_inner_seed["bid_qty"]    = float(real_snap.bid_qty)
                    feat_inner_seed["ask_qty"]    = float(real_snap.ask_qty)
                    feat_inner_seed["liquidity_score"] = float(getattr(real_snap, "liquidity_score", feat_inner_seed.get("liquidity_score", 0.0)))
                    feat_inner_seed["fill_prob"] = float(getattr(real_snap, "fill_prob", feat_inner_seed.get("fill_prob", 0.5)))
                    feat_inner_seed["fill_probability"] = float(getattr(real_snap, "fill_prob", feat_inner_seed.get("fill_probability", 0.5)))
                    feat_inner_seed["impact_cost_bps"] = float(getattr(real_snap, "impact_cost_bps", feat_inner_seed.get("impact_cost_bps", 0.0)))
                    feat_inner_seed["vol_z"] = float(getattr(real_snap, "vol_z", feat_inner_seed.get("vol_z", 0.0)))
            else:
                cache_key = (int(candle[0]), float(current_price))
                cached = self._analysis_cache.get(cache_key)
                if cached is not None:
                    cache_hits += 1
                    snapshot = cached["snapshot"]
                    trades = cached["trades"]
                    features_outer = dict(cached["features"])
                else:
                    cache_misses += 1
                    snapshot = _simulate_snapshot_from_candle(candle, prev_close)
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
                # FIX-1: when real L1 already populated ofi_zscore (real
                # rolling-60 z-score from BookSnapshot.ofi_z), preserve it.
                # Otherwise fall back to the synthetic ofi_norm-based proxy.
                if "ofi_zscore" not in feat_inner:
                    feat_inner["ofi_zscore"] = feat_inner.get("ofi_norm", feat_inner.get("ofi", 0.0))
                feat_inner["flow_imbalance"]   = feat_inner.get("aggressor_imbalance", 0.0)
                feat_inner["hawkes_intensity"] = feat_inner.get("trade_burst", 0.0)
            features = feat_inner if isinstance(feat_inner, dict) else features_outer

            if self.fill_model is not None:
                features = self.fill_model.enrich(features)
            if self.tox_filter is not None:
                features = self.tox_filter.enrich(features)

            if position is not None:
                funding_pnl = calculate_funding_payment(
                    side=str(position.get("side", "")),
                    entry_price=_safe_float(position.get("entry"), 0.0),
                    size=_safe_float(position.get("size"), 0.0),
                    funding_rate=_extract_funding_rate(features),
                    bar_interval_hours=self.cfg.bar_interval_hours,
                    funding_interval_hours=self.cfg.funding_interval_hours,
                )
                if funding_pnl:
                    balance += funding_pnl
                    position["funding_pnl"] = _safe_float(position.get("funding_pnl", 0.0), 0.0) + funding_pnl
                    peak = max(peak, balance)
                    dd = (peak - balance) / peak if peak > 0 else 0.0
                    max_dd = max(max_dd, dd)

            ts_seconds = _ts_seconds(candle)

            # ---- FIX CRITICAL-1: AdvancedRegimeEngine via canonical payload --
            regime_label = "UNKNOWN"
            regime_conf = 0.5
            volatility_score = 0.0
            if self.are is not None:
                vol_window = data[max(0, i - rolling_vol_window + 1): i + 1]
                vols = np.array([_safe_float(r[5]) for r in vol_window], dtype=float)
                vol_mean = float(vols.mean()) if vols.size else 0.0
                vol_std = float(vols.std()) if vols.size else 1.0
                if vol_std <= 0.0:
                    vol_std = 1.0
                are_payload = self._build_canonical_are_payload(
                    candle=candle,
                    prev_close=prev_close,
                    features=features,
                    vol_mean=vol_mean,
                    vol_std=vol_std,
                )
                # FIX-6 strict fail-closed: never forward a payload built
                # without calibration norms — those features are on the
                # wrong scale and would drive emission probabilities.
                if are_payload.get("_invalid"):
                    continue
                try:
                    are_out = self.are.update(are_payload)
                    if isinstance(are_out, dict):
                        if are_out.get("signal_valid") is False:
                            bars_skipped_signal_invalid += 1
                            continue
                        execution_mode = str(are_out.get("execution_mode", "")).lower()
                        engine_status = str(are_out.get("engine_status", ""))
                        risk_metrics = are_out.get("risk_metrics", {})
                        feed_status = risk_metrics.get("feed_status", "") if isinstance(risk_metrics, dict) else ""
                        if isinstance(feed_status, (list, tuple, set)):
                            feed_status_text = " ".join(str(x) for x in feed_status)
                        else:
                            feed_status_text = str(feed_status)
                        if (
                            execution_mode in {"halt", "circuit_breaker", "fail_safe", "halt_igarch"}
                            or engine_status == "DEGRADED"
                            or "UNCALIBRATED" in feed_status_text.upper()
                        ):
                            bars_skipped_execution_halted += 1
                            continue
                        regime_label = str(are_out.get("regime_label", "UNKNOWN"))
                        regime_conf = _safe_float(are_out.get("confidence", 0.5), 0.5)
                        volatility_score = _safe_float(
                            risk_metrics.get("expected_volatility", 0.0) if isinstance(risk_metrics, dict) else 0.0
                        )
                except Exception as exc:
                    logger.debug("ARE.update failed at i=%d: %s", i, exc)

            # ---- FIX CRITICAL-5: LiquiditySweepAlpha (seeded) ----------------
            lsa_out: Dict[str, Any] = {"action": "HOLD", "confidence": 0.0}
            lsa_md: Dict[str, Any] = {}
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

            # ---- LiquidityMagnetPredictor (alpha source #3) ------------------
            magnet_out, magnet_parity_inputs = self._build_magnet_prediction(
                features=features,
                lsa_market_data=lsa_md,
                current_price=current_price,
                ts_seconds=ts_seconds,
                regime_label=regime_label,
                atr=max(1e-8, _safe_float(candle[2]) - _safe_float(candle[3])),
            )
            if not magnet_parity_inputs:
                _magnet_non_parity_seen = True

            # ---- SignalEngine (alpha source #1) -----------------------------
            signal_engine_out = self.signal_engine.generate(features)

            # ---- FIX CRITICAL-6+7: AlphaOrchestrator with ≥2 sources ---------
            alpha_signals = self._build_alpha_signals(signal_engine_out, lsa_out, ts_seconds, magnet_out)
            if not self.cfg.legacy_mode and len(alpha_signals) < 2:
                _bars_with_insufficient_signals += 1
            self._last_alpha_signals = list(alpha_signals)
            for s in alpha_signals:
                try:
                    self._all_alpha_convictions.append(float(s.conviction))
                except Exception as _swallowed_exc:
                    logger.debug("[SWALLOWED] %s suppressed: %s", __name__, _swallowed_exc)

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

            # AUDIT FIX ISSUE-D: orchestration guard.
            # In production-valid mode, orch_action_str MUST have come from
            # AlphaOrchestrator.  If the orchestrator didn't run (too few
            # alpha signals, dependency None), force HOLD — never let a
            # raw SignalEngine output bypass the orchestration layer.
            if not self.cfg.legacy_mode:
                _valid_orch_actions = {"LONG", "SHORT", "HOLD"}
                if orch_action_str not in _valid_orch_actions:
                    logger.warning(
                        "[BACKTEST %s][ISSUE-D] orch_action_str=%r invalid at i=%d — forcing HOLD",
                        label, orch_action_str, i,
                    )
                    orch_action_str = "HOLD"
                    orch_conviction = 0.0
                if (self.orchestrator is None
                        or len(self._last_alpha_signals) < 2):
                    if orch_action_str in ("LONG", "SHORT"):
                        logger.warning(
                            "[BACKTEST %s][ISSUE-D] orchestrator unavailable/insufficient sources at i=%d "
                            "— forcing HOLD to prevent signal_engine bypass",
                            label, i,
                        )
                        orch_action_str = "HOLD"
                        orch_conviction = 0.0

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
                "alpha_sources": ["signal_engine", "liquidity_sweep_alpha", "liquidity_magnet_alpha"],
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

            if pending_order is not None:
                pending_order["age_bars"] = int(pending_order.get("age_bars", 0)) + 1
                fill_qty, fill_fraction = simulate_queue_fill(
                    side=str(pending_order.get("side", "buy")),
                    remaining_qty=_safe_float(pending_order.get("remaining_size", 0.0), 0.0),
                    features=features,
                )
                if fill_qty > 0.0:
                    pending_order["remaining_size"] = max(0.0, _safe_float(pending_order.get("remaining_size", 0.0), 0.0) - fill_qty)
                    pending_order["filled_size"] = _safe_float(pending_order.get("filled_size", 0.0), 0.0) + fill_qty
                    if position is None:
                        position = {
                            **pending_order["position_template"],
                            "size": pending_order["filled_size"],
                            "entry": pending_order["entry"],
                            "queue_fill_fraction": fill_fraction,
                            "queue_remaining_size": pending_order["remaining_size"],
                        }

                        if self.trade_lifecycle is not None:
                            self.trade_lifecycle.on_entry(
                                side=position["side"],
                                entry_price=position["entry"], size=position["size"], features=features,
                            )
                        if self.position_manager is not None:
                            self.position_manager.on_entry(
                                symbol="BTC/USDT",
                                side=position["side"],
                                size=position["size"], entry_price=position["entry"], order_id=position["trade_id"],
                                sl=position["sl"],
                                tp=position["tp"],
                                signal=str(signal.get("signal", "HOLD")),
                                confidence=float(orch_conviction),
                                regime=regime_label, fees=0.0, fee_type="pct",
                                features=features,
                            )
                    else:
                        position["size"] = _safe_float(position.get("size", 0.0), 0.0) + fill_qty
                        position["queue_fill_fraction"] = fill_fraction
                        position["queue_remaining_size"] = pending_order["remaining_size"]
                    if pending_order["remaining_size"] <= 1e-12:
                        pending_order = None
                elif int(pending_order.get("age_bars", 0)) >= self.cfg.queue_fill_timeout_bars:
                    pending_order = None

            # ---- Position open ----
            if position is None and pending_order is None and orch_action_str in ("LONG", "SHORT") and decision.get("execute"):
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
                position_template = {
                    "trade_id": trade_id,
                    "side": "LONG" if side == "buy" else "SHORT",
                    "entry": entry,
                    "sl": _safe_float(decision.get("sl", 0.0)),
                    "tp": _safe_float(decision.get("tp", 0.0)),
                    "fees": fees,
                    "fee_type": fee_type,
                    "entry_index": i,
                    "entry_features": features,
                    "signal": signal,
                    "meta": meta,
                    "funding_pnl": 0.0,
                }
                fill_qty, fill_fraction = simulate_queue_fill(
                    side=side,
                    remaining_qty=size,
                    features=features,
                )
                if fill_qty <= 0.0:
                    pending_order = {
                        "side": side,
                        "entry": entry,
                        "remaining_size": size,
                        "filled_size": 0.0,
                        "age_bars": 0,
                        "position_template": position_template,
                    }
                    continue
                remaining_size = max(0.0, size - fill_qty)
                position = {
                    **position_template,
                    "size": fill_qty,
                    "queue_fill_fraction": fill_fraction,
                    "queue_remaining_size": remaining_size,
                }
                if self.trade_lifecycle is not None:
                    self.trade_lifecycle.on_entry(
                        side=position["side"],
                        entry_price=position["entry"], size=position["size"], features=features,
                    )
                if self.position_manager is not None:
                    self.position_manager.on_entry(
                        symbol="BTC/USDT",
                        side=position["side"],
                        size=position["size"], entry_price=position["entry"], order_id=position["trade_id"],
                        sl=position["sl"],
                        tp=position["tp"],
                        signal=str(signal.get("signal", "HOLD")),
                        confidence=float(orch_conviction),
                        regime=regime_label, fees=0.0, fee_type="pct",
                        features=features,
                    )
                if remaining_size > 1e-12:
                    pending_order = {
                        "side": side,
                        "entry": entry,
                        "remaining_size": remaining_size,
                        "filled_size": fill_qty,
                        "age_bars": 0,
                        "position_template": position_template,
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
                fees = _safe_float(position.get("fees"), 0.0)
                slippage = self.cfg.slippage_bps / 10_000.0
                pnl, net_pnl_pct = calculate_trade_pnl(
                    side=side,
                    entry_price=entry,
                    exit_price=current_price,
                    size=_safe_float(position.get("size"), 0.0),
                    fee_pct=fees,
                    slippage_pct=slippage,
                    funding_pnl=0.0,
                )
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                returns.append(net_pnl_pct)
                # FIX-7 (REGIME_ENGINE_AUDIT 2026-04-23): notify the engine on
                # every realized trade close so portfolio-DD is tracked and
                # the circuit breaker can trip when DD >= _MAX_PORTFOLIO_DRAWDOWN.
                if self.are is not None and hasattr(self.are, "report_realized_pnl"):
                    try:
                        self.are.report_realized_pnl(
                            realized_pnl=float(pnl), equity=float(balance),
                        )
                    except Exception as exc:
                        logger.debug("ARE.report_realized_pnl failed: %s", exc)
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
                    "funding_pnl": round(_safe_float(position.get("funding_pnl", 0.0), 0.0), 6),
                    "queue_fill_fraction": round(_safe_float(position.get("queue_fill_fraction", 1.0), 1.0), 6),
                    "queue_remaining_size": round(_safe_float(position.get("queue_remaining_size", 0.0), 0.0), 8),
                    "signal": position["signal"],
                    "meta": position["meta"],
                })
                position = None
                pending_order = None

        total_trades = len(trade_log)
        wins = sum(1 for t in trade_log if _safe_float(t.get("pnl", 0.0)) > 0)
        win_rate = (wins / total_trades) if total_trades else 0.0
        gross_wins = [t["pnl"] for t in trade_log if _safe_float(t.get("pnl", 0.0)) > 0]
        gross_losses = [abs(_safe_float(t.get("pnl", 0.0))) for t in trade_log if _safe_float(t.get("pnl", 0.0)) <= 0]
        avg_win = sum(gross_wins) / len(gross_wins) if gross_wins else 0.0
        avg_loss = sum(gross_losses) / len(gross_losses) if gross_losses else 0.0
        expectancy = (avg_win * win_rate) - (avg_loss * (1.0 - win_rate))

        _orch_degraded_fraction = (_bars_with_insufficient_signals / max(len(data) - 25, 1))
        _non_production_conditions["orchestration_degraded"] = (not self.cfg.legacy_mode and _orch_degraded_fraction > 0.05)
        _calibration_fitted = (
            self.lsa is not None
            and getattr(getattr(self.lsa, "_calibrator", None), "fitted", False)
        )
        _non_production_conditions["calibrator_not_fitted"] = not _calibration_fitted
        _non_production_conditions["magnet_inputs_unavailable_or_non_parity"] = bool(_magnet_non_parity_seen)
        real_trades_count_pct = (
            getattr(self, "_lsa_trade_count_real_bars", 0)
            / max(getattr(self, "_lsa_trade_count_bars", 0), 1)
        )
        if real_trades_count_pct < 0.1:
            _non_production_conditions["synthetic_trade_counts"] = True
        _backtest_label = (
            "PRODUCTION-VALID"
            if not any(_non_production_conditions.values())
            else "NON-PRODUCTION-VALID: " + ", ".join(
                k for k, v in _non_production_conditions.items() if v
            )
        )

        logger.info("[BACKTEST %s] cache hits=%d misses=%d trades=%d", label, cache_hits, cache_misses, total_trades)
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 6),
            "pnl": round(balance - (initial_balance if initial_balance is not None else self.cfg.initial_balance), 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(_compute_sharpe(returns), 6),
            "expectancy": round(expectancy, 6),
            "trade_log": trade_log,
            "backtest_label": _backtest_label,                  # AUDIT FIX ISSUE-C / PART-6
            "non_production_conditions": _non_production_conditions,  # AUDIT FIX ISSUE-C / PART-6
            "orch_degraded_fraction": round(_orch_degraded_fraction, 4),
            "bars_skipped_signal_invalid": int(bars_skipped_signal_invalid),
            "bars_skipped_execution_halted": int(bars_skipped_execution_halted),
            "real_trades_count_pct": round(float(real_trades_count_pct), 6),
        }
