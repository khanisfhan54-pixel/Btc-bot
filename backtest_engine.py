# backtest_engine.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
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
    _be_log.getLogger(__name__).warning("backtest_engine: module import failed (%s) — BacktestEngine unusable", _be_import_err)
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
        self.basis = VenueBasisNormalizer(halt_threshold_pct=0.5)
        self.basis.set_venues("backtest", "backtest")
        self._analysis_cache: Dict[Tuple[int, float], Dict[str, Any]] = {}

    def run_backtest(self, ohlcv_data: List[list], initial_balance: float | None = None) -> Dict[str, Any]:
        cache_hits = 0
        cache_misses = 0
        data = [row for row in (ohlcv_data or []) if isinstance(row, (list, tuple)) and len(row) >= 6]
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
            }

        balance = float(initial_balance if initial_balance is not None else self.cfg.initial_balance)
        peak = balance
        max_dd = 0.0
        returns: List[float] = []
        trade_log: List[Dict[str, Any]] = []

        position: Optional[Dict[str, Any]] = None
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
            signal = self.signal_engine.generate(features)
            meta = self.meta_filter.evaluate(features=features, signal=signal, decision=None, router_decision=None, snapshot=snapshot, trades=trades)

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
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 6),
            "pnl": round(balance - (initial_balance if initial_balance is not None else self.cfg.initial_balance), 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(_compute_sharpe(returns), 6),
            "expectancy": round(expectancy, 6),
            "trade_log": trade_log,
        }
