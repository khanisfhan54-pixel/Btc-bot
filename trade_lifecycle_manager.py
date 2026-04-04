# trade_lifecycle_manager.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


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

    def __init__(self, config: LifecycleConfig | None = None) -> None:
        self.cfg = config or LifecycleConfig()
        self.state = TradeLifecycleState()

    def on_entry(
        self,
        side: str,
        entry_price: float,
        size: float,
        features: Dict[str, Any],
    ) -> None:
        self.state.active = True
        self.state.side = str(side).upper()
        self.state.entry_price = _safe_float(entry_price)
        self.state.entry_time = time.time()
        self.state.size = _safe_float(size)
        self.state.bars_held = 0
        self.state.flattened = False
        self.state.last_exit_reason = ""

        regime = str(features.get("regime", "unknown")).lower()
        if regime == "toxic":
            self.state.cooldown_bars_remaining = self.cfg.toxic_cooldown_bars
        else:
            self.state.cooldown_bars_remaining = self.cfg.normal_cooldown_bars

    def on_exit(self, pnl_pct: float, reason: str) -> None:
        self.state.active = False
        self.state.last_exit_reason = reason
        self.state.session_pnl += _safe_float(pnl_pct)
        self.state.peak_session_pnl = max(self.state.peak_session_pnl, self.state.session_pnl)

        if pnl_pct < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        self.state.cooldown_bars_remaining = self.cfg.normal_cooldown_bars

    def can_open_new_trade(self, features: Dict[str, Any]) -> bool:
        if self.state.cooldown_bars_remaining > 0:
            return False

        regime = str(features.get("regime", "unknown")).lower()
        regime_conf = _safe_float(features.get("regime_confidence", 0.0))
        vpin = _safe_float(features.get("vpin", features.get("toxicity", 0.0)))
        spread_bps = _safe_float(features.get("spread_bps", 0.0))
        liquidity_score = _safe_float(features.get("liquidity_score", 0.0))
        latency_ms = _safe_float(features.get("latency_ms", 0.0))

        if regime in ("toxic", "illiquid"):
            return False
        if regime_conf < self.cfg.min_regime_confidence:
            return False
        if vpin >= self.cfg.toxic_vpin:
            return False
        if spread_bps >= self.cfg.toxic_spread_bps:
            return False
        if liquidity_score < self.cfg.min_liquidity_score:
            return False
        if latency_ms >= self.cfg.stale_latency_ms:
            return False
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return False

        return True

    def update(self, current_price: float, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call every cycle. Returns lifecycle actions:
        - HOLD
        - BLOCK_NEW_ENTRIES
        - REDUCE_RISK
        - FLATTEN
        - COOLDOWN
        """
        regime = str(features.get("regime", "unknown")).lower()
        regime_conf = _safe_float(features.get("regime_confidence", 0.0))
        vpin = _safe_float(features.get("vpin", features.get("toxicity", 0.0)))
        spread_bps = _safe_float(features.get("spread_bps", 0.0))
        liquidity_score = _safe_float(features.get("liquidity_score", 0.0))
        latency_ms = _safe_float(features.get("latency_ms", 0.0))
        urgency = _safe_float(features.get("urgency", 0.5))
        hidden_liquidity = bool(features.get("hidden_liquidity", False))
        fill_prob = _safe_float(features.get("fill_prob", 1.0))
        trade_burst = _safe_float(features.get("trade_burst", 0.0))

        if self.state.cooldown_bars_remaining > 0:
            self.state.cooldown_bars_remaining -= 1

        if self.state.active:
            self.state.bars_held += 1

        # Hard stop: toxic / stale / illiquid
        if vpin >= self.cfg.toxic_vpin or spread_bps >= self.cfg.toxic_spread_bps:
            return {
                "action": "FLATTEN",
                "reason": "toxic_flow_or_wide_spread",
                "block_new_entries": True,
                "risk_scale": 0.0,
            }

        if latency_ms >= self.cfg.stale_latency_ms:
            return {
                "action": "FLATTEN",
                "reason": "stale_market_data",
                "block_new_entries": True,
                "risk_scale": 0.0,
            }

        if regime in ("toxic", "illiquid"):
            return {
                "action": "BLOCK_NEW_ENTRIES",
                "reason": f"regime_{regime}",
                "block_new_entries": True,
                "risk_scale": 0.0,
            }

        # Session kill-switch
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return {
                "action": "FLATTEN",
                "reason": "max_consecutive_losses",
                "block_new_entries": True,
                "risk_scale": 0.0,
            }

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
        if self.state.active:
            if regime == "range" and self.state.bars_held >= self.cfg.range_max_hold_bars:
                return {
                    "action": "FLATTEN",
                    "reason": "range_time_exit",
                    "block_new_entries": False,
                    "risk_scale": risk_scale,
                }

            if regime == "trend" and self.state.bars_held < self.cfg.trend_min_hold_bars:
                return {
                    "action": "HOLD",
                    "reason": "trend_min_hold",
                    "block_new_entries": False,
                    "risk_scale": risk_scale,
                }

            if regime_conf < 0.45 and self.state.bars_held >= 2:
                return {
                    "action": "REDUCE_RISK",
                    "reason": "weak_regime_confidence",
                    "block_new_entries": False,
                    "risk_scale": risk_scale * 0.75,
                }

        block_new_entries = regime in ("toxic", "illiquid") or self.state.cooldown_bars_remaining > 0
        return {
            "action": "HOLD",
            "reason": "normal_cycle",
            "block_new_entries": block_new_entries,
            "risk_scale": risk_scale,
        }

    def session_guard(self) -> Dict[str, Any]:
        dd = self.state.peak_session_pnl - self.state.session_pnl
        dd_pct = 0.0
        if self.state.peak_session_pnl > 0:
            dd_pct = dd / self.state.peak_session_pnl * 100.0
        if dd_pct >= self.cfg.max_session_drawdown_pct:
            return {
                "action": "FLATTEN",
                "reason": "session_drawdown_limit",
                "block_new_entries": True,
            }
        return {"action": "ALLOW", "reason": "session_ok", "block_new_entries": False}