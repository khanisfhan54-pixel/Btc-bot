# backtest_engine.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from venue_basis import VenueBasisNormalizer

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
LiquiditySweepAlpha = None  # type: ignore
AlphaOrchestrator = None  # type: ignore
OrchestratorConfig = None  # type: ignore
AlphaSignal = None  # type: ignore
RegimeContext = None  # type: ignore
FeatureQuality = None  # type: ignore
ExecutionState = None  # type: ignore
AdvancedRegimeEngine = None  # type: ignore
for _mod in (
    ("feature_engine", "FeatureEngine"),
    ("signal_engine", "SignalEngine"),
    ("execution", "ExecutionLogic"),
    ("meta_filter", "MetaFilter"),
    ("learning_engine", "LEARNING_ENGINE"),
    ("queue_fill_model", "QueueFillModel"),
    ("toxicity_filter", "ToxicityFilter"),
    ("position_manager", "PositionManager"),
    ("trade_lifecycle_manager", "TradeLifecycleManager"),
    ("capital_allocator", "CapitalAllocator"),
    ("alpha_liquidity_sweep_predictor", "LiquiditySweepAlpha"),
    ("alpha_orchestrator", "AlphaOrchestrator"),
    ("alpha_orchestrator", "OrchestratorConfig"),
    ("alpha_orchestrator", "AlphaSignal"),
    ("alpha_orchestrator", "RegimeContext"),
    ("alpha_orchestrator", "FeatureQuality"),
    ("alpha_orchestrator", "ExecutionState"),
    ("advanced_regime_engine", "AdvancedRegimeEngine"),
):
    try:
        _m = __import__(_mod[0], fromlist=[_mod[1]])
        globals()[_mod[1]] = getattr(_m, _mod[1])
    except Exception as _be_import_err:
        logging.getLogger(__name__).warning("backtest_engine: optional import failed %s.%s (%s)", _mod[0], _mod[1], _be_import_err)

logger = logging.getLogger(__name__)


class _FallbackFeatureEngine:
    def update(self, snapshot: Dict[str, Any], trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"features": {"latency_ms": 0.0, "liquidity_score": 1.0, "spread_bps": 1.0}, "snapshot": snapshot, "trade_count": len(trades)}


class _FallbackSignalEngine:
    def generate(self, features: Dict[str, Any]) -> Dict[str, Any]:
        return {"signal": "HOLD", "confidence": 0.0}


class _FallbackExecutionLogic:
    def decide(self, signal_payload: Dict[str, Any], features_payload: Dict[str, Any], snapshot: Dict[str, Any], account_equity: float, meta_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"execute": False, "position_size": 0.0, "sl": 0.0, "tp": 0.0, "reason": "fallback"}


class _FallbackMetaFilter:
    def evaluate(self, **kwargs: Any) -> Dict[str, Any]:
        return {"allow_trade": True}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
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


