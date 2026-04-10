# execution.py – order execution logic
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs): pass  # type: ignore[misc]

try:
    import ccxt
except ImportError as _ccxt_err:
    raise ImportError(
        "ccxt is required for execution.py. Install it with: pip install ccxt"
    ) from _ccxt_err

try:
    from telegram_bot import send_telegram_message
except Exception as _tg_import_err:
    import logging as _tg_logging
    _tg_logging.getLogger(__name__).warning("telegram_bot import failed in execution.py: %s", _tg_import_err)
    def send_telegram_message(message: str) -> bool:  # type: ignore[misc]
        return False

try:
    from learning_engine import LEARNING_ENGINE
except Exception:
    LEARNING_ENGINE = None

load_dotenv()

logger = logging.getLogger(__name__)

LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() in ("true", "1", "yes")

Level = Tuple[float, float]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if abs(b) > 1e-12 else default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _default_alpha():
    return {
        "direction": "NEUTRAL",
        "confidence": 0.5,
        "prob_above": 0.5,
        "prob_below": 0.5,
        "micro_prob": 0.5,
        "macro_prob": 0.5,
    }


def _validate_alpha(alpha: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not isinstance(alpha, dict):
            logger.warning("Alpha validation adjusted: %s", alpha)
            return _default_alpha()

        conf = float(alpha.get("confidence", 0.5))
        p_up = float(alpha.get("prob_above", 0.5))
        p_dn = float(alpha.get("prob_below", 0.5))
        direction = alpha.get("direction")
        adjusted = False

        if not math.isfinite(conf):
            conf = 0.5
            adjusted = True
        conf = max(0.0, min(1.0, conf))
        if not math.isfinite(p_up):
            p_up = 0.5
            adjusted = True
        if not math.isfinite(p_dn):
            p_dn = 0.5
            adjusted = True

        if abs((p_up + p_dn) - 1.0) > 1e-6:
            total = max(1e-8, p_up + p_dn)
            p_up /= total
            p_dn /= total
            adjusted = True

        if direction not in ("LONG", "SHORT", "NEUTRAL"):
            direction = "NEUTRAL"
            adjusted = True

        validated = {
            **alpha,
            "confidence": max(0.0, min(1.0, conf)),
            "prob_above": max(0.0, min(1.0, p_up)),
            "prob_below": max(0.0, min(1.0, p_dn)),
            "direction": direction,
        }
        if adjusted:
            logger.warning("Alpha validation adjusted: %s", alpha)
        return validated
    except Exception:
        logger.warning("Alpha validation adjusted: %s", alpha)
        return _default_alpha()


def _get_order_id(order: Any) -> str:
    if isinstance(order, dict):
        return str(order.get("id") or order.get("orderId") or "N/A")
    return str(
        getattr(order, "id", None)
        or getattr(order, "orderId", None)
        or "N/A"
    )


def _enforce_entry_fee_metadata(fees, fee_type, trade_id=None):
    fees_val = _safe_float(fees, 0.0)

    fee_type_val = str(fee_type).lower().strip() if fee_type is not None else None
    if fee_type_val not in ("quote", "pct"):
        logger.warning(
            "Invalid or missing fee_type in execution.py. Defaulting to pct. trade_id=%s",
            trade_id if trade_id is not None else "unknown",
        )
        fee_type_val = "pct"

    return fees_val, fee_type_val


def _extract_levels(snapshot: Dict[str, Any]) -> Tuple[List[Level], List[Level]]:
    def to_levels(raw: Any) -> List[Level]:
        out: List[Level] = []
        for lvl in raw or []:
            try:
                if isinstance(lvl, dict):
                    p = float(lvl.get("price"))
                    q = float(
                        lvl.get("size")
                        or lvl.get("amount")
                        or lvl.get("qty")
                        or lvl.get("quantity")
                        or 0.0
                    )
                else:
                    p = float(lvl[0])
                    q = float(lvl[1])
                if p > 0 and q >= 0:
                    out.append((p, q))
            except Exception:
                continue
        return out

    if "bids" in snapshot and "asks" in snapshot:
        bids = to_levels(snapshot.get("bids", []))
        asks = to_levels(snapshot.get("asks", []))
    elif "order_book" in snapshot:
        ob = snapshot.get("order_book") or {}
        bids = to_levels(ob.get("bids", []))
        asks = to_levels(ob.get("asks", []))
    else:
        raise ValueError("snapshot must contain bids/asks or order_book")

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])
    return bids, asks


def _best_prices(bids: List[Level], asks: List[Level]) -> Tuple[float, float, float, float]:
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2.0
    spread = max(0.0, best_ask - best_bid)
    return best_bid, best_ask, mid, spread


def _find_liquidity_wall(levels: List[Level], side: str, min_ratio: float = 1.5) -> Optional[float]:
    if len(levels) < 2:
        return None

    sizes = [q for _, q in levels if q > 0]
    if len(sizes) < 2:
        return None

    med = median(sizes)
    threshold = med * min_ratio
    candidates = [(p, q) for p, q in levels if q >= threshold]

    if not candidates:
        return None

    if side == "bid":
        return max(candidates, key=lambda x: x[0])[0]
    return min(candidates, key=lambda x: x[0])[0]


@dataclass
class ExecutionConfig:
    risk_per_trade: float = 0.005
    max_position_pct: float = 0.10
    min_confidence: float = 0.60
    min_liquidity_score: float = 0.35
    max_spread_bps: float = 12.0
    max_spoofing_intensity: float = 0.70
    fee_bps: float = 8.0
    slippage_bps_base: float = 3.0
    reward_risk_min: float = 1.6
    void_buffer_bps: float = 2.0
    take_profit_buffer_bps: float = 1.0
    max_age_ms: int = 2_500


