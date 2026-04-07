# trade_lifecycle_manager.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
import hashlib
import logging
import math
import time


LOGGER = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        if not math.isfinite(parsed):
            return default
        return parsed
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    x = _safe_float(x, lo)
    lo = _safe_float(lo, lo)
    hi = _safe_float(hi, hi)
    if hi < lo:
        lo, hi = hi, lo
    return max(lo, min(hi, x))


def _safe_timestamp_ms(x: Any, default: float | None = None) -> float:
    """Return a finite, non-negative unix timestamp in milliseconds."""
    now_ms = time.time() * 1000.0
    fallback = now_ms if default is None else _safe_float(default, now_ms)
    ts = _safe_float(x, fallback)
    if ts <= 0.0:
        return max(0.0, fallback)

    # Heuristic normalization:
    # - seconds epoch: ~1e9..1e11 => convert to ms
    # - milliseconds epoch: ~1e11..1e14 => keep as-is
    # - micro/nano epochs: >=1e14 => scale down to ms
    if ts < 1e11:
        ts *= 1000.0
    elif ts >= 1e14:
        while ts >= 1e14:
            ts /= 1000.0

    if not math.isfinite(ts):
        return max(0.0, fallback)
    return max(0.0, ts)


@dataclass
class TradeLifecycleState:
    active: bool = False
    side: str = "NEUTRAL"
    entry_price: float = 0.0
    entry_time: float = 0.0
    size: float = 0.0
    bars_held: int = 0
    consecutive_losses: int = 0
    session_pnl: float = 0.0
    peak_session_pnl: float = 0.0
    last_exit_reason: str = ""
    cooldown_bars_remaining: int = 0
    flattened: bool = False


@dataclass
class LifecycleConfig:
    max_consecutive_losses: int = 3
    max_session_drawdown_pct: float = 3.0
    min_regime_confidence: float = 0.55
    toxic_vpin: float = 0.70
    toxic_spread_bps: float = 20.0
    stale_latency_ms: float = 2500.0
    min_liquidity_score: float = 0.35
    trend_min_hold_bars: int = 3
    range_max_hold_bars: int = 8
    toxic_cooldown_bars: int = 4
    normal_cooldown_bars: int = 1


