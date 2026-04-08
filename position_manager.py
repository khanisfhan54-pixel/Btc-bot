from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time

logger = logging.getLogger(__name__)

try:
    from execution_quality import EXECUTION_QUALITY_ENGINE
except Exception as exc:
    logger.warning("[POSITION_MANAGER] execution_quality unavailable: %s", exc)

    class _FallbackExecutionQuality:
        def evaluate(self, **kwargs):
            return {
                "quality_score": 0.0,
                "slippage_bps": 0.0,
                "latency_ms": 0.0,
                "fallback": True,
            }

    EXECUTION_QUALITY_ENGINE = _FallbackExecutionQuality()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _deterministic_trade_id(
    side: str,
    entry_price: float,
    size: float,
    sl: float,
    tp: float,
    correlation_id: str = "",
) -> str:
    seed = "|".join(
        [
            str(side or "").upper().strip(),
            f"{round(_safe_float(entry_price), 8):.8f}",
            f"{round(_safe_float(size), 8):.8f}",
            f"{round(_safe_float(sl), 8):.8f}",
            f"{round(_safe_float(tp), 8):.8f}",
            str(correlation_id or "NO_CID").strip(),
        ]
    )
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def _is_valid_position(position: Any) -> bool:
    if position is None:
        return False
    try:
        side = str(getattr(position, "side", "")).upper().strip()
        entry_price = _safe_float(getattr(position, "entry_price", 0.0), 0.0)
        size = _safe_float(getattr(position, "size", 0.0), 0.0)
        sl = _safe_float(getattr(position, "sl", 0.0), 0.0)
        tp = _safe_float(getattr(position, "tp", 0.0), 0.0)
        opened_at = _safe_float(getattr(position, "opened_at", 0.0), 0.0)
        now_ts = time.time()
        if side not in ("LONG", "SHORT"):
            return False
        if entry_price <= 0 or sl <= 0 or tp <= 0:
            return False
        if size <= 0:
            return False
        if opened_at <= 0 or opened_at > now_ts + 5.0:
            return False
        return True
    except Exception:
        return False


@dataclass
class PositionState:
    side: str
    entry_price: float
    size: float
    sl: float
    tp: float
    opened_at: float = field(default_factory=time.time)

    highest_price: float = 0.0
    lowest_price: float = 0.0
    partial_taken: bool = False
    breakeven_moved: bool = False
    trailing_active: bool = False
    closed: bool = False
    exit_reason: Optional[str] = None

    # learning support fields (optional)
    features_entry: Optional[dict] = None
    features_exit: Optional[dict] = None
    trade_id: Optional[str] = None
    signal: Optional[str] = None
    confidence: Optional[float] = None
    regime: Optional[str] = None
    fees: Optional[float] = None
    fee_type: Optional[str] = None  # "quote" or "pct"
    correlation_id: str = ""

    def __post_init__(self):
        if self.fee_type is not None:
            self.fee_type = str(self.fee_type).lower().strip()

        if self.fees is not None and self.fee_type not in ("quote", "pct"):
            logger.error(
                "Invalid fee metadata in PositionState: fees=%s fee_type=%s side=%s entry_price=%s",
                self.fees,
                self.fee_type,
                self.side,
                self.entry_price,
            )
            raise ValueError("PositionState requires fee_type='quote' or 'pct' when fees are provided")