class ExecutionLogic:
    def __init__(self, config: ExecutionConfig | None = None, learning_engine: Any = None) -> None:
        self.cfg = config or ExecutionConfig()
        self.learning_engine = learning_engine

    def decide(
        self,
        signal_payload: Dict[str, Any],
        features_payload: Dict[str, Any],
        snapshot: Dict[str, Any],
        account_equity: float,
        meta_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta_result = meta_result or {}
        learning_params: Dict[str, Any] = {}
        if self.learning_engine is not None:
            try:
                learning_params = self.learning_engine.get_adaptive_params()
            except Exception:
                learning_params = {}
        if not isinstance(learning_params, dict):
            learning_params = {}
        if not isinstance(signal_payload, dict):
            return {
                "execute": False,
                "side": "buy",
                "sl": 0.0,
                "tp": 0.0,
                "position_size": 0.0,
                "reason": "invalid_signal_payload",
                "meta_result": meta_result,
                "learning_params": learning_params,
            }
        signal = str(signal_payload.get("signal", "HOLD")).upper()
        confidence = _safe_float(signal_payload.get("confidence", 0.0), 0.0)
        if not isinstance(features_payload, dict):
            return {
                "execute": False,
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0,
                "tp": 0.0,
                "position_size": 0.0,
                "reason": "invalid_features_payload",
                "meta_result": meta_result,
                "learning_params": learning_params,
            }

        raw_features = features_payload.get("features", features_payload)
        features = raw_features if isinstance(raw_features, dict) else {}
        if meta_result and not meta_result.get("allow_trade", True):
            return {
                "execute": False,
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": meta_result.get("reason", "meta_blocked"),
                "meta_result": meta_result,
                "learning_params": learning_params,
            }

        if signal not in ("LONG", "SHORT"):
            return {
                "execute": False, "side": "buy", "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "unsupported_signal",
                "meta_result": meta_result, "learning_params": learning_params,
            }

        if not features:
            return {
                "execute": False, "side": "buy", "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "missing_features",
                "meta_result": meta_result, "learning_params": learning_params,
            }

        alpha = _validate_alpha(features.get("alpha", {}))
        alpha_direction = alpha.get("direction")
        alpha_confidence = _clamp(_safe_float(alpha.get("confidence", 0.5), 0.5), 0.0, 1.0)
        if isinstance(alpha_direction, str):
            alpha_direction = alpha_direction.upper().strip()
        else:
            alpha_direction = None

        try:
            best_bid        = _safe_float(features.get("best_bid", 0.0), 0.0)
            best_ask        = _safe_float(features.get("best_ask", 0.0), 0.0)
            mid             = _safe_float(features.get("mid", 0.0), 0.0)
            spread          = _safe_float(features.get("spread", 0.0), 0.0)
            spread_bps      = _safe_float(features.get("spread_bps", 999.0), 999.0)
            gap_proxy_bps   = _safe_float(features.get("gap_proxy_bps", 0.0), 0.0)
            largest_gap_bps = _safe_float(features.get("largest_gap_bps", 0.0), 0.0)
            liquidity_score = _safe_float(features.get("liquidity_score", 0.0), 0.0)
            liquidity_score = _clamp(liquidity_score, 0.0, 1.0)
            spoofing_intensity = _safe_float(
                features.get(
                    "spoofing_intensity",
                    features.get("spoof_score", features.get("spoof", 0.0)),
                ),
                0.0,
            )
            urgency          = _safe_float(features.get("urgency", 0.5), 0.5)
            regime           = features.get("regime", "unknown")
            regime           = str(regime).lower().strip()
            if regime not in ("trend", "range", "toxic"):
                regime = "unknown"
            hidden_liquidity = bool(features.get("hidden_liquidity", False))
            latency_ms_feat  = _safe_float(features.get("latency_ms", 0.0), 0.0)
        except Exception as exc:
            raise ValueError(f"invalid features payload: {exc}") from exc

        _ = (best_bid, best_ask)

        regime_risk_map = learning_params.get("regime_risk_map", {}) if isinstance(learning_params, dict) else {}
        if not isinstance(regime_risk_map, dict):
            regime_risk_map = {}
        regime_risk = _clamp(
            _safe_float(regime_risk_map.get(regime, regime_risk_map.get("unknown", 0.8)), 0.8),
            0.0,
            1.5,
        )
        base_risk = _clamp(
            _safe_float(
                learning_params.get("risk_multiplier", learning_params.get("risk_scale", 1.0))
            ),
            0.0,
            2.0,
        )
        learning_risk_scale = _clamp(base_risk * regime_risk, 0.0, 2.0)
        learning_conf_thr = _clamp(_safe_float(learning_params.get("min_confidence_threshold", learning_params.get("confidence_threshold", 0.60))), 0.3, 0.9)
        learning_meta_strictness = _clamp(_safe_float(learning_params.get("meta_strictness", 1.0)), 0.75, 1.35)  # noqa: F841
        execution_quality = _clamp(_safe_float(learning_params.get("execution_quality", 1.0)), 0.0, 1.0)
        execution_slippage_bps = max(0.0, _safe_float(learning_params.get("execution_slippage", 0.0)))
        execution_latency_ms = max(0.0, _safe_float(learning_params.get("execution_latency", 0.0)))
        execution_fill_rate = _clamp(_safe_float(learning_params.get("execution_fill_rate", 0.5)), 0.0, 1.0)
        slippage_tolerance_bps = _clamp(
            _safe_float(learning_params.get("slippage_tolerance_bps", self.cfg.max_spread_bps)),
            2.0,
            20.0,
        )
        alpha_ts = _safe_float(alpha.get("timestamp", 0.0), 0.0)
        now_ts = time.time()
        if alpha_ts > 0.0 and math.isfinite(alpha_ts) and alpha_ts <= now_ts:
            latency_ms = max(0.0, (now_ts - alpha_ts) * 1000.0)
        else:
            latency_ms = max(0.0, _safe_float(features.get("latency_ms", 0.0), 0.0))
        alpha_decay = max(0.6, 1.0 - (latency_ms / 3000.0))
        alpha_confidence = _clamp(alpha_confidence * alpha_decay, 0.0, 1.0)
        alpha_strength = _clamp(abs(alpha_confidence - 0.5) * 2.0, 0.0, 1.0)
        base_confidence = confidence

        if alpha_direction == signal:
            boost = 1.0 + (0.1 * alpha_strength)
            alpha_weight = 0.5 * _clamp(1.0 - confidence, 0.0, 1.0)
            confidence *= 1.0 + ((boost - 1.0) * alpha_weight)
        elif (alpha_direction == "LONG" and signal == "SHORT") or (alpha_direction == "SHORT" and signal == "LONG"):
            penalty = 0.1 * alpha_strength
            alpha_weight = _clamp(confidence, 0.0, 1.0)
            confidence *= 1.0 - (penalty * alpha_weight)
        if alpha_strength > 0.8 and (
            (alpha_direction == "LONG" and signal == "SHORT")
            or (alpha_direction == "SHORT" and signal == "LONG")
        ):
            confidence *= 0.6
        max_alpha_impact = 0.15
        confidence_delta = confidence - base_confidence
        confidence = base_confidence + _clamp(confidence_delta, -max_alpha_impact, max_alpha_impact)
        prev_conf = getattr(self, "_last_exec_conf", confidence)
        confidence = 0.6 * prev_conf + 0.4 * confidence
        self._last_exec_conf = confidence
        confidence = _clamp(confidence, 0.01, 0.99)

        if confidence < max(self.cfg.min_confidence, learning_conf_thr):
            return {
                "execute": False,
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0,
                "tp": 0.0,
                "position_size": 0.0,
                "reason": "low_confidence",
                "meta_result": meta_result,
                "learning_params": learning_params,
            }

        effective_spread_limit = max(min(self.cfg.max_spread_bps, slippage_tolerance_bps), 2.0)
        if liquidity_score < self.cfg.min_liquidity_score or spread_bps > effective_spread_limit:
            reasons = []
            if "liquidity_score" not in features:
                reasons.append("missing_liquidity_data")
            if liquidity_score < self.cfg.min_liquidity_score:
                reasons.append("low_liquidity")
            if spread_bps > effective_spread_limit:
                reasons.append("spread_too_wide")
            _reason = "|".join(reasons) if reasons else "execution_filtered"
            return {
                "execute": False,
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0,
                "tp": 0.0,
                "position_size": 0.0,
                "reason": _reason,
                "meta_result": meta_result,
                "learning_params": learning_params,
            }

        if spoofing_intensity > self.cfg.max_spoofing_intensity:
            return {
                "execute": False,
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0,
                "tp": 0.0,
                "position_size": 0.0,
                "reason": "spoofing_detected",
                "meta_result": meta_result,
                "learning_params": learning_params,
            }

        age_ms = self._snapshot_age_ms(snapshot)
        if age_ms is not None and age_ms > self.cfg.max_age_ms:
            return {
                "execute": False, "reason": "snapshot_stale",
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "meta_result": meta_result, "learning_params": learning_params,
            }

        if latency_ms_feat > 0 and age_ms is None and latency_ms_feat > self.cfg.max_age_ms:
            return {
                "execute": False, "reason": "feature_latency_stale",
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "meta_result": meta_result, "learning_params": learning_params,
            }

        if regime == "toxic":
            return {
                "execute": False, "reason": "regime_toxic",
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "meta_result": meta_result, "learning_params": learning_params,
            }

        if hidden_liquidity and confidence < self.cfg.min_confidence + 0.10:
            return {
                "execute": False, "reason": "hidden_liquidity_low_confidence",
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "meta_result": meta_result, "learning_params": learning_params,
            }

        bids, asks = _extract_levels(snapshot)
        if not bids or not asks:
            return {
                "execute": False, "side": "buy", "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "invalid_order_book",
                "meta_result": meta_result, "learning_params": learning_params,
            }

        expected_slippage_bps = self._estimate_slippage_bps(liquidity_score, spread_bps)
        if execution_slippage_bps > 0.0:
            expected_slippage_bps = max(expected_slippage_bps, execution_slippage_bps)
        expected_cost_bps = self.cfg.fee_bps + expected_slippage_bps + max(0.0, spread_bps * 0.25)

        signal_strength = _safe_float(signal_payload.get("confidence", confidence), confidence)
        volatility = _safe_float(features.get("volatility", features.get("atr_pct", 0.0)), 0.0)
        regime_multiplier = 1.2 if regime == "trend" else 0.9 if regime == "range" else 1.0
        volatility_multiplier = _clamp(1.0 + (volatility * 0.5), 0.8, 1.5)
        expected_edge_bps = (
            25.0
            * signal_strength
            * max(0.25, liquidity_score)
            * regime_multiplier
            * volatility_multiplier
        )
        expected_edge_bps *= _clamp(0.75 + 0.25 * execution_quality, 0.75, 1.0)
        if expected_edge_bps <= expected_cost_bps:
            return {
                "execute": False,
                "side": "buy" if signal == "LONG" else "sell",
                "sl": 0.0,
                "tp": 0.0,
                "position_size": 0.0,
                "reason": "edge_below_cost",
                "meta_result": meta_result,
                "learning_params": learning_params,
            }

        entry = mid if mid > 0 else ((best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0)
        if entry <= 0:
            entry = best_ask if signal == "LONG" else best_bid
        if entry <= 0:
            return {
                "execute": False, "side": "buy", "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "invalid_entry_price",
                "meta_result": meta_result, "learning_params": learning_params,
            }

        void_buffer = max(
            entry * self.cfg.void_buffer_bps / 10_000.0,
            spread * 1.5,
            entry * max(gap_proxy_bps, largest_gap_bps) / 10_000.0,
        )
        tp_buffer = max(
            entry * self.cfg.take_profit_buffer_bps / 10_000.0,
            spread,
            entry * gap_proxy_bps / 10_000.0,
        )

        if signal == "LONG":
            support = _find_liquidity_wall(bids[: min(10, len(bids))], side="bid")
            if support is None:
                support = max(0.0, best_bid - void_buffer)
            sl = min(support - void_buffer, entry - void_buffer)

            risk_per_unit = max(entry - sl, entry * 1e-6)

            resistance = _find_liquidity_wall(asks[: min(10, len(asks))], side="ask")
            if resistance is None:
                resistance = entry + self.cfg.reward_risk_min * risk_per_unit
            tp = max(resistance + tp_buffer, entry + self.cfg.reward_risk_min * risk_per_unit)
            side = "buy"

        else:
            resistance = _find_liquidity_wall(asks[: min(10, len(asks))], side="ask")
            if resistance is None:
                resistance = best_ask + void_buffer
            sl = max(resistance + void_buffer, entry + void_buffer)

            risk_per_unit = max(sl - entry, entry * 1e-6)

            support = _find_liquidity_wall(bids[: min(10, len(bids))], side="bid")
            if support is None:
                support = entry - self.cfg.reward_risk_min * risk_per_unit
            tp = min(support - tp_buffer, entry - self.cfg.reward_risk_min * risk_per_unit)
            side = "sell"

        if risk_per_unit <= 0:
            return {
                "execute": False, "side": side, "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "invalid_risk_per_unit",
                "meta_result": meta_result, "learning_params": learning_params,
            }

        meta_risk_scale = _clamp(_safe_float(meta_result.get("risk_scale", 1.0)), 0.0, 1.5)
        _meta_state = meta_result.get("meta_state") if isinstance(meta_result.get("meta_state"), dict) else {}
        meta_position_scale = _clamp(_safe_float(_meta_state.get("position_scale", 1.0)), 0.0, 1.0)
        combined_meta_scale = _clamp(meta_risk_scale * meta_position_scale, 0.0, 1.5)
        combined_learning_scale = _clamp(combined_meta_scale * learning_risk_scale, 0.0, 1.5)

        risk_budget = account_equity * self.cfg.risk_per_trade * max(0.25, liquidity_score)
        risk_budget *= _clamp(1.0 + (urgency - 0.5) * 0.6, 0.7, 1.3)
        risk_budget *= _clamp(0.85 + 0.15 * execution_quality, 0.85, 1.0)
        # NOTE: Risk scaling is applied both here and in main.py.
        risk_budget *= max(0.0, combined_learning_scale)
        raw_qty = risk_budget / risk_per_unit

        max_notional = account_equity * self.cfg.max_position_pct
        max_qty = max_notional / max(entry, 1e-12)
        qty = min(raw_qty, max_qty)

        if combined_learning_scale <= 0.0:
            return {
                "execute": False, "side": side,
                "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "meta_risk_zero", "meta_result": meta_result,
                "learning_params": learning_params,
            }

        if qty <= 0:
            return {
                "execute": False, "side": side, "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                "reason": "position_size_zero",
                "meta_result": meta_result, "learning_params": learning_params,
            }

        if signal == "LONG":
            if not (sl < entry < tp):
                return {
                    "execute": False, "side": side, "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                    "reason": "invalid_bracket_long",
                    "meta_result": meta_result, "learning_params": learning_params,
                }
        else:
            if not (tp < entry < sl):
                return {
                    "execute": False, "side": side, "sl": 0.0, "tp": 0.0, "position_size": 0.0,
                    "reason": "invalid_bracket_short",
                    "meta_result": meta_result, "learning_params": learning_params,
                }

        _meta_state = meta_result.get("meta_state") if isinstance(meta_result.get("meta_state"), dict) else {}
        order_pref = _meta_state.get("order_preference", "MARKET")
        queue_fill_probability = _safe_float(features.get("fill_prob", 0.5))
        queue_fill_probability = _clamp((queue_fill_probability + execution_fill_rate) / 2.0, 0.0, 1.0)
        adaptive_order_preference = order_pref
        if execution_quality <= 0.45 or execution_latency_ms > float(self.cfg.max_age_ms):
            adaptive_order_preference = "MARKET"
        elif execution_quality >= 0.80 and queue_fill_probability >= 0.65 and expected_slippage_bps <= 5.0:
            adaptive_order_preference = "LIMIT"
        order_type = self.choose_order_type(
            cascade_detected=_meta_state.get("cascade", {}).get("cascade_detected", False),
            urgency="high" if urgency > 0.7 else "normal",
            order_preference=adaptive_order_preference,
            queue_fill_probability=queue_fill_probability,
            expected_slippage_bps=expected_slippage_bps,
        )

        return {
            "execute": True,
            "side": side,
            "sl": float(sl),
            "tp": float(tp),
            "position_size": float(qty),
            "order_type": order_type,
            "risk_scale": float(combined_learning_scale),
            "meta_result": meta_result,
            "learning_params": learning_params,
        }

    def _estimate_slippage_bps(self, liquidity_score: float, spread_bps: float) -> float:
        base = self.cfg.slippage_bps_base
        penalty = (1.0 - _clamp(liquidity_score, 0.0, 1.0)) * 12.0
        spread_penalty = min(6.0, spread_bps * 0.15)
        return base + penalty + spread_penalty

    def _snapshot_age_ms(self, snapshot: Dict[str, Any]) -> Optional[float]:
        raw_ts = snapshot.get("ts") or snapshot.get("timestamp")
        if raw_ts is None:
            return None
        try:
            t = float(raw_ts)
        except Exception:
            return None

        now = __import__("time").time()
        if t > 1e12:
            return max(0.0, now * 1000.0 - t)
        if t > 1e9:
            return max(0.0, (now - t) * 1000.0)
        return None


def _extract_fill_info(
    result: Any,
    fallback_price: float,
    fallback_size: float,
) -> Tuple[float, float, str]:
    executed_price = _safe_float(fallback_price)
    filled_size = _safe_float(fallback_size)
    status = "filled"
    try:
        if isinstance(result, dict):
            entry_result = result.get("entry_order") if isinstance(result.get("entry_order"), dict) else result
            executed_price = _safe_float(
                entry_result.get("average")
                or entry_result.get("avgPrice")
                or entry_result.get("price")
                or fallback_price
            )
            filled_size = _safe_float(
                entry_result.get("filled")
                or entry_result.get("amount")
                or fallback_size
            )
            status = str(entry_result.get("status", "filled"))
        else:
            executed_price = _safe_float(
                getattr(result, "average", None)
                or getattr(result, "avgPrice", None)
                or fallback_price
            )
            filled_size = _safe_float(
                getattr(result, "filled", None)
                or getattr(result, "amount", None)
                or fallback_size
            )
            status = str(getattr(result, "status", "filled"))
    except Exception:
        pass
    return executed_price, filled_size, status


class ExecutionEngine:
    def __init__(
        self,
        config: ExecutionConfig | None = None,
        learning_engine: Any = None,
    ) -> None:
        api_key = os.getenv("BINANCE_API_KEY", "")
        secret = os.getenv("BINANCE_SECRET", "")

        self.exchange = ccxt.binance(
            {
                "apiKey": api_key or None,
                "secret": secret or None,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future",
                },
            }
        )
        # Strict assignment: use the provided learning_engine if given (even if falsey), else default.
        self.learning_engine = learning_engine if learning_engine is not None else LEARNING_ENGINE
        self.execution_logic = ExecutionLogic(
            config=config or ExecutionConfig(),
            learning_engine=self.learning_engine
        )
        self.current_symbol: Optional[str] = os.getenv("TRADING_SYMBOL") or None

        try:
            from meta_filter import MetaFilter as _MetaFilter
            self.meta_filter = _MetaFilter(learning_engine=self.learning_engine)
        except Exception:
            self.meta_filter = None

    def _sync_learning_engine(self):
        """Ensure all subsystems reference the latest learning_engine."""
        if getattr(self, "execution_logic", None) is not None:
            self.execution_logic.learning_engine = self.learning_engine
        if getattr(self, "meta_filter", None) is not None:
            self.meta_filter.learning_engine = self.learning_engine

    def set_learning_engine(self, learning_engine: Any):
        self.learning_engine = learning_engine
        self._sync_learning_engine()

    def get_balance(self) -> float:
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get("USDT", {})
            free = usdt.get("free")
            total = usdt.get("total")

            if free is not None:
                return float(free)
            if total is not None:
                return float(total)
            return 0.0
        except Exception:
            return 0.0

    def set_symbol(self, symbol: str) -> None:
        self.current_symbol = symbol

    def decide(
        self,
        signal_payload: Dict[str, Any],
        features_payload: Dict[str, Any],
        snapshot: Dict[str, Any],
        account_equity: float,
        meta_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.execution_logic.decide(
            signal_payload=signal_payload,
            features_payload=features_payload,
            snapshot=snapshot,
            account_equity=account_equity,
            meta_result=meta_result,
        )

    def run(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.decide(*args, **kwargs)

    def execute_trade(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.decide(*args, **kwargs)

    def compute_notional(
        self,
        equity: float,
        stop_distance: float,
        risk_scale: float,
        expected_slippage_bps: float = 0.0,
        cascade_detected: bool = False,
        risk_per_trade: float = 0.01,
        max_notional: float = 1_000_000.0,
    ) -> float:
        equity = max(0.0, _safe_float(equity))
        if equity <= 0.0:
            equity = 10_000.0
        stop_distance = max(stop_distance, 1e-9)
        risk_budget = equity * _clamp(_safe_float(risk_per_trade), 0.0001, 0.10) * _clamp(_safe_float(risk_scale), 0.0, 3.0)
        slippage_penalty = 1.0 + _clamp(_safe_float(expected_slippage_bps) / 12.0, 0.0, 1.5)
        cascade_penalty  = 1.35 if cascade_detected else 1.0
        notional = risk_budget / stop_distance / slippage_penalty / cascade_penalty
        return float(_clamp(notional, 0.0, max_notional))

    def compute_quantity(
        self,
        notional: float,
        price: float,
        quantity_step: float = 0.0001,
    ) -> float:
        price = max(price, 1e-9)
        qty   = notional / price
        qty   = math.floor(qty / max(quantity_step, 1e-9)) * quantity_step
        return float(_clamp(qty, 0.0, 1e9))

    def limit_price(
        self,
        anchor_price: float,
        side: str,
        expected_slippage_bps: float = 0.0,
    ) -> Optional[float]:
        if anchor_price <= 0:
            return None
        delta = anchor_price * min(0.0015, _safe_float(expected_slippage_bps) / 10_000.0)
        if str(side).lower() == "buy":
            return max(0.0, anchor_price - delta)
        return anchor_price + delta

    def choose_order_type(
        self,
        cascade_detected: bool = False,
        urgency: str = "normal",
        order_preference: str = "MARKET",
        queue_fill_probability: float = 0.5,
        expected_slippage_bps: float = 0.0,
    ) -> str:
        if cascade_detected or urgency in {"high", "urgent"}:
            return "MARKET"
        pref = str(order_preference).upper()
        if pref in {"LIMIT", "MARKET"}:
            return pref
        if queue_fill_probability >= 0.60 and expected_slippage_bps <= 4.0:
            return "LIMIT"
        return "MARKET"

    def place_market_order(self, symbol: str, side: str, amount: float, correlation_id: str = "") -> dict:
        cid = (correlation_id or "")[:12]
        try:
            logger.info("order_send_market cid=%s symbol=%s side=%s amount=%.8f", cid, symbol, side, amount)
            params: Dict[str, Any] = {}
            if cid:
                params["clientOrderId"] = f"{cid}_ENTRY"
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side.lower(),
                amount=amount,
                params=params,
            )
            if isinstance(order, dict):
                order["correlation_id"] = correlation_id or ""
            else:
                try:
                    setattr(order, "correlation_id", correlation_id or "")
                except Exception:
                    logger.warning("Failed to attach correlation_id to order object type=%s", type(order))

            order_id = _get_order_id(order)
            message = (
                f"🚀 TRADE EXECUTED\n\n"
                f"CID: {cid}\n"
                f"Symbol: {symbol}  \n"
                f"Side: {side.upper()}  \n"
                f"Size: {amount}  \n\n"
                f"Order ID: {order_id}"
            )
            send_telegram_message(message)
            logger.info("order_sent_market cid=%s symbol=%s side=%s amount=%.8f order_id=%s", cid, symbol, side, amount, order_id)
            return order
        except Exception as exc:
            send_telegram_message(
                f"⚠️ Trade execution failed\n\n"
                f"CID: {cid}\n"
                f"Symbol: {symbol}\n"
                f"Side: {side.upper()}\n"
                f"Size: {amount}\n"
                f"Error: {exc}"
            )
            logger.error("order_failed_market cid=%s symbol=%s side=%s amount=%.8f error=%s", cid, symbol, side, amount, exc)
            raise

    def place_limit_order(self, symbol: str, side: str, amount: float, price: float, correlation_id: str = "") -> dict:
        cid = (correlation_id or "")[:12]
        try:
            logger.info("order_send_limit cid=%s symbol=%s side=%s amount=%.8f price=%.8f", cid, symbol, side, amount, price)
            params: Dict[str, Any] = {"timeInForce": "GTC"}
            if cid:
                params["clientOrderId"] = f"{cid}_ENTRY"
            order = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side.lower(),
                amount=amount,
                price=price,
                params=params,
            )
            if isinstance(order, dict):
                order["correlation_id"] = correlation_id or ""
            else:
                try:
                    setattr(order, "correlation_id", correlation_id or "")
                except Exception:
                    logger.warning("Failed to attach correlation_id to order object type=%s", type(order))
            order_id = _get_order_id(order)
            logger.info("order_sent_limit cid=%s symbol=%s side=%s amount=%.8f price=%.8f order_id=%s", cid, symbol, side, amount, price, order_id)
            return order
        except Exception as exc:
            send_telegram_message(
                f"⚠️ Limit order failed\n\n"
                f"CID: {cid}\n"
                f"Symbol: {symbol}\n"
                f"Side: {side.upper()}\n"
                f"Size: {amount}\n"
                f"Price: {price}\n"
                f"Error: {exc}"
            )
            logger.error("order_failed_limit cid=%s symbol=%s side=%s amount=%.8f price=%.8f error=%s", cid, symbol, side, amount, price, exc)
            raise

    def place_order_with_sl_tp(
        self,
        symbol: str,
        side: str,
        amount: float,
        sl: float,
        tp: float,
        correlation_id: str = "",
    ) -> dict:
        cid = (correlation_id or "")[:12]
        try:
            if side.lower() not in {"buy", "sell"}:
                raise ValueError("side must be 'buy' or 'sell'")

            if amount <= 0:
                raise ValueError("amount must be greater than zero")

            if sl <= 0 or tp <= 0:
                raise ValueError("sl and tp must be greater than zero")

            entry_order = self.place_market_order(symbol, side, amount, correlation_id=correlation_id)
            exit_side = "sell" if side.lower() == "buy" else "buy"
            sl_order = None
            tp_order = None
            try:
                sl_order = self.exchange.create_order(
                    symbol=symbol,
                    type="STOP_MARKET",
                    side=exit_side,
                    amount=amount,
                    params={
                        "stopPrice": sl,
                        "reduceOnly": True,
                        "workingType": "MARK_PRICE",
                        "clientOrderId": f"{cid}_SL",
                    },
                )

                tp_order = self.exchange.create_order(
                    symbol=symbol,
                    type="TAKE_PROFIT_MARKET",
                    side=exit_side,
                    amount=amount,
                    params={
                        "stopPrice": tp,
                        "reduceOnly": True,
                        "workingType": "MARK_PRICE",
                        "clientOrderId": f"{cid}_TP",
                    },
                )
            except Exception as exc:
                logger.error("bracket_partial_failure cid=%s error=%s", cid, exc)
                # FUTURE: compensation hook (disabled for safety)
                # try:
                #     entry_id = _get_order_id(entry_order)
                #     if entry_id and entry_id != "N/A":
                #         self.exchange.cancel_order(entry_id, symbol=symbol)
                # except Exception as cancel_exc:
                #     logger.warning("compensation_cancel_failed cid=%s error=%s", cid, cancel_exc)
                # TODO (future): centralize all execution feedback in _execute_liquidity_trade()
                # Current placement is intentional to capture bracket-level failures early.
                le = getattr(self, "learning_engine", None)
                if le and hasattr(le, "record_execution_feedback"):
                    try:
                        le.record_execution_feedback(
                            score=0.0,
                            slippage_bps=0.0,
                            latency_ms=0.0,
                            filled_qty=0.0,
                            requested_qty=amount,
                            side=side,
                            reason="bracket_partial_failure",
                            trade_id=correlation_id or "",
                        )
                    except Exception:
                        pass
                return {
                    "entry_order": entry_order,
                    "sl_order": sl_order,
                    "tp_order": tp_order,
                    "partial_failure": True,
                    "error": str(exc),
                    "correlation_id": correlation_id or "",
                }
            if isinstance(sl_order, dict):
                sl_order["correlation_id"] = correlation_id or ""
            if isinstance(tp_order, dict):
                tp_order["correlation_id"] = correlation_id or ""

            logger.info("order_sent_bracket cid=%s symbol=%s side=%s amount=%.8f sl=%.8f tp=%.8f", cid, symbol, side, amount, sl, tp)
            return {
                "entry_order": entry_order,
                "sl_order": sl_order,
                "tp_order": tp_order,
                "correlation_id": correlation_id or "",
            }

        except Exception as exc:
            send_telegram_message(
                f"⚠️ Bracket order failed\n\n"
                f"CID: {cid}\n"
                f"Symbol: {symbol}\n"
                f"Side: {side.upper()}\n"
                f"Size: {amount}\n"
                f"SL: {sl}\n"
                f"TP: {tp}\n"
                f"Error: {exc}"
            )
            logger.error("order_failed_bracket cid=%s symbol=%s side=%s amount=%.8f error=%s", cid, symbol, side, amount, exc)
            raise

    def _execute_liquidity_trade(
        self,
        execution_signal: str,
        price: float,
        confidence: float,
        sl_price: float,
        tp_price: float,
        position_size: float,
        symbol: Optional[str] = None,
        meta_result: Optional[Dict[str, Any]] = None,
        features: Optional[Dict[str, Any]] = None,
        correlation_id: str = "",
    ) -> dict:
        cid = (correlation_id or "")[:12]
        fees, fee_type = _enforce_entry_fee_metadata(
            fees=_safe_float(features.get("fees", 0.0), 0.0) if isinstance(features, dict) else 0.0,
            fee_type=(features.get("fee_type") if isinstance(features, dict) else "pct"),
            trade_id=None,
        )
        try:
            def _is_finite_positive(x: Any) -> bool:
                v = _safe_float(x, float("nan"))
                return math.isfinite(v) and v > 0.0

            def _normalize_amount(symbol_name: str, requested: float) -> Tuple[float, Optional[str]]:
                req = _safe_float(requested, 0.0)
                if not math.isfinite(req) or req <= 0.0:
                    return 0.0, "invalid_requested_size"
                try:
                    precision_qty = _safe_float(self.exchange.amount_to_precision(symbol_name, req), req)
                    amount = max(0.0, precision_qty)
                except Exception:
                    amount = max(0.0, req)
                try:
                    market = self.exchange.market(symbol_name)
                    amount_min = _safe_float((market.get("limits") or {}).get("amount", {}).get("min"), 0.0)
                except Exception:
                    amount_min = 0.0
                if amount_min > 0.0 and amount < amount_min:
                    return 0.0, f"size_below_exchange_min:{amount}<{amount_min}"
                return amount, None

            if getattr(self, "learning_engine", None):
                try:
                    params = self.learning_engine.get_adaptive_params()
                    exec_adj = self.learning_engine.get_execution_adjustment()
                except Exception:
                    params = {}
                    exec_adj = {}

                if not exec_adj or not exec_adj.get("allow_trading", True):
                    reason = exec_adj.get("reason", "blocked") if exec_adj else "blocked"
                    return {"status": "blocked", "executed": False, "reason": reason, "fees": fees, "fee_type": fee_type}

                conf_thr = _safe_float(params.get("confidence_threshold", 0.0))
                if conf_thr > 0 and confidence < conf_thr:
                    return {"status": "filtered", "executed": False, "reason": "low_confidence", "fees": fees, "fee_type": fee_type}

            if meta_result and not meta_result.get("allow_trade", True):
                return {
                    "executed": False,
                    "reason": meta_result.get("reason", "meta_blocked"),
                    "meta_result": meta_result,
                    "fees": fees,
                    "fee_type": fee_type,
                }
            trade_symbol = symbol or self.current_symbol
            if not trade_symbol:
                return {"executed": False, "reason": "symbol_required", "fees": fees, "fee_type": fee_type}

            signal = str(execution_signal).upper().strip()
            if signal not in {"LONG", "SHORT"}:
                return {"executed": False, "reason": "invalid_signal", "fees": fees, "fee_type": fee_type}

            if not _is_finite_positive(price):
                return {"executed": False, "reason": "invalid_price", "fees": fees, "fee_type": fee_type}
            if not _is_finite_positive(position_size):
                return {"executed": False, "reason": "invalid_position_size", "fees": fees, "fee_type": fee_type}
            if not _is_finite_positive(sl_price) or not _is_finite_positive(tp_price):
                return {"executed": False, "reason": "invalid_sl_tp", "fees": fees, "fee_type": fee_type}
            if not math.isfinite(_safe_float(confidence, float("nan"))):
                return {"executed": False, "reason": "invalid_confidence", "fees": fees, "fee_type": fee_type}

            side = "buy" if signal == "LONG" else "sell"
            _features = features if isinstance(features, dict) else {}
            normalized_size, size_err = _normalize_amount(trade_symbol, position_size)
            if size_err is not None or normalized_size <= 0.0:
                return {"executed": False, "reason": size_err or "size_normalization_failed", "fees": fees, "fee_type": fee_type}

            if LIVE_TRADING:
                try:
                    result = self.place_order_with_sl_tp(
                        symbol=trade_symbol,
                        side=side,
                        amount=normalized_size,
                        sl=sl_price,
                        tp=tp_price,
                        correlation_id=correlation_id,
                    )
                except Exception as exc:
                    logger.error("execution_live_failed cid=%s symbol=%s reason=exchange_order_failed error=%s", cid, trade_symbol, exc)
                    return {
                        "executed": False,
                        "reason": "exchange_order_failed",
                        "error": str(exc),
                        "fees": fees,
                        "fee_type": fee_type,
                    }
                executed_price, filled_size, fill_status = _extract_fill_info(
                    result, fallback_price=price, fallback_size=normalized_size,
                )
                if not math.isfinite(filled_size) or filled_size <= 0.0:
                    return {
                        "executed": False,
                        "reason": "no_fill",
                        "fill_status": fill_status,
                        "fees": fees,
                        "fee_type": fee_type,
                    }

                le = getattr(self, "learning_engine", None)
                if le and hasattr(le, "record_execution_feedback"):
                    try:
                        execution_score = _features.get("execution_score", 0.0)
                        slippage = _safe_float(_features.get("slippage_bps", 0.0))
                        latency = _safe_float(_features.get("latency_ms", 0.0))
                        le.record_execution_feedback(
                            score=execution_score,
                            slippage_bps=slippage,
                            latency_ms=latency,
                            filled_qty=filled_size,
                            requested_qty=normalized_size,
                            side=side,
                            reason="post_trade_execution",
                            trade_id=correlation_id or "",
                        )
                    except Exception as exc:
                        logger.warning("[LEARNING] post_exec feedback failed: %s", exc)
                result = {
                    "executed": True,
                    "paper": False,
                    "symbol": trade_symbol,
                    "side": side,
                    "requested_size": normalized_size,
                    "filled_size": float(filled_size),
                    "executed_price": float(executed_price),
                    "fill_status": fill_status,
                    "raw_result": result,
                    "fees": fees,
                    "fee_type": fee_type,
                    "correlation_id": correlation_id or "",
                }

            else:
                simulated_size = normalized_size
                simulated_slippage_bps = _safe_float(_features.get("spread_bps", 0.0)) * 0.35
                sim_price = (
                    price * (1.0 + simulated_slippage_bps / 10_000.0)
                    if side == "buy"
                    else price * (1.0 - simulated_slippage_bps / 10_000.0)
                )
                result = {
                    "executed": True,
                    "paper": True,
                    "symbol": trade_symbol,
                    "side": side,
                    "requested_size": normalized_size,
                    "filled_size": float(simulated_size),
                    "executed_price": float(sim_price),
                    "fill_status": "filled",
                    "fees": fees,
                    "fee_type": fee_type,
                    "correlation_id": correlation_id or "",
                }
                le = getattr(self, "learning_engine", None)
                if le and hasattr(le, "record_execution_feedback"):
                    try:
                        execution_score = _features.get("execution_score", 0.0)
                        slippage = _safe_float(_features.get("slippage_bps", 0.0))
                        latency = _safe_float(_features.get("latency_ms", 0.0))
                        le.record_execution_feedback(
                            score=execution_score,
                            slippage_bps=slippage,
                            latency_ms=latency,
                            filled_qty=simulated_size,
                            requested_qty=normalized_size,
                            side=side,
                            reason="paper_execution",
                            trade_id=correlation_id or "",
                        )
                    except Exception as exc:
                        logger.warning("[LEARNING] post_exec paper feedback failed: %s", exc)

            send_telegram_message(
                f"✅ Liquidity trade placed\n\n"
                f"CID: {cid}\n"
                f"Symbol: {trade_symbol}\n"
                f"Signal: {signal}\n"
                f"Entry Price: {price}\n"
                f"Confidence: {confidence}\n"
                f"Size: {normalized_size}\n"
                f"SL: {sl_price}\n"
                f"TP: {tp_price}"
            )
            logger.info(
                "execution_trade_result cid=%s symbol=%s signal=%s side=%s requested_size=%.8f executed=%s",
                cid, trade_symbol, signal, side, normalized_size, result.get("executed")
            )
            return result
        except Exception as exc:
            send_telegram_message(
                f"⚠️ Liquidity trade execution failed\n\n"
                f"CID: {cid}\n"
                f"Signal: {execution_signal}\n"
                f"Price: {price}\n"
                f"Confidence: {confidence}\n"
                f"SL: {sl_price}\n"
                f"TP: {tp_price}\n"
                f"Size: {position_size}\n"
                f"Error: {exc}"
            )
            logger.error("execution_trade_exception cid=%s signal=%s size=%.8f error=%s", cid, execution_signal, position_size, exc)
            return {
                "executed": False,
                "reason": "execution_exception",
                "error": str(exc),
                "fees": fees,
                "fee_type": fee_type,
                "correlation_id": correlation_id or "",
            }

    def execute_decision(
        self,
        symbol: str,
        signal_payload: Dict[str, Any],
        features_payload: Dict[str, Any],
        snapshot: Dict[str, Any],
        account_equity: float,
        current_price: float,
        confidence: float,
        meta_result: Optional[Dict[str, Any]] = None,
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        cid = (correlation_id or "")[:12]
        self.set_symbol(symbol)
        decision = self.decide(
            signal_payload=signal_payload,
            features_payload=features_payload,
            snapshot=snapshot,
            account_equity=account_equity,
            meta_result=meta_result,
        )
        final_decision = dict(decision)
        final_decision["position_size"] = _safe_float(final_decision.get("position_size", 0.0), 0.0)
        try:
            assert final_decision["position_size"] >= 0.0, "position_size must be >= 0"
            if not bool(final_decision.get("execute")):
                assert final_decision["position_size"] == 0.0, "execute=False requires position_size=0"
        except AssertionError as assert_exc:
            logger.warning("[EXECUTION VALIDATION] %s", assert_exc)

        if not bool(final_decision.get("execute")):
            final_decision["position_size"] = 0.0
            final_decision["execution_status"] = "skipped"
            logger.info(
                "[EXECUTION] skipped cid=%s symbol=%s execute=%s final_position_size=%.8f reason=%s",
                cid,
                symbol,
                final_decision.get("execute"),
                final_decision["position_size"],
                final_decision.get("reason", "not_executable"),
            )
            return final_decision

        if not isinstance(features_payload, dict):
            return {
                **final_decision,
                "execute": False,
                "position_size": 0.0,
                "execution_status": "blocked",
                "reason": "invalid_features_payload",
            }

        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        # Execute exactly once for this cycle.
        signal = signal_payload.get("signal", "")
        signal_name = str(signal).strip().upper()
        if signal_name not in ("LONG", "SHORT"):
            return {
                **final_decision,
                "execute": False,
                "position_size": 0.0,
                "execution_status": "blocked",
                "reason": "invalid_signal",
            }
        price_val = _safe_float(current_price, 0.0)
        size_val = _safe_float(final_decision.get("position_size", 0.0), 0.0)
        sl_val = _safe_float(final_decision.get("sl", 0.0), 0.0)
        tp_val = _safe_float(final_decision.get("tp", 0.0), 0.0)
        if (
            price_val <= 0.0
            or size_val <= 0.0
            or sl_val <= 0.0
            or tp_val <= 0.0
        ):
            return {
                **final_decision,
                "execute": False,
                "position_size": 0.0,
                "execution_status": "failed",
                "reason": "invalid_execution_inputs",
            }
        if signal_name == "LONG":
            if not (final_decision.get("sl", 0.0) < current_price < final_decision.get("tp", 0.0)):
                return {
                    **final_decision,
                    "execute": False,
                    "position_size": 0.0,
                    "execution_status": "failed",
                    "reason": "invalid_sl_tp_long",
                }
        if signal_name == "SHORT":
            if not (final_decision.get("tp", 0.0) < current_price < final_decision.get("sl", 0.0)):
                return {
                    **final_decision,
                    "execute": False,
                    "position_size": 0.0,
                    "execution_status": "failed",
                    "reason": "invalid_sl_tp_short",
                }
        features = features_payload.get("features", features_payload)
        execution_result = self._execute_liquidity_trade(
            execution_signal=signal_name,
            price=current_price,
            confidence=confidence,
            sl_price=float(final_decision["sl"]),
            tp_price=float(final_decision["tp"]),
            position_size=float(final_decision["position_size"]),
            symbol=symbol,
            meta_result=meta_result,
            features=features,
            correlation_id=correlation_id,
        )
        if isinstance(execution_result, dict):
            if execution_result.get("status") == "blocked":
                return {
                    **final_decision,
                    "execute": False,
                    "position_size": 0.0,
                    "execution_status": "blocked",
                    "reason": execution_result.get("reason", "blocked"),
                    "execution_result": execution_result,
                }
            if execution_result.get("partial_failure"):
                return {
                    **final_decision,
                    "execute": False,
                    "position_size": 0.0,
                    "execution_status": "failed",
                    "reason": "bracket_partial_failure",
                    "execution_result": execution_result,
                }

            if execution_result.get("executed") is False:
                return {
                    **final_decision,
                    "execute": False,
                    "position_size": 0.0,
                    "execution_status": "failed",
                    "reason": execution_result.get("reason", "execution_failed"),
                    "execution_result": execution_result,
                }
            return {
                **final_decision,
                "execution_status": "success",
                "execution_result": execution_result
            }
        return final_decision

    def get_open_position(self, symbol: str) -> Dict[str, Any]:
        try:
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions or []:
                info = pos.get("info", {})
                amt = float(pos.get("contracts", 0) or info.get("positionAmt", 0))
                if abs(amt) > 0:
                    entry = float(
                        pos.get("entryPrice", 0)
                        or info.get("entryPrice", 0)
                    )
                    return {
                        "side": "LONG" if amt > 0 else "SHORT",
                        "size": abs(amt),
                        "entry_price": entry,
                        "sl": 0.0,
                        "tp": 0.0,
                    }
            return {}
        except Exception as exc:
            logger.warning("[POSITION] get_open_position failed: %s", exc)
            return {}

    def close_position(self, symbol: str) -> Dict[str, Any]:
        try:
            pos = self.get_open_position(symbol)
            if not pos:
                logger.info("[POSITION] close_position: no open position for %s", symbol)
                return {"executed": False, "reason": "no_open_position"}
            size = float(pos["size"])
            if size <= 0:
                return {"executed": False, "reason": "zero_size"}
            close_side = "sell" if pos["side"] == "LONG" else "buy"
            order = self.place_market_order(symbol, close_side, size)
            logger.info(
                "[POSITION ACTION] action=CLOSE symbol=%s side=%s size=%.4f",
                symbol, close_side, size,
            )
            return {"executed": True, "order": order, "closed_size": size}
        except Exception as exc:
            logger.error("[POSITION ACTION] close_position failed: %s", exc)
            return {"executed": False, "reason": str(exc)}

    def partial_close_position(self, symbol: str, reduce_pct: float) -> Dict[str, Any]:
        try:
            if not (0 < reduce_pct <= 1.0):
                return {"executed": False, "reason": f"invalid_reduce_pct={reduce_pct}"}
            pos = self.get_open_position(symbol)
            if not pos:
                return {"executed": False, "reason": "no_open_position"}
            full_size = float(pos["size"])
            if full_size <= 0:
                return {"executed": False, "reason": "zero_size"}
            close_size = full_size * reduce_pct
            if close_size <= 0:
                return {"executed": False, "reason": "computed_size_zero"}
            close_side = "sell" if pos["side"] == "LONG" else "buy"
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=close_size,
                params={"reduceOnly": True},
            )
            logger.info(
                "[POSITION ACTION] action=PARTIAL_CLOSE symbol=%s side=%s"
                " reduce_pct=%.2f size=%.4f",
                symbol, close_side, reduce_pct, close_size,
            )
            return {"executed": True, "order": order, "closed_size": close_size}
        except Exception as exc:
            logger.error("[POSITION ACTION] partial_close_position failed: %s", exc)
            return {"executed": False, "reason": str(exc)}

    def move_stop_loss(self, symbol: str, new_sl_price: float) -> Dict[str, Any]:
        try:
            pos = self.get_open_position(symbol)
            if not pos:
                return {"executed": False, "reason": "no_open_position"}
            if new_sl_price <= 0:
                return {"executed": False, "reason": "invalid_sl_price"}
            try:
                open_orders = self.exchange.fetch_open_orders(symbol)
                for o in open_orders:
                    if o.get("type", "").upper() in ("STOP_MARKET", "STOP", "STOP_LOSS"):
                        self.exchange.cancel_order(o["id"], symbol)
            except Exception as cancel_err:
                logger.warning(
                    "[POSITION ACTION] cancel SL orders failed (continuing): %s", cancel_err
                )
            size = float(pos["size"])
            exit_side = "sell" if pos["side"] == "LONG" else "buy"
            sl_order = self.exchange.create_order(
                symbol=symbol,
                type="STOP_MARKET",
                side=exit_side,
                amount=size,
                params={
                    "stopPrice": new_sl_price,
                    "reduceOnly": True,
                    "workingType": "MARK_PRICE",
                },
            )
            logger.info(
                "[POSITION ACTION] action=MOVE_SL symbol=%s new_sl=%.2f",
                symbol, new_sl_price,
            )
            return {"executed": True, "sl_order": sl_order, "new_sl": new_sl_price}
        except Exception as exc:
            logger.error("[POSITION ACTION] move_stop_loss failed: %s", exc)
            return {"executed": False, "reason": str(exc)}

    def trail_stop(self, symbol: str, new_sl_price: float) -> Dict[str, Any]:
        try:
            pos = self.get_open_position(symbol)
            if not pos:
                return {"executed": False, "reason": "no_open_position"}
            if new_sl_price <= 0:
                return {"executed": False, "reason": "invalid_sl_price"}
            current_sl = float(pos.get("sl", 0.0))
            side = pos["side"]
            if current_sl > 0:
                if side == "LONG" and new_sl_price <= current_sl:
                    return {
                        "executed": False,
                        "reason": f"trail_backward_LONG new={new_sl_price} current={current_sl}",
                    }
                if side == "SHORT" and new_sl_price >= current_sl:
                    return {
                        "executed": False,
                        "reason": f"trail_backward_SHORT new={new_sl_price} current={current_sl}",
                    }
            entry = float(pos.get("entry_price", 0.0))
            if entry > 0:
                if side == "LONG" and new_sl_price > entry:
                    return {"executed": False, "reason": "sl_above_entry_long"}
                if side == "SHORT" and new_sl_price < entry:
                    return {"executed": False, "reason": "sl_below_entry_short"}
            return self.move_stop_loss(symbol, new_sl_price)
        except Exception as exc:
            logger.error("[POSITION ACTION] trail_stop failed: %s", exc)
            return {"executed": False, "reason": str(exc)}

    @staticmethod
    def calculate_position_size(
        balance: float,
        risk_percent: float,
        stop_loss_distance: float,
    ) -> float:
        if stop_loss_distance <= 0:
            raise ValueError("stop_loss_distance must be greater than zero")

        risk_amount = balance * (risk_percent / 100.0)
        position_size = risk_amount / stop_loss_distance
        return max(position_size, 0.0)

    @staticmethod
    def calculate_liquidity_sl_tp(
        signal: str,
        price: float,
        market_data: Optional[dict] = None,
        **kwargs: Any,
    ) -> Tuple[float, float]:
        if market_data is None and "data" in kwargs:
            market_data = kwargs["data"]

        if not isinstance(market_data, dict):
            raise ValueError("market_data must be a dictionary")

        required_keys = ("high", "low", "liquidity_sweep")
        for key in required_keys:
            if key not in market_data:
                raise ValueError(f"Liquidity data missing: {key}")

        liquidity_sweep = market_data.get("liquidity_sweep")
        if not isinstance(liquidity_sweep, dict) or "side" not in liquidity_sweep:
            raise ValueError("Liquidity data missing: liquidity_sweep.side")

        try:
            recent_high = float(market_data["high"])
            recent_low = float(market_data["low"])
            _ = float(price)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric market data: {exc}") from exc

        if recent_high <= 0 or recent_low <= 0:
            raise ValueError("recent high/low must be greater than zero")

        if recent_high < recent_low:
            raise ValueError("recent high cannot be lower than recent low")

        sweep_side = str(liquidity_sweep.get("side", "")).upper()
        signal = str(signal).upper().strip()

        if sweep_side not in {"BUY", "SELL"}:
            raise ValueError("liquidity_sweep.side must be BUY or SELL")

        buffer = max((recent_high - recent_low) * 0.08, 15.0)

        if signal == "LONG":
            if sweep_side != "SELL":
                raise ValueError("LONG requires SELL-side liquidity sweep")
            sl = recent_low - buffer
            tp = recent_high + (buffer * 2.0)
            return float(sl), float(tp)

        if signal == "SHORT":
            if sweep_side != "BUY":
                raise ValueError("SHORT requires BUY-side liquidity sweep")
            sl = recent_high + buffer
            tp = recent_low - (buffer * 2.0)
            return float(sl), float(tp)

        raise ValueError("signal must be LONG or SHORT")


def calculate_position_size(
    balance: float,
    risk_percent: float,
    stop_loss_distance: float,
) -> float:
    return ExecutionEngine.calculate_position_size(balance, risk_percent, stop_loss_distance)


def calculate_liquidity_sl_tp(
    signal: str,
    price: float,
    data: Optional[dict] = None,
    **kwargs: Any,
) -> Tuple[float, float]:
    return ExecutionEngine.calculate_liquidity_sl_tp(signal, price, data, **kwargs)


def apply_risk_scale(decision: Dict[str, Any], risk_scale: float = 1.0) -> Dict[str, Any]:
    out = dict(decision)
    sz = _safe_float(out.get("position_size", 0.0))
    if sz and risk_scale != 1.0:
        out["position_size"] = sz * _clamp(risk_scale, 0.05, 5.0)
    return out


def build_order_plan(
    signal: str,
    mid_price: float,
    position_size: float,
    spread: float = 0.0,
    liq_result: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if liq_result:
        return list(liq_result)
    order_type = "MARKET" if signal in ("LONG", "SHORT") else "NONE"
    return [{"price": round(_safe_float(mid_price), 2),
             "size": _safe_float(position_size), "type": order_type}]


def build_execution_decision(
    signal: str,
    meta_result: Dict[str, Any],
    quality_result: Dict[str, Any],
    liq_plan: List[Dict[str, Any]],
) -> Dict[str, Any]:
    allow = (
        meta_result.get("allow_trade", True)
        and quality_result.get("execute", True)
        and signal in ("LONG", "SHORT")
    )
    size_mult = (
        _safe_float(meta_result.get("risk_scale", 1.0))
        * _safe_float(quality_result.get("position_size_multiplier", 1.0))
    )
    return {
        "execute":          allow,
        "signal":           signal,
        "size_multiplier":  _clamp(size_mult, 0.0, 3.0),
        "order_type":       quality_result.get("order_type", "MARKET"),
        "order_plan":       liq_plan,
        "quality_score":    _safe_float(quality_result.get("quality_score", 0.5)),
        "meta_reason":      meta_result.get("reason", ""),
        "quality_reason":   quality_result.get("reason", ""),
    }


def execute_with_meta(
    signal: str,
    meta_result: Dict[str, Any],
    quality_result: Dict[str, Any],
    liq_plan: List[Dict[str, Any]],
    base_size: float = 0.0,
) -> Dict[str, Any]:
    decision = build_execution_decision(signal, meta_result, quality_result, liq_plan)
    if not decision["execute"]:
        return {**decision, "final_size": 0.0}

    meta_state = meta_result.get("meta_state", {})
    hunt    = meta_state.get("liquidity_hunt", {})
    cascade = meta_state.get("cascade", {})
    stack   = meta_state.get("stack", {})

    if hunt.get("stop_hunt_detected"):
        decision = {**decision, "order_type": "MARKET"}

    if cascade.get("cascade_detected") and stack.get("stack_detected"):
        decision = {**decision, "order_type": "MARKET"}
    elif cascade.get("cascade_detected"):
        decision = {**decision, "order_type": "LIMIT"}

    final_size = _safe_float(base_size) * decision["size_multiplier"]
    if _safe_float(hunt.get("confidence", 0.0)) > 0.8:
        final_size *= 1.1
    if stack.get("stack_detected"):
        final_size *= 1.2

    return {**decision, "final_size": round(final_size, 8)}


Execution = ExecutionEngine

# ⚠️ LEGACY ONLY — DO NOT USE IN PRODUCTION PATH
# The following singleton is kept for explicit legacy fallback only.
# Do not use this in new code or any production logic.
# This will be removed in the future after code migration is complete.
# execution_engine = ExecutionLogic(learning_engine=LEARNING_ENGINE)

if __name__ == "__main__":
    engine = ExecutionEngine()
    print(engine.get_balance())