@dataclass
class BacktestConfig:
    fee_bps: float = 8.0
    slippage_bps: float = 3.0
    max_hold_bars: int = 12
    initial_balance: float = 10_000.0
    basis_mode: str = "none"  # none|fixed
    fixed_basis: float = 0.0


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None, learning_engine: Any = None, signal_only: bool = True) -> None:
        self.cfg = config or BacktestConfig()
        self.signal_only = bool(signal_only)
        self.learning_engine = learning_engine if learning_engine is not None else LEARNING_ENGINE
        self.feature_engine = FeatureEngine() if FeatureEngine is not None else _FallbackFeatureEngine()
        self.signal_engine = SignalEngine() if SignalEngine is not None else _FallbackSignalEngine()
        self.execution_logic = None if self.signal_only else (ExecutionLogic() if ExecutionLogic is not None else _FallbackExecutionLogic())
        self.meta_filter = MetaFilter() if MetaFilter is not None else _FallbackMetaFilter()
        self.fill_model = QueueFillModel() if QueueFillModel is not None else None
        self.tox_filter = ToxicityFilter() if ToxicityFilter is not None else None
        self.position_manager = PositionManager() if PositionManager is not None else None
        self.trade_lifecycle = TradeLifecycleManager() if TradeLifecycleManager is not None else None
        self.capital_allocator = CapitalAllocator() if CapitalAllocator is not None else None
        self.alpha_predictor = LiquiditySweepAlpha() if LiquiditySweepAlpha is not None else None
        self.alpha_orchestrator = (AlphaOrchestrator(OrchestratorConfig(signal_weights={"alpha_model": 1.0})) if (AlphaOrchestrator is not None and OrchestratorConfig is not None) else None)
        try:
            self.regime_engine = AdvancedRegimeEngine(enable_background_workers=False) if AdvancedRegimeEngine is not None else None
        except Exception:
            self.regime_engine = None
        self.basis = VenueBasisNormalizer(halt_threshold_pct=0.5)
        self.basis.set_venues("backtest", "backtest")
        self._analysis_cache: Dict[Tuple[int, float], Dict[str, Any]] = {}

    def run_backtest(
        self,
        ohlcv_data: List[list],
        initial_balance: float | None = None,
        *,
        signal_quality_required: bool = True,
        allow_ohlcv_synthetic: bool = False,
    ) -> Dict[str, Any]:
        cache_hits = 0
        cache_misses = 0
        data = [row for row in (ohlcv_data or []) if isinstance(row, (list, tuple)) and len(row) >= 6]
        has_microstructure_rows = any(
            isinstance(row, dict) and isinstance(row.get("snapshot"), dict) and "bids" in row.get("snapshot", {}) and "asks" in row.get("snapshot", {})
            for row in (ohlcv_data or [])
        )
        if signal_quality_required and (not has_microstructure_rows):
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "expectancy": 0.0,
                "trade_log": [],
                "signal_only_mode": self.signal_only,
                "signal_coverage": 0.0,
                "long_signals": 0,
                "short_signals": 0,
                "hold_signals": 0,
                "avg_return_per_trade": 0.0,
                "avg_holding_bars": 0.0,
                "alpha_non_empty_count": 0,
                "regime_state": "explicit_fallback",
                "signal_quality_valid": False,
                "signal_quality_reason": "production_parity_requires_regime_engine",
                "production_valid": False,
            }
        if signal_quality_required and has_microstructure_rows and self.alpha_orchestrator is None:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "expectancy": 0.0,
                "trade_log": [],
                "signal_only_mode": self.signal_only,
                "signal_coverage": 0.0,
                "long_signals": 0,
                "short_signals": 0,
                "hold_signals": 0,
                "avg_return_per_trade": 0.0,
                "avg_holding_bars": 0.0,
                "alpha_non_empty_count": 0,
                "regime_state": "explicit_fallback",
                "signal_quality_valid": False,
                "signal_quality_reason": "production_parity_requires_regime_engine",
                "production_valid": False,
            }

        if len(data) < 50:
            logger.info("[BACKTEST CACHE] hits=%d misses=%d", cache_hits, cache_misses)
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "expectancy": 0.0,
                "trade_log": [],
                "signal_quality_valid": False,
                "signal_quality_reason": "insufficient_data",
                "production_valid": False,
            }

        balance = float(initial_balance if initial_balance is not None else self.cfg.initial_balance)
        peak = balance
        max_dd = 0.0
        returns: List[float] = []
        trade_log: List[Dict[str, Any]] = []

        position: Optional[Dict[str, Any]] = None
        signal_counts: Dict[str, int] = {"LONG": 0, "SHORT": 0, "HOLD": 0}
        non_hold_signals = 0
        bars_processed = 0
        alpha_non_empty_count = 0
        synthetic_microstructure = True
        for i in range(25, len(data)):
            window = data[: i + 1]
            candle = window[-1]
            current_price = _safe_float(candle[4])

            cache_key = (int(candle[0]), float(current_price))
            cached = self._analysis_cache.get(cache_key)
            if cached is not None:
                cache_hits += 1
                snapshot = cached["snapshot"]
                trades = cached["trades"]
                features = dict(cached["features"])
            else:
                cache_misses += 1
                snapshot = _simulate_snapshot_from_candle(candle, window[-2][4] if len(window) > 1 else None)
                trades = _simulate_trades_from_candle(candle)
                features = self.feature_engine.update(snapshot, trades)
                self._analysis_cache[cache_key] = {"snapshot": snapshot, "trades": trades, "features": dict(features)}
            bars_processed += 1

            if self.fill_model is not None:
                features = self.fill_model.enrich(features)
            if self.tox_filter is not None:
                features = self.tox_filter.enrich(features)
            alpha = {}
            if self.alpha_predictor is not None:
                alpha_raw = self.alpha_predictor.predict({"features": features}) or {}
            else:
                alpha_raw = {}

            if signal_quality_required:
                if self.regime_engine is None:
                    return {
                        "total_trades": 0, "win_rate": 0.0, "pnl": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "expectancy": 0.0,
                        "trade_log": [], "signal_only_mode": self.signal_only, "signal_coverage": 0.0, "long_signals": 0,
                        "short_signals": 0, "hold_signals": 0, "avg_return_per_trade": 0.0, "avg_holding_bars": 0.0,
                        "alpha_non_empty_count": 0, "regime_state": "explicit_fallback", "signal_quality_valid": False,
                        "signal_quality_reason": "production_parity_requires_regime_engine",
                    }
                if not (self.alpha_orchestrator is not None and AlphaSignal is not None and RegimeContext is not None and FeatureQuality is not None and ExecutionState is not None):
                    return {
                        "total_trades": 0, "win_rate": 0.0, "pnl": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "expectancy": 0.0,
                        "trade_log": [], "signal_only_mode": self.signal_only, "signal_coverage": 0.0, "long_signals": 0,
                        "short_signals": 0, "hold_signals": 0, "avg_return_per_trade": 0.0, "avg_holding_bars": 0.0,
                        "alpha_non_empty_count": 0, "regime_state": "explicit_fallback", "signal_quality_valid": False,
                        "signal_quality_reason": "production_parity_requires_regime_engine",
                    }
                prev_close = _safe_float(window[-2][4]) if len(window) > 1 else _safe_float(candle[1])
                cur_close = _safe_float(candle[4])
                log_ret = 0.0 if prev_close <= 0.0 or cur_close <= 0.0 else math.log(cur_close / prev_close)
                regime_features = [
                    _safe_float(features.get("imbalance", 0.0)) if isinstance(features, dict) else 0.0,
                    _safe_float(features.get("bid_vol", 0.0)) if isinstance(features, dict) else 0.0,
                    _safe_float(features.get("trade_count", len(trades))) if isinstance(features, dict) else float(len(trades)),
                ]
                reg_out = self.regime_engine.update({
                    "return": log_ret,
                    "features": regime_features,
                    "price": current_price,
                    "volume": _safe_float(candle[5]),
                    "orderbook": snapshot,
                    "trades": trades,
                    "require_calibration": True,
                    "require_microstructure": True,
                }) or {}
                if not bool(reg_out.get("signal_valid", True)):
                    return {
                        "total_trades": 0, "win_rate": 0.0, "pnl": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "expectancy": 0.0,
                        "trade_log": [], "signal_only_mode": self.signal_only, "signal_coverage": 0.0, "long_signals": 0,
                        "short_signals": 0, "hold_signals": 0, "avg_return_per_trade": 0.0, "avg_holding_bars": 0.0,
                        "alpha_non_empty_count": 0, "regime_state": "explicit_fallback", "signal_quality_valid": False,
                        "signal_quality_reason": "production_parity_requires_regime_engine",
                    }
                regime_context = reg_out
                if not isinstance(alpha_raw, dict):
                    continue
                current_time = float(snapshot.get("timestamp", i) or i)
                src = alpha_raw.get("source_id")
                direction_raw = alpha_raw.get("direction")
                conviction_raw = alpha_raw.get("confidence", alpha_raw.get("conviction"))
                edge_raw = alpha_raw.get("expected_edge_bps", alpha_raw.get("edge_bps", alpha_raw.get("edge", 0.0)))
                if src is None or direction_raw is None or conviction_raw is None:
                    continue
                direction_val = 1 if str(direction_raw).upper() in ("LONG", "BUY", "1") else (-1 if str(direction_raw).upper() in ("SHORT", "SELL", "-1") else 0)
                conviction_val = _clamp(_safe_float(conviction_raw, 0.0), 0.0, 1.0)
                edge_val = abs(_safe_float(edge_raw, 0.0))
                signal_obj = AlphaSignal(
                    source_id=str(src),
                    direction=direction_val,
                    conviction=conviction_val,
                    expected_edge_bps=edge_val,
                    timestamp=max(current_time, 1.0),
                )
                rc = RegimeContext(
                    regime_name=str(regime_context.get("regime_label", "unknown")),
                    volatility_score=_clamp(_safe_float(regime_context.get("confidence", 0.5), 0.5), 0.0, 1.0),
                    liquidity_score=_clamp(_safe_float(regime_context.get("risk_level", 0.5), 0.5), 0.0, 1.0),
                )
                fq_payload = features.get("quality", {}) if isinstance(features, dict) else {}
                if not isinstance(fq_payload, dict):
                    fq_payload = {}
                fq = FeatureQuality(
                    staleness_ratio=_clamp(_safe_float(fq_payload.get("staleness_ratio", 0.0), 0.0), 0.0, 1.0),
                    missing_data_ratio=_clamp(_safe_float(fq_payload.get("missing_data_ratio", 0.0), 0.0), 0.0, 1.0),
                )
                orch_action = self.alpha_orchestrator.orchestrate(
                    signals=[signal_obj],
                    regime_context=rc,
                    feature_quality=fq,
                    execution_state=ExecutionState(current_exposure_usd=0.0, max_exposure_usd=max(balance, 0.0), current_drawdown_pct=0.0),
                    current_time=max(current_time, 1.0),
                )
                if orch_action.action.name == "BUY":
                    alpha = {"direction": "LONG", "confidence": float(orch_action.net_conviction), "prob_above": 1.0, "prob_below": 0.0}
                elif orch_action.action.name == "SELL":
                    alpha = {"direction": "SHORT", "confidence": float(orch_action.net_conviction), "prob_above": 0.0, "prob_below": 1.0}
                else:
                    alpha = {"direction": "NEUTRAL", "confidence": float(orch_action.net_conviction), "prob_above": 0.5, "prob_below": 0.5}
                alpha_non_empty_count += 1
            if isinstance(features, dict):
                features["alpha"] = alpha
            signal = self.signal_engine.generate(features)
            sig_name = str(signal.get("signal", "HOLD")).upper()
            if "LONG" in sig_name:
                signal_counts["LONG"] += 1
                non_hold_signals += 1
            elif "SHORT" in sig_name:
                signal_counts["SHORT"] += 1
                non_hold_signals += 1
            else:
                signal_counts["HOLD"] += 1
            meta = self.meta_filter.evaluate(features=features, signal=signal, decision=None, router_decision=None, snapshot=snapshot, trades=trades)
            if self.signal_only:
                continue

            analysis_mid = _safe_float(snapshot["bids"][0][0] + snapshot["asks"][0][0], 0.0) / 2.0
            basis_mode = str(getattr(self.cfg, "basis_mode", "none")).strip().lower()
            if basis_mode == "none":
                execution_mid = analysis_mid
            elif basis_mode == "fixed":
                execution_mid = analysis_mid + _safe_float(getattr(self.cfg, "fixed_basis", 0.0), 0.0)
            else:
                logger.error("[BACKTEST BASIS] invalid basis_mode=%s", basis_mode)
                continue
            self.basis.seed(analysis_mid, execution_mid)
            self.basis.update(analysis_mid, execution_mid)
            basis_status = self.basis.validate()
            if not basis_status.ok:
                logger.warning("[BACKTEST BASIS] blocked reason=%s", basis_status.reason)
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

            if position is None and decision.get("execute"):
                side = str(decision.get("side", "buy")).lower()
                entry = current_price * (1.0 + (self.cfg.slippage_bps / 10_000.0 if side == "buy" else -(self.cfg.slippage_bps / 10_000.0)))
                size = _safe_float(decision.get("position_size", 0.0))
                if self.capital_allocator is not None:
                    alloc = self.capital_allocator.allocate(
                        signal_confidence=_safe_float(signal.get("confidence", 0.0)),
                        regime_context={"regime": "TREND"},
                        current_equity=balance,
                        max_risk_pct=0.005,
                    )
                    if not alloc.get("allow_trading", True):
                        continue
                    size *= _safe_float(alloc.get("capital_scale", 1.0), 1.0)
                if size <= 0:
                    continue
                trade_id = f"bt-{i}"
                fees = _safe_float(self.cfg.fee_bps, 0.0) / 10_000.0
                fee_type = "pct"
                if fee_type not in ("quote", "pct"):
                    logger.warning(
                        "Missing fee_type in backtest_engine.py. Defaulting to pct. trade_id=%s",
                        trade_id if trade_id is not None else "unknown",
                    )
                    fee_type = "pct"
                if self.trade_lifecycle is not None:
                    self.trade_lifecycle.on_entry(side="LONG" if side=="buy" else "SHORT", entry_price=entry, size=size, features=features)
                if self.position_manager is not None:
                    self.position_manager.on_entry(symbol="BTC/USDT", side="LONG" if side=="buy" else "SHORT", size=size, entry_price=entry, order_id=trade_id, sl=_safe_float(decision.get("sl", 0.0)), tp=_safe_float(decision.get("tp",0.0)), signal=str(signal.get("signal","HOLD")), confidence=_safe_float(signal.get("confidence",0.0)), regime="unknown", fees=0.0, fee_type="pct", features=features)
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

            side = position["side"]
            entry = _safe_float(position["entry"])
            sl = _safe_float(position["sl"])
            tp = _safe_float(position["tp"])
            hold = i - int(position["entry_index"])

            hit_sl = current_price <= sl if side == "LONG" else current_price >= sl
            hit_tp = current_price >= tp if side == "LONG" else current_price <= tp
            flip = (
                (side == "LONG" and str(signal.get("signal", "HOLD")).upper() in ("SHORT", "STRONG_SHORT"))
                or (side == "SHORT" and str(signal.get("signal", "HOLD")).upper() in ("LONG", "STRONG_LONG"))
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
                            pnl=net_pnl_pct,
                            pnl_abs=pnl,
                            fees=position.get("fees"),
                            fee_type=position.get("fee_type"),
                            hold_time=hold,
                            signal=position.get("signal"),
                            features=position.get("entry_features"),
                            meta=position.get("meta"),
                        )
                    except Exception as e:
                        logger.warning("learning_engine failure: %s", str(e))
                if self.position_manager is not None and self.position_manager.has_position():
                    _ = self.position_manager.update(current_price, features)
                if self.trade_lifecycle is not None:
                    self.trade_lifecycle.on_exit(pnl_pct=net_pnl_pct, reason="backtest_exit")
                trade_log.append(
                    {
                        "entry_index": position["entry_index"],
                        "exit_index": i,
                        "side": side,
                        "entry": round(entry, 2),
                        "exit": round(current_price, 2),
                        "pnl": round(pnl, 4),
                        "pnl_pct": round(net_pnl_pct * 100.0, 4),
                        "signal": position["signal"],
                        "meta": position["meta"],
                    }
                )
                position = None

        total_trades = len(trade_log)
        wins = sum(1 for t in trade_log if _safe_float(t.get("pnl", 0.0)) > 0)
        losses = total_trades - wins
        win_rate = (wins / total_trades) if total_trades else 0.0
        gross_wins = [t["pnl"] for t in trade_log if _safe_float(t.get("pnl", 0.0)) > 0]
        gross_losses = [abs(_safe_float(t.get("pnl", 0.0))) for t in trade_log if _safe_float(t.get("pnl", 0.0)) <= 0]
        avg_win = sum(gross_wins) / len(gross_wins) if gross_wins else 0.0
        avg_loss = sum(gross_losses) / len(gross_losses) if gross_losses else 0.0
        expectancy = (avg_win * win_rate) - (avg_loss * (1.0 - win_rate))

        logger.info("[BACKTEST CACHE] hits=%d misses=%d", cache_hits, cache_misses)
        signal_coverage = (non_hold_signals / bars_processed) if bars_processed > 0 else 0.0
        coverage_reason = "" if signal_coverage > 0 else "no_non_hold_signals"
        if synthetic_microstructure:
            coverage_reason = "ohlcv_synthetic_microstructure_not_valid_for_live_signal_quality"
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 6),
            "pnl": round(balance - (initial_balance if initial_balance is not None else self.cfg.initial_balance), 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(_compute_sharpe(returns), 6),
            "expectancy": round(expectancy, 6),
            "trade_log": trade_log,
            "signal_only_mode": self.signal_only,
            "signal_coverage": round(signal_coverage, 6),
            "long_signals": signal_counts["LONG"],
            "short_signals": signal_counts["SHORT"],
            "hold_signals": signal_counts["HOLD"],
            "avg_return_per_trade": round((sum(returns) / len(returns)) if returns else 0.0, 6),
            "avg_holding_bars": round((sum((t["exit_index"] - t["entry_index"]) for t in trade_log) / total_trades) if total_trades else 0.0, 6),
            "alpha_non_empty_count": alpha_non_empty_count,
            "regime_state": "explicit_fallback" if synthetic_microstructure else "feature_derived",
            "signal_quality_valid": (signal_coverage > 0.0) and (not synthetic_microstructure),
            "signal_quality_reason": coverage_reason,
            "production_valid": bool(signal_quality_required and (not synthetic_microstructure) and (signal_coverage > 0.0)),
        }