class PositionManager:
    """
    Institutional-style position management:
    - break-even move
    - trailing stop
    - partial take profit
    - toxicity / regime kill-switch
    - liquidity-aware exit decisions
    """

    def __init__(
        self,
        partial_tp_r_multiple: float = 1.0,
        breakeven_r_multiple: float = 1.0,
        trailing_start_r_multiple: float = 2.0,
        trailing_distance_bps: float = 35.0,
        toxic_vpin_threshold: float = 0.70,
        toxic_spread_bps_threshold: float = 20.0,
        min_liquidity_score: float = 0.35,
        learning_engine: Any = None,
    ) -> None:
        self.partial_tp_r_multiple = partial_tp_r_multiple
        self.breakeven_r_multiple = breakeven_r_multiple
        self.trailing_start_r_multiple = trailing_start_r_multiple
        self.trailing_distance_bps = trailing_distance_bps
        self.toxic_vpin_threshold = toxic_vpin_threshold
        self.toxic_spread_bps_threshold = toxic_spread_bps_threshold
        self.min_liquidity_score = min_liquidity_score
        self.position: Optional[PositionState] = None
        self.learning_engine = learning_engine
        self._already_closed = False

    def _reset_to_safe_state(self, reason: str) -> None:
        logger.error("Forcing safe flat state due to invalid/corrupt position state: %s", reason)
        self.position = None

    def _sanitize_internal_state(self) -> None:
        if self.position is None:
            return
        if not _is_valid_position(self.position):
            self._reset_to_safe_state("position_validation_failed")
            return
        self.position.size = max(0.0, _safe_float(self.position.size, 0.0))
        self.position.entry_price = _safe_float(self.position.entry_price, 0.0)
        self.position.sl = _safe_float(self.position.sl, 0.0)
        self.position.tp = _safe_float(self.position.tp, 0.0)
        self.position.opened_at = _safe_float(self.position.opened_at, time.time())
        self.position.highest_price = max(self.position.entry_price, _safe_float(self.position.highest_price, self.position.entry_price))
        self.position.lowest_price = min(self.position.entry_price, _safe_float(self.position.lowest_price, self.position.entry_price))
        if self.position.size <= 0 or self.position.entry_price <= 0 or self.position.sl <= 0 or self.position.tp <= 0:
            self._reset_to_safe_state("non_positive_position_field")

    def _reconcile_with_exchange(self, features: Dict[str, Any]) -> None:
        ex = features.get("exchange_position", features.get("exchange_state", None))
        if ex is None:
            return

        if not isinstance(ex, dict):
            logger.warning("exchange_position provided in unsupported format; skipping reconciliation")
            return

        ex_side = str(ex.get("side", "")).upper().strip()
        ex_size = max(0.0, _safe_float(ex.get("size", 0.0), 0.0))
        ex_entry = _safe_float(ex.get("entry_price", 0.0), 0.0)
        has_exchange_position = ex_side in ("LONG", "SHORT") and ex_size > 0 and ex_entry > 0

        if not has_exchange_position:
            if self.position is not None:
                logger.warning("Desync detected: exchange flat but internal position exists. Resetting internal state.")
                self.position = None
            return

        if self.position is None:
            logger.warning("Desync detected: exchange has open position but internal state is flat. Rebuilding internal state.")
            self.position = PositionState(
                side=ex_side,
                entry_price=ex_entry,
                size=ex_size,
                sl=max(1e-12, _safe_float(ex.get("sl", ex_entry), ex_entry)),
                tp=max(1e-12, _safe_float(ex.get("tp", ex_entry), ex_entry)),
                highest_price=ex_entry,
                lowest_price=ex_entry,
            )
            self._sanitize_internal_state()
            return

        local = self.position
        local_changed = (
            local.side != ex_side
            or abs(local.size - ex_size) > 1e-12
            or abs(local.entry_price - ex_entry) > 1e-12
        )
        if local_changed:
            logger.warning(
                "Desync detected: correcting internal state to exchange source of truth. local=(%s,%.8f,%.8f) ex=(%s,%.8f,%.8f)",
                local.side, local.size, local.entry_price, ex_side, ex_size, ex_entry,
            )
            local.side = ex_side
            local.size = ex_size
            local.entry_price = ex_entry
            local.sl = max(1e-12, _safe_float(ex.get("sl", local.sl), local.sl))
            local.tp = max(1e-12, _safe_float(ex.get("tp", local.tp), local.tp))
            local.highest_price = max(local.highest_price, ex_entry)
            local.lowest_price = min(local.lowest_price, ex_entry)
            local.closed = False
            self._sanitize_internal_state()

    def reduce_on_cascade(self, position_size: float, cascade_score: float) -> float:
        try:
            if _safe_float(cascade_score) > 0.8:
                return _safe_float(position_size) * 0.5
            return _safe_float(position_size)
        except Exception:
            return _safe_float(position_size)

    def has_position(self) -> bool:
        self._sanitize_internal_state()
        return self.position is not None and not self.position.closed

    def get_position(self) -> dict:
        self._sanitize_internal_state()
        return dict(vars(self.position)) if getattr(self, "position", None) else {}

    def on_entry(
        self,
        side: str,
        entry_price: float,
        size: float,
        sl: float,
        tp: float,
        features: Optional[Dict[str, Any]] = None,
        trade_id: Optional[str] = None,
        signal: Optional[str] = None,
        confidence: Optional[float] = None,
        regime: Optional[str] = None,
        fees: Optional[float] = None,
        fee_type: Optional[str] = None,
        correlation_id: str = "",
    ) -> None:
        self._already_closed = False
        cid = (correlation_id or "")[:12]
        self._sanitize_internal_state()
        side = str(side).upper().strip()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")

        clean_entry = _safe_float(entry_price, 0.0)
        clean_size = max(0.0, _safe_float(size, 0.0))
        clean_sl = _safe_float(sl, 0.0)
        clean_tp = _safe_float(tp, 0.0)
        if clean_entry <= 0 or clean_size <= 0 or clean_sl <= 0 or clean_tp <= 0:
            logger.error(
                "Rejected invalid entry payload cid=%s side=%s entry=%s size=%s sl=%s tp=%s",
                cid, side, entry_price, size, sl, tp,
            )
            return
        if self.has_position():
            logger.warning(
                "Duplicate entry blocked cid=%s existing_side=%s existing_size=%.8f requested_side=%s requested_size=%s",
                cid, self.position.side, self.position.size, side, size,
            )
            return

        if fee_type is not None:
            fee_type = str(fee_type).lower().strip()

        if fees is not None and fee_type not in ("quote", "pct"):
            logger.error(
                "Invalid fee metadata at entry source: fees=%s fee_type=%s side=%s entry_price=%s size=%s",
                fees,
                fee_type,
                side,
                entry_price,
                size,
            )
            raise ValueError("fee_type must be provided as 'quote' or 'pct' when fees are used")

        normalized_trade_id = str(trade_id or "").strip()
        if not normalized_trade_id:
            logger.warning(
                "trade_id_missing_at_entry cid=%s generating_deterministic_id",
                correlation_id[:12] if correlation_id else "",
            )
            normalized_trade_id = _deterministic_trade_id(
                side=side,
                entry_price=clean_entry,
                size=clean_size,
                sl=clean_sl,
                tp=clean_tp,
                correlation_id=correlation_id or "",
            )

        self.position = PositionState(
            side=side,
            entry_price=clean_entry,
            size=clean_size,
            sl=clean_sl,
            tp=clean_tp,
            highest_price=clean_entry,
            lowest_price=clean_entry,
            features_entry=features if features is not None else None,
            trade_id=normalized_trade_id,
            signal=signal,
            confidence=confidence,
            regime=regime,
            fees=fees,
            fee_type=fee_type,
            correlation_id=correlation_id or "",
        )
        self._already_closed = False
        logger.info(
            "position_open cid=%s side=%s entry=%.8f size=%.8f sl=%.8f tp=%.8f",
            cid, side, clean_entry, clean_size, clean_sl, clean_tp,
        )
        self._sanitize_internal_state()

    def close(self, reason: str = "manual", exit_price: float = 0.0, features_exit: Optional[dict] = None) -> Dict[str, Any]:
        self._sanitize_internal_state()
        if self._already_closed:
            return {"action": "NO_POSITION", "reason": "already_closed"}
        if not self.position:
            return {"action": "NO_POSITION"}
        entry_price = _safe_float(getattr(self.position, "entry_price", 0.0), 0.0)
        correlation_id = str(getattr(self.position, "correlation_id", "") or "")
        cid = correlation_id[:12]
        side = str(getattr(self.position, "side", "LONG")).upper().strip()
        if side not in ("LONG", "SHORT"):
            side = "LONG"
        size = max(0.0, _safe_float(getattr(self.position, "size", 0.0), 0.0))
        _tp = _safe_float(getattr(self.position, "tp", 0.0), 0.0)
        _sl = _safe_float(getattr(self.position, "sl", 0.0), 0.0)
        safe_eq_fallback = {
            "quality_score": 0.0,
            "slippage_bps": 0.0,
            "latency_ms": 0.0,
            "fallback": True,
        }
        learning_recorded = False
        learning_engine = getattr(self, "learning_engine", None)
        features_entry = getattr(self.position, "features_entry", None)
        features_entry_safe = features_entry if isinstance(features_entry, dict) else {}
        features_exit_safe = features_exit if isinstance(features_exit, dict) else {}
        signal = str(getattr(self.position, "signal", side) or side)
        confidence = _safe_float(getattr(self.position, "confidence", 0.0), 0.0)
        regime = str(getattr(self.position, "regime", "unknown") or "unknown")
        if "correlation_id" not in features_entry_safe:
            features_entry_safe = {**features_entry_safe, "correlation_id": correlation_id}
        if "regime" not in features_entry_safe:
            features_entry_safe = {**features_entry_safe, "regime": regime}
        mfe = 0.0
        mae = 0.0
        entry_ts = getattr(self.position, "opened_at", None)
        exit_ts = time.time()
        resolved_trade_id = str(getattr(self.position, "trade_id", "") or "").strip()
        if not resolved_trade_id:
            resolved_trade_id = _deterministic_trade_id(
                side=side,
                entry_price=entry_price,
                size=size,
                sl=_sl,
                tp=_tp,
                correlation_id=correlation_id,
            )

        try:
            exit_price = _safe_float(exit_price, 0.0)
            if exit_price <= 0:
                logger.warning("Invalid exit_price received (%s). Falling back to entry_price for safe close.", exit_price)
                exit_price = entry_price

            pnl_pct = 0.0
            if entry_price > 0 and exit_price > 0:
                if side == "LONG":
                    pnl_pct = (exit_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price

            if abs(size) < 1e-12:
                logger.warning("Closing position with near-zero size. Forcing pnl_pct=0.")
                pnl_pct = 0.0

            logger.info(
                "[POSITION CLOSED] cid=%s side=%s entry=%.2f exit=%.2f pnl=%.4f%% size=%.6f reason=%s",
                cid, side, entry_price, exit_price, pnl_pct * 100, size, reason,
            )

            reason_lower = str(reason).lower()
            if reason_lower in ("tp", "take_profit", "target") and _tp > 0:
                expected_price = _tp
            elif reason_lower in ("sl", "stop_loss", "stop") and _sl > 0:
                expected_price = _sl
            else:
                expected_price = entry_price

            slippage_bps = 0.0
            if expected_price > 0 and exit_price > 0:
                if side == "LONG":
                    slippage_bps = ((exit_price - expected_price) / expected_price) * 10_000.0
                else:
                    slippage_bps = ((expected_price - exit_price) / expected_price) * 10_000.0

            try:
                eq_result = EXECUTION_QUALITY_ENGINE.evaluate(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    expected_price=expected_price,
                    slippage_bps=slippage_bps,
                    latency_ms=0.0,
                    spread_bps=0.0,
                    side=side,
                    reason=reason,
                    price_after_1s=None,
                    price_after_3s=None,
                )
            except Exception as exc:
                logger.error("[EXECUTION_QUALITY] evaluation failed: %s", exc)
                eq_result = safe_eq_fallback

            self.position.closed = True
            self.position.exit_reason = reason

            ps = self.position

            features_exit_actual = features_exit if features_exit is not None else getattr(ps, "features_exit", None)
            features_exit_safe = features_exit_actual if isinstance(features_exit_actual, dict) else {}

            entry_ts = getattr(ps, "opened_at", entry_ts)
            exit_ts = time.time()

            hp = _safe_float(getattr(ps, "highest_price", entry_price), entry_price)
            lp = _safe_float(getattr(ps, "lowest_price", entry_price), entry_price)

            mfe = 0.0
            mae = 0.0
            if entry_price > 0:
                if side == "LONG":
                    mfe = max(0.0, (hp - entry_price) / entry_price)
                    mae = max(0.0, (entry_price - lp) / entry_price)
                else:
                    mfe = max(0.0, (entry_price - lp) / entry_price)
                    mae = max(0.0, (hp - entry_price) / entry_price)

            mfe = _safe_float(mfe)
            mae = _safe_float(mae)
            mfe = max(0.0, mfe)
            mae = max(0.0, mae)

            fees = getattr(ps, "fees", 0.0)
            fee_type = getattr(ps, "fee_type", None)

            fees_val = _safe_float(fees)

            if fees_val <= 0:
                fee_pct = 0.0

            elif fee_type == "pct":
                fee_pct = fees_val

            elif fee_type == "quote":
                notional = max(entry_price * abs(size), 1e-9)
                fee_pct = fees_val / notional

            else:
                logger.error(
                    "Missing fee_type detected. Defaulting fee_pct=0.0. This indicates upstream integration issue. "
                    "fees=%s fee_type=%s side=%s entry_price=%s exit_price=%s reason=%s",
                    fees,
                    fee_type,
                    side,
                    entry_price,
                    exit_price,
                    reason,
                )
                fee_pct = 0.0

            realized_pnl = pnl_pct - fee_pct

            holding_time = 0.0
            try:
                if entry_ts is not None and exit_ts is not None:
                    holding_time = float(exit_ts) - float(entry_ts)
            except Exception:
                holding_time = 0.0
            holding_time = max(0.0, _safe_float(holding_time, 0.0))

            if "tp" in reason_lower:
                exit_type = "tp"
            elif "sl" in reason_lower:
                exit_type = "sl"
            elif "toxic" in reason_lower:
                exit_type = "toxicity_exit"
            else:
                exit_type = "manual"

            if abs(mfe) < 1e-6:
                exit_score = realized_pnl
            else:
                exit_score = realized_pnl / mfe
            exit_score = _clamp(exit_score, -10.0, 10.0)

            trade_id = resolved_trade_id
            signal = getattr(ps, "signal", signal)
            confidence = _safe_float(getattr(ps, "confidence", confidence), confidence)
            regime = str(getattr(ps, "regime", regime) or regime)
            stop_loss = getattr(ps, "sl", None)

            if learning_engine and hasattr(learning_engine, "record_closed_trade"):
                try:
                    learning_engine.record_closed_trade(
                        signal=signal,
                        side=side,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        size=size,
                        entry_ts=entry_ts,
                        exit_ts=exit_ts,
                        confidence=confidence,
                        features_entry=features_entry,
                        features_exit=features_exit_actual,
                        reason=reason,
                        exit_type=exit_type,
                        fees=fees,
                        mfe_pct=mfe,
                        mae_pct=mae,
                        stop_loss=stop_loss,
                        trade_id=trade_id,
                        correlation_id=correlation_id,
                    )
                    learning_recorded = True
                    logger.info(
                        "learning_trade_emitted trade_id=%s pnl=%.4f",
                        trade_id,
                        realized_pnl,
                    )
                except Exception as e:
                    logger.warning("[LEARNING] closed trade recording failed: %s", e)
                    try:
                        learning_engine.record_closed_trade(
                            signal=signal or side,
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            size=size,
                            confidence=_safe_float(confidence, 0.0),
                            features_entry=features_entry_safe,
                            features_exit=features_exit_safe,
                            reason="fallback_exception_close",
                            exit_type="fallback",
                            pnl_override=None,
                            mfe_pct=mfe,
                            mae_pct=mae,
                            trade_id=resolved_trade_id,
                            correlation_id=correlation_id,
                        )
                        learning_recorded = True
                    except Exception as fallback_err:
                        logger.error("FATAL: fallback learning also failed: %s", fallback_err)

            if learning_engine and hasattr(learning_engine, "record_exit_quality"):
                try:
                    learning_engine.record_exit_quality(
                        mfe_pct=mfe,
                        mae_pct=mae,
                        exit_quality_score=exit_score,
                        exit_classification=exit_type,
                        holding_seconds=holding_time,
                        reason=reason,
                        regime=regime,
                        realized_pnl=realized_pnl,
                        confidence=confidence,
                        side=side,
                    )
                except Exception as e:
                    logger.warning("[LEARNING] exit quality analytics failed: %s", e)

            out = {
                "action":           "CLOSE",
                "reason":           reason,
                "correlation_id":   correlation_id,
                "trade_id":         resolved_trade_id,
                "side":             side,
                "entry_price":      entry_price,
                "exit_price":       exit_price,
                "pnl_pct":          pnl_pct,
                "size":             size,
                "sl":               _sl,
                "tp":               _tp,
                "exec_quality":     eq_result,
                "learning_recorded": learning_recorded,
            }
            return out
        except Exception as exc:
            logger.error("[POSITION CLOSE] unexpected failure, returning safe close payload: %s", exc)
            if not learning_recorded and learning_engine and hasattr(learning_engine, "record_closed_trade"):
                try:
                    learning_engine.record_closed_trade(
                        signal=signal or side,
                        side=side,
                        entry_price=entry_price,
                        exit_price=max(_safe_float(exit_price, entry_price), entry_price),
                        size=size,
                        confidence=_safe_float(confidence, 0.0),
                        features_entry=features_entry_safe,
                        features_exit=features_exit_safe,
                        reason="fallback_exception_close",
                        exit_type="fallback",
                        pnl_override=None,
                        mfe_pct=mfe,
                        mae_pct=mae,
                        trade_id=resolved_trade_id,
                        correlation_id=correlation_id,
                    )
                    learning_recorded = True
                except Exception as fallback_exc:
                    logger.error("FATAL: fallback learning also failed: %s", fallback_exc)
            exit_price_safe = entry_price
            try:
                _candidate_exit = _safe_float(exit_price, entry_price)
                if _candidate_exit > 0:
                    exit_price_safe = _candidate_exit
            except Exception:
                exit_price_safe = entry_price

            pnl_pct_safe = 0.0
            try:
                pnl_candidate = None
                if "pnl_pct" in locals():
                    pnl_candidate = _safe_float(locals().get("pnl_pct"), 0.0)
                if pnl_candidate is not None and math.isfinite(pnl_candidate):
                    pnl_pct_safe = pnl_candidate
                elif entry_price > 0 and exit_price_safe > 0:
                    if side == "LONG":
                        pnl_pct_safe = (exit_price_safe - entry_price) / entry_price
                    else:
                        pnl_pct_safe = (entry_price - exit_price_safe) / entry_price
            except Exception:
                pnl_pct_safe = 0.0
            return {
                "action": "CLOSE",
                "reason": reason,
                "correlation_id": correlation_id,
                "trade_id": resolved_trade_id,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price_safe,
                "pnl_pct": pnl_pct_safe,
                "size": size,
                "sl": _sl,
                "tp": _tp,
                "exec_quality": safe_eq_fallback,
                "learning_recorded": learning_recorded,
                "fallback_error": True,
            }
        finally:
            self._already_closed = True
            self.position = None

    def update(self, current_price: float, features: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(features, dict):
            features = {}
        self._sanitize_internal_state()
        self._reconcile_with_exchange(features)
        self._sanitize_internal_state()
        if not self.position or self.position.closed:
            return {"action": "NO_POSITION", "reason": "no_open_position"}

        price = _safe_float(current_price)
        if price <= 0:
            logger.warning("Invalid current_price in update (%s). Holding without state mutation.", current_price)
            return {"action": "HOLD", "reason": "invalid_price"}
        side = self.position.side
        entry = self.position.entry_price
        sl = self.position.sl
        tp = self.position.tp

        liquidity_score = _safe_float(features.get("liquidity_score", 0.0))
        vpin = _safe_float(features.get("vpin", features.get("toxicity", 0.0)))
        spread_bps = _safe_float(features.get("spread_bps", 0.0))
        urgency = _safe_float(features.get("urgency", 0.5))
        fill_prob = _safe_float(features.get("fill_prob", 1.0))
        latency_ms = _safe_float(features.get("latency_ms", 0.0))
        regime = str(features.get("regime", "unknown")).lower()
        hidden_liquidity = bool(features.get("hidden_liquidity", False))

        if price > self.position.highest_price:
            self.position.highest_price = price
        if price < self.position.lowest_price:
            self.position.lowest_price = price

        risk = abs(entry - sl)
        if risk <= 0:
            return {"action": "HOLD", "reason": "invalid_risk"}

        if vpin >= self.toxic_vpin_threshold:
            self.position.features_exit = features
            return self.close("toxic_flow", exit_price=price, features_exit=features)

        if spread_bps >= self.toxic_spread_bps_threshold:
            self.position.features_exit = features
            return self.close("spread_too_wide", exit_price=price, features_exit=features)

        if liquidity_score < self.min_liquidity_score and regime == "toxic":
            self.position.features_exit = features
            return self.close("illiquid_toxic_regime", exit_price=price, features_exit=features)

        adaptive_latency = _safe_float(features.get("execution_latency", 1500.0))
        if latency_ms > max(1500.0, adaptive_latency):
            self.position.features_exit = features
            return self.close("stale_signal", exit_price=price, features_exit=features)

        if fill_prob < 0.20 and regime in ("range", "toxic"):
            return {"action": "HOLD", "reason": "low_fill_prob_wait"}

        r_mult = self._current_r_multiple(price)

        if not self.position.partial_taken and r_mult >= self.partial_tp_r_multiple:
            self.position.partial_taken = True
            return {
                "action": "PARTIAL_TAKE_PROFIT",
                "reason": "hit_1r",
                "reduce_size_pct": 0.50,
                "new_sl": self.position.sl,
                "new_tp": self.position.tp,
            }

        if not self.position.breakeven_moved and r_mult >= self.breakeven_r_multiple:
            self.position.breakeven_moved = True
            new_sl = entry

            cushion_bps = max(2.0, min(8.0, spread_bps * 0.25 + urgency * 3.0))
            cushion = entry * cushion_bps / 10_000.0

            if side == "LONG":
                new_sl = max(new_sl, entry + cushion * 0.15)
            else:
                new_sl = min(new_sl, entry - cushion * 0.15)

            self.position.sl = new_sl
            return {
                "action": "MOVE_SL_TO_BE",
                "reason": "hit_1r_break_even",
                "new_sl": new_sl,
                "new_tp": self.position.tp,
            }

        if r_mult >= self.trailing_start_r_multiple:
            self.position.trailing_active = True
            trailed_sl = self._compute_trailing_sl(price, entry, side, spread_bps)
            if side == "LONG" and trailed_sl > self.position.sl:
                self.position.sl = trailed_sl
                return {
                    "action": "TRAIL_STOP",
                    "reason": "trail_after_2r",
                    "new_sl": trailed_sl,
                    "new_tp": self.position.tp,
                }
            if side == "SHORT" and trailed_sl < self.position.sl:
                self.position.sl = trailed_sl
                return {
                    "action": "TRAIL_STOP",
                    "reason": "trail_after_2r",
                    "new_sl": trailed_sl,
                    "new_tp": self.position.tp,
                }

        if side == "LONG":
            if price <= self.position.sl:
                self.position.features_exit = features
                return self.close("stop_loss_hit", exit_price=price, features_exit=features)
            if price >= self.position.tp:
                self.position.features_exit = features
                return self.close("take_profit_hit", exit_price=price, features_exit=features)
        else:
            if price >= self.position.sl:
                self.position.features_exit = features
                return self.close("stop_loss_hit", exit_price=price, features_exit=features)
            if price <= self.position.tp:
                self.position.features_exit = features
                return self.close("take_profit_hit", exit_price=price, features_exit=features)

        return {
            "action": "HOLD",
            "reason": "position_managed",
            "new_sl": self.position.sl,
            "new_tp": self.position.tp,
        }

    def _current_r_multiple(self, price: float) -> float:
        if not self.position:
            return 0.0
        price = _safe_float(price, 0.0)
        if price <= 0:
            return 0.0
        entry = self.position.entry_price
        sl = self.position.sl
        risk = max(abs(entry - sl), 1e-9)
        if risk <= 0:
            return 0.0

        if self.position.side == "LONG":
            return (price - entry) / risk
        return (entry - price) / risk

    def _compute_trailing_sl(
        self,
        price: float,
        entry: float,
        side: str,
        spread_bps: float,
    ) -> float:
        side = side.upper()
        trail_bps = _clamp(self.trailing_distance_bps + spread_bps * 0.25, 20.0, 120.0)
        trail_dist = price * trail_bps / 10_000.0

        if side == "LONG":
            return max(entry, self.position.highest_price - trail_dist)
        else:
            return min(entry, self.position.lowest_price + trail_dist)