class TradeLifecycleManager:
    """
    Portfolio / trade lifecycle control:
    - permits / blocks new trades
    - decides when to flatten
    - enforces session risk and cooldown
    - regime-aware holding rules
    """

    _VALID_STATES = {"IDLE", "READY", "EXECUTING", "IN_POSITION", "EXITING", "COOLDOWN"}
    _VALID_TRANSITIONS = {
        "IDLE": {"READY", "EXECUTING", "COOLDOWN", "IDLE"},
        "READY": {"EXECUTING", "COOLDOWN", "IDLE", "READY"},
        "EXECUTING": {"IN_POSITION", "COOLDOWN", "IDLE"},
        "IN_POSITION": {"EXITING", "IN_POSITION"},
        "EXITING": {"COOLDOWN", "IDLE"},
        "COOLDOWN": {"READY", "IDLE", "COOLDOWN"},
    }
    _EXECUTION_LOCK_TIMEOUT_MS = 4000.0
    _CACHE_TTL_MS = 2000.0

    def __init__(self, config: LifecycleConfig | None = None) -> None:
        self.cfg = config or LifecycleConfig()
        self.state = TradeLifecycleState()
        self._fsm_state = "IDLE"
        self._last_cycle_id = ""
        self._last_cycle_result: Dict[str, Any] | None = None
        self._last_cycle_ts_ms = 0.0
        self._last_state_version = -1
        self._last_execution_id = ""
        self._current_correlation_id = ""
        self._last_execution_ts_ms = 0.0
        self._pending_execution = False
        self._state_version = 0

    def _bump_state_version(self) -> None:
        self._state_version = int(max(0, self._state_version)) + 1

    def _set_pending_execution(self, value: bool) -> None:
        new_value = bool(value)
        if self._pending_execution != new_value:
            self._pending_execution = new_value
            self._bump_state_version()

    def _generate_correlation_id(self) -> str:
        return hashlib.sha256(
            f"{time.time_ns()}_{id(self)}_{self._state_version}".encode()
        ).hexdigest()

    def _clear_correlation_id(self) -> None:
        if self._current_correlation_id:
            LOGGER.info("lifecycle_cid_cleared cid=%s", self._current_correlation_id[:12])
        self._current_correlation_id = ""

    def _clear_execution_tracking(self) -> None:
        if self._last_execution_id:
            LOGGER.info("lifecycle_execution_id_cleared execution_id=%s", self._last_execution_id[:12])
        self._last_execution_id = ""
        self._clear_correlation_id()

    def _transition(self, new_state: str, reason: str) -> None:
        curr = self._fsm_state
        cid = self._current_correlation_id[:12] if self._current_correlation_id else ""
        if new_state not in self._VALID_STATES:
            LOGGER.warning("lifecycle_invalid_state cid=%s state=%s reason=%s", cid, new_state, reason)
            self._fsm_state = "IDLE"
            return
        allowed = self._VALID_TRANSITIONS.get(curr, set())
        if new_state not in allowed:
            LOGGER.error(
                "lifecycle_fsm_reset from=%s to=%s reason=%s active=%s pending=%s cooldown=%s",
                curr,
                new_state,
                reason,
                self.state.active,
                self._pending_execution,
                self.state.cooldown_bars_remaining,
            )
            LOGGER.warning(
                "lifecycle_illegal_transition cid=%s from=%s to=%s reason=%s resetting=IDLE",
                cid,
                curr,
                new_state,
                reason,
            )
            self._fsm_state = "IDLE"
            return
        if curr != new_state:
            LOGGER.info("lifecycle_transition cid=%s from=%s to=%s reason=%s", cid, curr, new_state, reason)
        self._fsm_state = new_state

    def _build_cycle_id(self, current_price: float, features: Dict[str, Any]) -> str:
        raw_ts = features.get("timestamp_ms", features.get("timestamp", features.get("ts", features.get("time"))))
        if raw_ts is None:
            synthetic_seed = (
                round(_safe_float(current_price), 8),
                str(features.get("regime", "unknown")).lower(),
                round(_safe_float(features.get("regime_confidence", 0.0)), 6),
                round(_safe_float(features.get("vpin", features.get("toxicity", 0.0))), 6),
                round(_safe_float(features.get("spread_bps", 0.0)), 6),
                round(_safe_float(features.get("latency_ms", 0.0)), 3),
            )
            synthetic_digest = hashlib.sha256(repr(synthetic_seed).encode("utf-8")).hexdigest()
            snapshot_ts_ms = float(int(synthetic_digest[:16], 16))
            LOGGER.debug("lifecycle_cycle_id_deterministic_ts_fallback used=true")
        else:
            snapshot_ts_ms = _safe_timestamp_ms(raw_ts)
        payload = {
            "ts_ms": int(snapshot_ts_ms),
            "price": round(_safe_float(current_price), 8),
            "regime": str(features.get("regime", "unknown")).lower(),
            "regime_conf": round(_safe_float(features.get("regime_confidence", 0.0)), 6),
            "vpin": round(_safe_float(features.get("vpin", features.get("toxicity", 0.0))), 6),
            "spread_bps": round(_safe_float(features.get("spread_bps", 0.0)), 6),
            "latency_ms": round(_safe_float(features.get("latency_ms", 0.0)), 3),
        }
        cycle_id = hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()
        LOGGER.debug("lifecycle_cycle_id_generated cycle_id=%s ts_ms=%s", cycle_id[:12], payload["ts_ms"])
        return cycle_id

    def _cooldown_remaining(self) -> int:
        return int(max(0, int(_safe_float(self.state.cooldown_bars_remaining, 0.0))))

    def on_entry(
        self,
        side: str,
        entry_price: float,
        size: float,
        features: Dict[str, Any],
    ) -> None:
        try:
            correlation_id = self.get_correlation_id()
            if not correlation_id:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                self._transition("IDLE", "on_entry_rejected")
                LOGGER.warning("lifecycle_on_entry_rejected cid=%s reason=%s", "", "missing_cid")
                LOGGER.error("lifecycle_on_entry_rejected_missing_cid")
                return
            if self._last_execution_id and correlation_id != self._last_execution_id:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                self._transition("IDLE", "on_entry_rejected")
                LOGGER.warning("lifecycle_on_entry_rejected cid=%s reason=%s", correlation_id[:12], "stale_cid")
                LOGGER.warning(
                    "lifecycle_on_entry_rejected_stale cid=%s expected_execution_id=%s",
                    correlation_id[:12],
                    self._last_execution_id[:12],
                )
                return
            cid = correlation_id[:12]
            execution_id = self._last_execution_id or correlation_id
            if self.state.active and self._last_execution_id == execution_id:
                LOGGER.info("lifecycle_on_entry_duplicate_blocked cid=%s execution_id=%s", cid, execution_id[:12])
                return

            if self._fsm_state != "EXECUTING":
                self._transition("EXECUTING", "on_entry")
            self.state.active = True
            self.state.side = str(side).upper()
            self.state.entry_price = _clamp(_safe_float(entry_price), 0.0, float("inf"))
            self.state.entry_time = _safe_timestamp_ms(
                features.get("timestamp_ms", features.get("timestamp", features.get("ts", time.time())))
            )
            self.state.size = _clamp(_safe_float(size), 0.0, float("inf"))
            self.state.bars_held = 0
            self.state.flattened = False
            self.state.last_exit_reason = ""

            regime = str(features.get("regime", "unknown")).lower()
            if regime == "toxic":
                self.state.cooldown_bars_remaining = max(0, int(self.cfg.toxic_cooldown_bars))
            else:
                self.state.cooldown_bars_remaining = max(0, int(self.cfg.normal_cooldown_bars))

            self._set_pending_execution(False)
            self._last_execution_id = execution_id
            self._current_correlation_id = correlation_id
            self._last_execution_ts_ms = _safe_timestamp_ms(self.state.entry_time)
            self._bump_state_version()
            self._transition("IN_POSITION", "entry_confirmed")
            LOGGER.info("lifecycle_execution_lock cid=%s released=entry_confirmed", cid)
            LOGGER.info(
                "lifecycle_entry cid=%s side=%s size=%.6f entry_price=%.6f execution_id=%s",
                cid,
                self.state.side,
                self.state.size,
                self.state.entry_price,
                execution_id[:12],
            )
        except Exception:
            LOGGER.exception("lifecycle_on_entry_error cid=%s", self.get_correlation_id()[:12])
            self._set_pending_execution(False)
            self._clear_execution_tracking()
            self.state.active = False
            self.state.size = 0.0
            self.state.bars_held = 0
            self.state.flattened = True
            self._transition("IDLE", "on_entry_exception")
            LOGGER.info("lifecycle_execution_lock released=entry_failed")

    def on_exit(self, pnl_pct: float, reason: str) -> None:
        try:
            cid = self.get_correlation_id()[:12]
            if not self.state.active:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                LOGGER.debug("lifecycle_on_exit_noop cid=%s reason=already_flat", cid)
                return
            self._transition("EXITING", f"on_exit:{reason}")
            pnl = _safe_float(pnl_pct, 0.0)
            self.state.active = False
            self.state.last_exit_reason = str(reason)
            self.state.session_pnl = _safe_float(self.state.session_pnl + pnl, self.state.session_pnl)
            self.state.peak_session_pnl = max(
                _safe_float(self.state.peak_session_pnl, 0.0),
                _safe_float(self.state.session_pnl, 0.0),
            )

            if pnl < 0:
                self.state.consecutive_losses = max(0, int(self.state.consecutive_losses) + 1)
            else:
                self.state.consecutive_losses = 0

            self.state.cooldown_bars_remaining = max(0, int(self.cfg.normal_cooldown_bars))
            self.state.bars_held = 0
            self.state.size = 0.0
            self.state.flattened = True
            self._set_pending_execution(False)
            self._bump_state_version()
            LOGGER.info("lifecycle_execution_lock cid=%s released=exit_complete", cid)
            self._transition("COOLDOWN", "exit_complete")
            LOGGER.info("lifecycle_exit cid=%s pnl_pct=%.6f reason=%s", cid, pnl, reason)
            self._clear_execution_tracking()
        except Exception:
            LOGGER.exception("lifecycle_on_exit_error cid=%s", self.get_correlation_id()[:12])
            self._set_pending_execution(False)
            if not self.state.active:
                self._clear_execution_tracking()
            LOGGER.info("lifecycle_execution_lock released=exit_failed")
            self._transition("IDLE", "on_exit_exception")

    def can_open_new_trade(self, features: Dict[str, Any]) -> bool:
        try:
            if self.state.active:
                self._set_pending_execution(False)
                LOGGER.info("lifecycle_blocked reason=position_active")
                return False
            if self._pending_execution:
                LOGGER.info("lifecycle_blocked reason=pending_execution")
                return False
            if self._fsm_state in {"EXECUTING", "EXITING"}:
                LOGGER.info("lifecycle_blocked reason=fsm_%s", self._fsm_state.lower())
                return False
            if self._cooldown_remaining() > 0 or self._fsm_state == "COOLDOWN":
                LOGGER.info("lifecycle_blocked reason=cooldown bars_remaining=%s", self._cooldown_remaining())
                return False

            regime = str(features.get("regime", "unknown")).lower()
            regime_conf = _safe_float(features.get("regime_confidence", 0.0))
            vpin = _safe_float(features.get("vpin", features.get("toxicity", 0.0)))
            spread_bps = _safe_float(features.get("spread_bps", 0.0))
            liquidity_score = _safe_float(features.get("liquidity_score", 0.0))
            latency_ms = _safe_float(features.get("latency_ms", 0.0))

            if regime in ("toxic", "illiquid"):
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False
            if regime_conf < self.cfg.min_regime_confidence:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False
            if vpin >= self.cfg.toxic_vpin:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False
            if spread_bps >= self.cfg.toxic_spread_bps:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False
            if liquidity_score < self.cfg.min_liquidity_score:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False
            if latency_ms >= self.cfg.stale_latency_ms:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False
            if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                return False

            self._set_pending_execution(True)
            correlation_id = self._generate_correlation_id()
            self._current_correlation_id = correlation_id
            self._last_execution_id = correlation_id
            self._last_execution_ts_ms = _safe_timestamp_ms(
                features.get("timestamp_ms", features.get("timestamp", features.get("ts", features.get("time")))),
                default=(time.time() * 1000.0),
            )
            self._transition("EXECUTING", "can_open_new_trade_true")
            LOGGER.info("lifecycle_execution_lock cid=%s engaged=trade_approved", correlation_id[:12])
            return True
        except Exception:
            LOGGER.exception("lifecycle_can_open_new_trade_error")
            self._set_pending_execution(False)
            self._clear_execution_tracking()
            LOGGER.info("lifecycle_execution_lock released=approval_error")
            return False

    def update(self, current_price: float, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call every cycle. Returns lifecycle actions:
        - HOLD
        - BLOCK_NEW_ENTRIES
        - REDUCE_RISK
        - FLATTEN
        - COOLDOWN
        """
        try:
            current_time_ms = _safe_timestamp_ms(
                features.get("timestamp_ms", features.get("timestamp", features.get("ts", features.get("time")))),
                default=(time.time() * 1000.0),
            )
            latency_ms = max(0.0, _safe_float(features.get("latency_ms", 0.0)))
            timeout_ms = _clamp(_safe_float(self._EXECUTION_LOCK_TIMEOUT_MS, 4000.0), 1000.0, 30000.0)
            adaptive_timeout_ms = max(timeout_ms, _clamp(latency_ms * 3.0, 2000.0, 15000.0))
            lock_age_ms = max(0.0, current_time_ms - _safe_timestamp_ms(self._last_execution_ts_ms, current_time_ms))
            if self._pending_execution and lock_age_ms > adaptive_timeout_ms and not self.state.active:
                self._set_pending_execution(False)
                self._clear_execution_tracking()
                LOGGER.info("lifecycle_execution_lock released=timeout")
                if self._fsm_state == "EXECUTING":
                    self._transition("READY", "execution_lock_timeout")

            cycle_id = self._build_cycle_id(current_price, features)
            cache_age_ms = max(0.0, current_time_ms - _safe_timestamp_ms(self._last_cycle_ts_ms, current_time_ms))
            if (
                self._last_cycle_id == cycle_id
                and self._last_cycle_result is not None
                and self._last_state_version == self._state_version
                and cache_age_ms <= _clamp(_safe_float(self._CACHE_TTL_MS, 2000.0), 1000.0, 5000.0)
            ):
                LOGGER.info("lifecycle_idempotent_hit cycle_id=%s", cycle_id[:12])
                return dict(self._last_cycle_result)

            regime = str(features.get("regime", "unknown")).lower()
            regime_conf = _safe_float(features.get("regime_confidence", 0.0))
            vpin = _safe_float(features.get("vpin", features.get("toxicity", 0.0)))
            spread_bps = _safe_float(features.get("spread_bps", 0.0))
            liquidity_score = _safe_float(features.get("liquidity_score", 0.0))
            urgency = _clamp(_safe_float(features.get("urgency", 0.5)), 0.0, 1.0)
            hidden_liquidity = bool(features.get("hidden_liquidity", False))
            fill_prob = _clamp(_safe_float(features.get("fill_prob", 1.0)), 0.0, 1.0)
            trade_burst = _clamp(_safe_float(features.get("trade_burst", 0.0)), 0.0, 1.0)

            self.state.cooldown_bars_remaining = self._cooldown_remaining()
            if self.state.cooldown_bars_remaining > 0:
                self.state.cooldown_bars_remaining = max(0, self.state.cooldown_bars_remaining - 1)
                self._bump_state_version()
                if self.state.cooldown_bars_remaining > 0:
                    self._transition("COOLDOWN", "cooldown_active")
                else:
                    self._transition("READY", "cooldown_complete")

            if self.state.active:
                self.state.bars_held = max(0, int(self.state.bars_held) + 1)
                self._bump_state_version()

            # Hard stop: toxic / stale / illiquid
            if vpin >= self.cfg.toxic_vpin or spread_bps >= self.cfg.toxic_spread_bps:
                result = {
                    "action": "FLATTEN",
                    "reason": "toxic_flow_or_wide_spread",
                    "block_new_entries": True,
                    "risk_scale": 0.0,
                }
                if self._pending_execution and not self.state.active:
                    self._set_pending_execution(False)
                    self._clear_execution_tracking()
                    LOGGER.info("lifecycle_execution_lock released=hard_stop_toxic")
                self._transition("EXITING", "hard_stop_toxic")
            elif latency_ms >= self.cfg.stale_latency_ms:
                result = {
                    "action": "FLATTEN",
                    "reason": "stale_market_data",
                    "block_new_entries": True,
                    "risk_scale": 0.0,
                }
                if self._pending_execution and not self.state.active:
                    self._set_pending_execution(False)
                    self._clear_execution_tracking()
                    LOGGER.info("lifecycle_execution_lock released=hard_stop_stale")
                self._transition("EXITING", "hard_stop_stale")
            elif regime in ("toxic", "illiquid"):
                result = {
                    "action": "BLOCK_NEW_ENTRIES",
                    "reason": f"regime_{regime}",
                    "block_new_entries": True,
                    "risk_scale": 0.0,
                }
                if self._pending_execution and not self.state.active:
                    self._set_pending_execution(False)
                    self._clear_execution_tracking()
                    LOGGER.info("lifecycle_execution_lock released=regime_block")
                self._transition("COOLDOWN", "regime_block")
            elif self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
                # Session kill-switch
                result = {
                    "action": "FLATTEN",
                    "reason": "max_consecutive_losses",
                    "block_new_entries": True,
                    "risk_scale": 0.0,
                }
                if self._pending_execution and not self.state.active:
                    self._set_pending_execution(False)
                    self._clear_execution_tracking()
                    LOGGER.info("lifecycle_execution_lock released=max_losses")
                self._transition("EXITING", "max_consecutive_losses")
            else:
                # Dynamic risk scale
                risk_scale = 1.0
                if regime == "trend":
                    risk_scale = 1.10
                elif regime == "range":
                    risk_scale = 0.85
                elif regime in ("accumulation", "distribution"):
                    risk_scale = 0.80

                if liquidity_score < 0.50:
                    risk_scale *= 0.80
                if hidden_liquidity and regime == "trend":
                    risk_scale *= 0.95
                if fill_prob < 0.40:
                    risk_scale *= 0.85
                if urgency > 0.80:
                    risk_scale *= 0.95
                if trade_burst > 0.70:
                    risk_scale *= 0.90

                risk_scale = _clamp(risk_scale, 0.0, 1.25)

                # Holding rules for an active position
                if self.state.active and regime == "range" and self.state.bars_held >= self.cfg.range_max_hold_bars:
                    result = {
                        "action": "FLATTEN",
                        "reason": "range_time_exit",
                        "block_new_entries": False,
                        "risk_scale": risk_scale,
                    }
                    self._transition("EXITING", "range_time_exit")
                elif self.state.active and regime == "trend" and self.state.bars_held < self.cfg.trend_min_hold_bars:
                    result = {
                        "action": "HOLD",
                        "reason": "trend_min_hold",
                        "block_new_entries": False,
                        "risk_scale": risk_scale,
                    }
                    self._transition("IN_POSITION", "trend_min_hold")
                elif self.state.active and regime_conf < 0.45 and self.state.bars_held >= 2:
                    result = {
                        "action": "REDUCE_RISK",
                        "reason": "weak_regime_confidence",
                        "block_new_entries": False,
                        "risk_scale": _clamp(risk_scale * 0.75, 0.0, 1.25),
                    }
                    self._transition("IN_POSITION", "weak_regime_confidence")
                else:
                    block_new_entries = regime in ("toxic", "illiquid") or self._cooldown_remaining() > 0
                    result = {
                        "action": "HOLD",
                        "reason": "normal_cycle",
                        "block_new_entries": block_new_entries,
                        "risk_scale": risk_scale,
                    }
                    if self.state.active:
                        self._transition("IN_POSITION", "normal_hold")
                    elif block_new_entries:
                        self._transition("COOLDOWN", "entry_blocked")
                    else:
                        self._transition("READY", "idle_ready")

            # Final invariants
            result["risk_scale"] = _clamp(_safe_float(result.get("risk_scale", 0.0)), 0.0, 1.25)
            result["block_new_entries"] = bool(result.get("block_new_entries", False))
            if self._fsm_state not in self._VALID_STATES:
                self._fsm_state = "IDLE"

            self._last_cycle_id = cycle_id
            self._last_cycle_result = dict(result)
            self._last_cycle_ts_ms = current_time_ms
            self._last_state_version = self._state_version
            LOGGER.info(
                "lifecycle_update action=%s reason=%s state=%s cooldown=%s",
                result.get("action"),
                result.get("reason"),
                self._fsm_state,
                self._cooldown_remaining(),
            )
            return dict(result)
        except Exception:
            LOGGER.exception("lifecycle_update_error")
            self._fsm_state = "IDLE"
            self._set_pending_execution(False)
            self._clear_execution_tracking()
            LOGGER.info("lifecycle_execution_lock released=update_exception")
            fallback = {
                "action": "HOLD",
                "reason": "lifecycle_safe_fallback",
                "block_new_entries": True,
                "risk_scale": 0.0,
            }
            self._last_cycle_result = dict(fallback)
            self._last_cycle_ts_ms = _safe_timestamp_ms(time.time() * 1000.0)
            self._last_state_version = self._state_version
            return fallback

    def session_guard(self) -> Dict[str, Any]:
        try:
            peak = max(0.0, _safe_float(self.state.peak_session_pnl, 0.0))
            pnl = _safe_float(self.state.session_pnl, 0.0)
            dd = max(0.0, peak - pnl)
            dd_pct = 0.0
            if peak > 0:
                dd_pct = _clamp((dd / peak) * 100.0, 0.0, float("inf"))
            if dd_pct >= self.cfg.max_session_drawdown_pct:
                self._transition("EXITING", "session_drawdown_limit")
                return {
                    "action": "FLATTEN",
                    "reason": "session_drawdown_limit",
                    "block_new_entries": True,
                }
            return {"action": "ALLOW", "reason": "session_ok", "block_new_entries": False}
        except Exception:
            LOGGER.exception("lifecycle_session_guard_error")
            return {
                "action": "FLATTEN",
                "reason": "lifecycle_safe_fallback",
                "block_new_entries": True,
            }

    def get_correlation_id(self) -> str:
        return self._current_correlation_id or ""
