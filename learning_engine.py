from __future__ import annotations

import json
import logging
import math
import os
import uuid
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LEARNING_STATE_PATH = os.path.join(os.path.dirname(__file__), "learning_state.json")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _is_valid_r(x) -> bool:
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and not (isinstance(x, float) and math.isnan(x))
    )


@dataclass
class LearningConfig:
    window_size: int = 50
    min_risk_scale: float = 0.5
    max_risk_scale: float = 1.5
    min_confidence_threshold: float = 0.45
    max_confidence_threshold: float = 0.85
    min_meta_strictness: float = 0.75
    max_meta_strictness: float = 1.35
    min_signal_bias: float = -0.20
    max_signal_bias: float = 0.20
    save_every_n: int = 5
    slippage_soft_limit_bps: float = 6.0
    fill_quality_soft_limit: float = 0.55


class LearningEngine:
    def __init__(self, state_path: str = LEARNING_STATE_PATH, config: LearningConfig | None = None) -> None:
        self.state_path = state_path
        self.cfg = config or LearningConfig()

        self.trades: Deque[Dict[str, Any]] = deque(maxlen=self.cfg.window_size)
        self.exec_feedback: Deque[Dict[str, Any]] = deque(maxlen=250)
        self.win_rate_history: Deque[float] = deque(maxlen=250)
        self.pnl_history: Deque[float] = deque(maxlen=250)
        self.closed_trades: Deque[Dict[str, Any]] = deque(maxlen=250)
        self.exit_reason_stats: Dict[str, float] = defaultdict(float)
        self.holding_time_history: Deque[float] = deque(maxlen=250)
        self.exit_regime_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0}
        )
        self.exit_quality_history: Deque[Dict[str, Any]] = deque(maxlen=250)
        self.exec_quality_scores: Deque[float] = deque(maxlen=250)

        self.signal_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0}
        )
        self.regime_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0}
        )
        self.side_exec_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "slippage_bps_sum": 0.0,
                "fill_quality_sum": 0.0,
                "count": 0.0,
                "filled_ratio_sum": 0.0,
                "latency_ms_sum": 0.0,
            }
        )

        self._open_trades: Dict[str, Dict] = {}

        self._trade_counter = 0

        self.state: Dict[str, Any] = {
            "risk_scale": 1.0,
            "confidence_threshold": 0.60,
            "meta_strictness": 1.0,
            "signal_bias": 0.0,
            "regime_bias": {},
            "last_win_rate": 0.0,
            "last_expectancy": 0.0,
            "last_avg_slippage_bps": 0.0,
            "last_avg_fill_quality": 0.0,
            "total_trades": 0,
            "last_update_reason": "init",
            "valid_r_sample_size": 0,
        }

        self._load()

    def _parse_ts(self, ts: Any) -> Optional[float]:
        if ts is None:
            return None
        try:
            if isinstance(ts, (int, float)):
                val = float(ts)
                return val / 1000.0 if val > 1e12 else val
            s = str(ts)
            if s.isdigit():
                val = float(s)
                return val / 1000.0 if val > 1e12 else val
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def _load(self) -> None:
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k in self.state.keys():
                        if k in data:
                            self.state[k] = data[k]
                    # Ensure valid_r_sample_size is present
                    if "valid_r_sample_size" not in self.state or self.state["valid_r_sample_size"] is None:
                        self.state["valid_r_sample_size"] = 0

                    for item in data.get("recent_trades", [])[-self.cfg.window_size:]:
                        if isinstance(item, dict):
                            self.trades.append(item)

                    for item in data.get("execution_feedback", [])[-250:]:
                        if isinstance(item, dict):
                            self.exec_feedback.append(item)

                    for x in data.get("win_rate_history", []):
                        self.win_rate_history.append(_safe_float(x))

                    for x in data.get("pnl_history", []):
                        self.pnl_history.append(_safe_float(x))

                    for side, stats in data.get("side_exec_stats", {}).items():
                        if isinstance(stats, dict):
                            self.side_exec_stats[side].update(
                                {
                                    "slippage_bps_sum": _safe_float(stats.get("slippage_bps_sum", 0.0)),
                                    "fill_quality_sum": _safe_float(stats.get("fill_quality_sum", 0.0)),
                                    "count": _safe_float(stats.get("count", 0.0)),
                                    "filled_ratio_sum": _safe_float(stats.get("filled_ratio_sum", 0.0)),
                                    "latency_ms_sum": _safe_float(stats.get("latency_ms_sum", 0.0)),
                                }
                            )

                    for item in data.get("closed_trades", [])[-250:]:
                        if isinstance(item, dict):
                            self.closed_trades.append(item)

                    for item in data.get("holding_time_history", [])[-250:]:
                        self.holding_time_history.append(_safe_float(item))

                    for k, v in data.get("exit_reason_stats", {}).items():
                        self.exit_reason_stats[k] = _safe_float(v)

                    for regime, stats in data.get("exit_regime_stats", {}).items():
                        if isinstance(stats, dict):
                            self.exit_regime_stats[regime].update({
                                "wins": _safe_float(stats.get("wins", 0.0)),
                                "losses": _safe_float(stats.get("losses", 0.0)),
                                "pnl": _safe_float(stats.get("pnl", 0.0)),
                                "count": _safe_float(stats.get("count", 0.0)),
                            })

                    for item in data.get("exit_quality_history", [])[-250:]:
                        if isinstance(item, dict):
                            self.exit_quality_history.append(item)

                    for x in data.get("exec_quality_scores", [])[-250:]:
                        self.exec_quality_scores.append(_safe_float(x))

                    open_trades_list = data.get("open_trades", [])
                    restored = {}
                    if isinstance(open_trades_list, dict):
                        for tid, trade in open_trades_list.items():
                            if not isinstance(trade, dict):
                                continue
                            trade_id = trade.get("trade_id")
                            if not isinstance(trade_id, str) or not trade_id:
                                continue
                            restored[trade_id] = dict(trade)
                    elif isinstance(open_trades_list, list):
                        for trade in open_trades_list:
                            if not isinstance(trade, dict):
                                continue
                            trade_id = trade.get("trade_id")
                            if not isinstance(trade_id, str) or not trade_id:
                                continue
                            restored[trade_id] = dict(trade)
                    self._open_trades = restored
        except Exception as exc:
            logger.warning("learning state load failed: %s", exc)
            self._reset_safe()

    def _reset_safe(self) -> None:
        self.trades.clear()
        self.exec_feedback.clear()
        self.win_rate_history.clear()
        self.pnl_history.clear()
        self.closed_trades.clear()
        self.exit_reason_stats.clear()
        self.holding_time_history.clear()
        self.exit_regime_stats.clear()
        self.exit_quality_history.clear()
        self.exec_quality_scores.clear()
        self.signal_stats.clear()
        self.regime_stats.clear()
        self.side_exec_stats.clear()
        self._open_trades.clear()
        self.state.update(
            {
                "risk_scale": 1.0,
                "confidence_threshold": 0.60,
                "meta_strictness": 1.0,
                "signal_bias": 0.0,
                "regime_bias": {},
                "last_win_rate": 0.0,
                "last_expectancy": 0.0,
                "last_avg_slippage_bps": 0.0,
                "last_avg_fill_quality": 0.0,
                "total_trades": 0,
                "last_update_reason": "reset_safe",
                "valid_r_sample_size": 0,
            }
        )

    def save(self) -> None:
        try:
            payload = dict(self.state)
            payload["recent_trades"] = list(self.trades)
            payload["execution_feedback"] = list(self.exec_feedback)
            payload["win_rate_history"] = list(self.win_rate_history)
            payload["pnl_history"] = list(self.pnl_history)
            payload["signal_stats"] = dict(self.signal_stats)
            payload["regime_stats"] = dict(self.regime_stats)
            payload["side_exec_stats"] = dict(self.side_exec_stats)
            payload["closed_trades"] = list(self.closed_trades)
            payload["exit_reason_stats"] = dict(self.exit_reason_stats)
            payload["holding_time_history"] = list(self.holding_time_history)
            payload["exit_regime_stats"] = dict(self.exit_regime_stats)
            payload["exit_quality_history"] = list(self.exit_quality_history)
            payload["exec_quality_scores"] = list(self.exec_quality_scores)
            payload["open_trades"] = list(self._open_trades.values())

            tmp_path = self.state_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            os.replace(tmp_path, self.state_path)
        except Exception as exc:
            logger.warning("learning state save failed: %s", exc)

    def _window_metrics(self) -> Tuple[float, float, float, float]:
        trades_list = list(self.closed_trades)
        if not trades_list:
            return 0.0, 0.0, 0.0, 0.0

        valid_r_trades = [
            t for t in trades_list if not t.get("r_unreliable", False) and _is_valid_r(t.get("r_multiple"))
        ]
        r_values = [t["r_multiple"] for t in valid_r_trades]
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r < 0]
        total = len(r_values)
        win_rate = len(wins) / total if total > 0 else 0.0
        avg_win = sum(wins) / len(wins) if len(wins) > 0 else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if len(losses) > 0 else 0.0
        expectancy = sum(r_values) / total if total > 0 else 0.0
        return win_rate, avg_win, avg_loss, expectancy

    def _avg_exec_metrics(self) -> Tuple[float, float, float]:
        if not self.exec_feedback:
            return 0.0, 0.0, 0.0
        slippage = sum(_safe_float(x.get("slippage_bps", 0.0)) for x in self.exec_feedback) / len(self.exec_feedback)
        fill_quality = sum(_safe_float(x.get("fill_quality", 0.0)) for x in self.exec_feedback) / len(self.exec_feedback)
        fill_ratio = sum(_safe_float(x.get("filled_ratio", 0.0)) for x in self.exec_feedback) / len(self.exec_feedback)
        return slippage, fill_quality, fill_ratio

    def _recompute(self) -> None:
        trades_list = list(self.closed_trades)
        self.pnl_history.clear()
        for t in trades_list:
            self.pnl_history.append(_safe_float(t.get("pnl", 0.0)))
        win_rate, avg_win, avg_loss, expectancy = self._window_metrics()
        avg_slippage_bps, avg_fill_quality, avg_fill_ratio = self._avg_exec_metrics()

        recent_closed = trades_list[-self.cfg.window_size:]
        recent_r = [
            t["r_multiple"]
            for t in recent_closed
            if not t.get("r_unreliable", False)
            and _is_valid_r(t.get("r_multiple"))
        ]

        if len(recent_r) >= 3:
            avg_r = sum(recent_r) / len(recent_r)
            r_vol = math.sqrt(
                sum((x - avg_r) ** 2 for x in recent_r) / (len(recent_r) - 1)
            )
        else:
            avg_r = 0.0
            r_vol = 0.0

        effective_sample_ratio = len(recent_r) / max(1, len(recent_closed))
        if effective_sample_ratio < 0.3:
            avg_r = 0.0
            r_vol = 0.0

        self.state["valid_r_sample_size"] = len(recent_r)

        self.state["last_win_rate"] = round(win_rate, 6)
        self.state["last_expectancy"] = round(expectancy, 6)
        self.state["last_avg_slippage_bps"] = round(avg_slippage_bps, 6)
        self.state["last_avg_fill_quality"] = round(avg_fill_quality, 6)

        risk_scale = 1.0
        conf_thr = 0.60
        strictness = 1.0
        signal_bias = 0.0

        if win_rate < 0.40:
            risk_scale *= 0.85
            conf_thr += 0.06
            strictness *= 1.15
        elif win_rate > 0.60:
            risk_scale *= 1.05
            conf_thr -= 0.03
            strictness *= 0.95

        if avg_slippage_bps > self.cfg.slippage_soft_limit_bps:
            risk_scale *= 0.90
            strictness *= 1.08
            conf_thr += 0.03
        elif avg_slippage_bps < 3.0 and avg_fill_quality > 0.70:
            risk_scale *= 1.03

        if avg_fill_quality < self.cfg.fill_quality_soft_limit:
            risk_scale *= 0.92
            strictness *= 1.06
            conf_thr += 0.02
        elif avg_fill_quality > 0.75 and win_rate > 0.55:
            risk_scale *= 1.02

        if len(recent_r) >= 3:
            if avg_r < 0:
                risk_scale *= 0.90
                strictness *= 1.08
                conf_thr += 0.02
            elif avg_r > 0 and r_vol > 1e-6:
                risk_scale *= 1.03

        long_stats = self.signal_stats.get("LONG", {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0})
        short_stats = self.signal_stats.get("SHORT", {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0})
        long_edge = long_stats["pnl"] / max(1.0, long_stats["count"])
        short_edge = short_stats["pnl"] / max(1.0, short_stats["count"])
        if long_edge > short_edge * 1.05:
            signal_bias += 0.05
        elif short_edge > long_edge * 1.05:
            signal_bias -= 0.05

        regime_bias = {}
        for regime, stats in self.regime_stats.items():
            cnt = max(1.0, stats["count"])
            regime_bias[regime] = {
                "edge": round(stats["pnl"] / cnt, 6),
                "win_rate": round(stats["wins"] / max(1.0, stats["wins"] + stats["losses"]), 6),
                "count": int(stats["count"]),
            }

        try:
            _recent_exec_q = list(self.exec_quality_scores)[-self.cfg.window_size:]
            if len(_recent_exec_q) >= 3:
                _avg_exec_q = sum(_recent_exec_q) / len(_recent_exec_q)
                self.state["last_avg_exec_quality_score"] = round(_avg_exec_q, 6)
                if _avg_exec_q < 0.40:
                    risk_scale  *= 0.92
                    conf_thr    += 0.03
                    strictness  *= 1.08
                elif _avg_exec_q > 0.75:
                    risk_scale  *= 1.02
                    conf_thr    -= 0.01
        except Exception:
            pass

        try:
            _recent_eq = list(self.exit_quality_history)[-self.cfg.window_size:]
            if len(_recent_eq) >= 3:
                _avg_eq_score = sum(
                    _safe_float(r.get("exit_quality_score", 0.5)) for r in _recent_eq
                ) / len(_recent_eq)
                _early_exits = sum(1 for r in _recent_eq if r.get("exit_classification") == "early")
                _late_exits = sum(1 for r in _recent_eq if r.get("exit_classification") == "late")
                _n_eq = len(_recent_eq)

                if _avg_eq_score < 0.35:
                    risk_scale *= 0.92
                    conf_thr += 0.03
                    strictness *= 1.08
                elif _avg_eq_score > 0.70:
                    risk_scale *= 1.03
                    conf_thr -= 0.02

                if _early_exits > _n_eq * 0.5:
                    signal_bias += 0.03

                if _late_exits > _n_eq * 0.5:
                    risk_scale *= 0.95
        except Exception:
            pass

        try:
            _bad_exit_reasons = {
                "toxic", "stale", "liquidity_kill", "session_guard", "risk_guard",
                "toxic_flow", "spread_too_wide", "illiquid_toxic_regime", "stale_signal",
            }
            _recent_closed = list(self.closed_trades)[-self.cfg.window_size:]
            _bad_exits = sum(
                1 for t in _recent_closed
                if str(t.get("reason", "")).lower() in _bad_exit_reasons
            )
            if _bad_exits >= 3:
                risk_scale *= 0.92
                strictness *= 1.08
                conf_thr += 0.02
            if self.holding_time_history:
                _avg_hold = sum(self.holding_time_history) / len(self.holding_time_history)
                if _avg_hold > 1800:
                    strictness *= 1.03
        except Exception:
            pass

        self.state["risk_scale"] = round(_clamp(risk_scale, self.cfg.min_risk_scale, self.cfg.max_risk_scale), 6)
        self.state["confidence_threshold"] = round(_clamp(conf_thr, self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold), 6)
        self.state["meta_strictness"] = round(_clamp(strictness, self.cfg.min_meta_strictness, self.cfg.max_meta_strictness), 6)
        self.state["signal_bias"] = round(_clamp(signal_bias, self.cfg.min_signal_bias, self.cfg.max_signal_bias), 6)
        self.state["regime_bias"] = regime_bias
        self.state["last_update_reason"] = "recomputed"

        for side, stats in self.side_exec_stats.items():
            if stats["count"] > 0:
                stats["avg_slippage_bps"] = stats["slippage_bps_sum"] / stats["count"]
                stats["avg_fill_quality"] = stats["fill_quality_sum"] / stats["count"]
                stats["avg_filled_ratio"] = stats["filled_ratio_sum"] / stats["count"]
                stats["avg_latency_ms"] = stats["latency_ms_sum"] / stats["count"]

    def get_adaptive_params(self) -> Dict[str, Any]:
        self._recompute()
        return {
            "risk_scale": _clamp(_safe_float(self.state.get("risk_scale", 1.0)), self.cfg.min_risk_scale, self.cfg.max_risk_scale),
            "confidence_threshold": _clamp(_safe_float(self.state.get("confidence_threshold", 0.60)), self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold),
            "meta_strictness": _clamp(_safe_float(self.state.get("meta_strictness", 1.0)), self.cfg.min_meta_strictness, self.cfg.max_meta_strictness),
            "signal_bias": _clamp(_safe_float(self.state.get("signal_bias", 0.0)), self.cfg.min_signal_bias, self.cfg.max_signal_bias),
            "last_win_rate": _safe_float(self.state.get("last_win_rate", 0.0)),
            "last_expectancy": _safe_float(self.state.get("last_expectancy", 0.0)),
            "last_avg_slippage_bps": _safe_float(self.state.get("last_avg_slippage_bps", 0.0)),
            "last_avg_fill_quality": _safe_float(self.state.get("last_avg_fill_quality", 0.0)),
            "regime_bias": self.state.get("regime_bias", {}),
        }

    def get_policy(self):
        state = getattr(self, "state", {}) or {}
        closed_trades = list(getattr(self, "closed_trades", []) or [])

        valid_r_trades = [t for t in closed_trades if not t.get("r_unreliable", False) and _is_valid_r(t.get("r_multiple"))]
        r_values = [t["r_multiple"] for t in valid_r_trades]
        total = len(r_values)
        wins = sum(1 for r in r_values if r > 0)
        win_rate = wins / total if total > 0 else 0.0
        avg_r = sum(r_values) / total if total > 0 else 0.0

        return {
            "trades_seen": len(closed_trades),
            "valid_r_trades": total,
            "win_rate": win_rate,
            "avg_r_multiple": avg_r,
            "confidence_threshold": _safe_float(state.get("confidence_threshold", 0.62), 0.62),
            "risk_scale": _safe_float(state.get("risk_scale", 1.0), 1.0),
            "meta_strictness": _safe_float(state.get("meta_strictness", 1.0), 1.0),
        }

    @property
    def win_rate(self) -> float:
        closed_trades = list(self.closed_trades)
        valid_r_trades = [t for t in closed_trades if not t.get("r_unreliable", False) and _is_valid_r(t.get("r_multiple"))]
        r_values = [t["r_multiple"] for t in valid_r_trades]
        total = len(r_values)
        wins = sum(1 for r in r_values if r > 0)
        return wins / total if total > 0 else 0.0

    def record_trade(
        self,
        signal: str,
        confidence: float,
        features: Dict[str, Any],
        trade_id: Optional[str] = None,
        entry_ts: Any = None,
    ) -> str:
        signal = str(signal or "HOLD").upper()
        confidence = _safe_float(confidence)
        features = features if isinstance(features, dict) else {}
        regime = str(features.get("regime", "unknown")).lower()
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

        if trade_id is None:
            trade_id = str(uuid.uuid4())

        trade = {
            "trade_id": trade_id,
            "created_at": now_iso,
            "entry_ts": entry_ts,
            "signal": signal,
            "confidence": confidence,
            "features": features,
            "regime": regime,
        }
        self.trades.append(trade)
        self._open_trades[trade_id] = dict(trade)
        if len(self._open_trades) == 1:
            self.state["last_trade_id"] = trade_id

        self._trade_counter += 1
        if self._trade_counter % self.cfg.save_every_n == 0:
            self.save()
        return trade_id

    def record_closed_trade(
        self,
        *,
        signal: Any = "HOLD",
        side: Any = "LONG",
        entry_price: Any = 0.0,
        exit_price: Any = 0.0,
        size: Any = 0.0,
        entry_ts: Any = None,
        exit_ts: Any = None,
        confidence: Any = 0.0,
        features_entry: Optional[Dict[str, Any]] = None,
        features_exit: Optional[Dict[str, Any]] = None,
        reason: Any = "unknown",
        exit_type: Any = "manual",
        fees: Any = 0.0,
        pnl_override: Optional[float] = None,
        mfe_pct: Optional[float] = None,
        mae_pct: Optional[float] = None,
        stop_loss: Optional[float] = None,
        trade_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        signal = str(signal or "HOLD").upper()
        side = str(side or "LONG").upper()
        reason = str(reason or "unknown").lower()
        exit_type = str(exit_type or "manual").lower()
        entry_price = _safe_float(entry_price)
        exit_price = _safe_float(exit_price)
        size = max(0.0, _safe_float(size))
        confidence = _safe_float(confidence)
        fees = max(0.0, _safe_float(fees))
        features_entry = features_entry if isinstance(features_entry, dict) else {}
        features_exit = features_exit if isinstance(features_exit, dict) else {}
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        orphan = False
        open_trade = None

        resolved_trade_id = None
        open_trades_count = len(self._open_trades)
        if trade_id is not None:
            resolved_trade_id = trade_id
            open_trade = self._open_trades.get(resolved_trade_id)
        elif features_entry.get("trade_id") is not None and isinstance(features_entry.get("trade_id"), str):
            resolved_trade_id = features_entry.get("trade_id")
            open_trade = self._open_trades.get(resolved_trade_id)
        elif features_exit.get("trade_id") is not None and isinstance(features_exit.get("trade_id"), str):
            resolved_trade_id = features_exit.get("trade_id")
            open_trade = self._open_trades.get(resolved_trade_id)
        elif open_trades_count > 0:
            possible_times = []
            if entry_ts is not None:
                parsed_entry_ts = self._parse_ts(entry_ts)
                if parsed_entry_ts is not None:
                    possible_times.append(parsed_entry_ts)
            for k in ["entry_ts", "timestamp", "ts"]:
                t = features_entry.get(k)
                if t is not None:
                    pt = self._parse_ts(t)
                    if pt is not None:
                        possible_times.append(pt)
            best_id = None
            best_diff = float("inf")
            for otid, otrade in self._open_trades.items():
                if not isinstance(otrade, dict):
                    continue
                if str(otrade.get("signal", "")).upper() != signal:
                    continue
                oentry_ts = otrade.get("entry_ts")
                if oentry_ts is None:
                    oentry_ts = otrade.get("created_at")
                oentry_ts_parsed = self._parse_ts(oentry_ts)
                for t in possible_times:
                    if oentry_ts_parsed is not None:
                        diff = abs(oentry_ts_parsed - t)
                        if diff < best_diff:
                            best_diff = diff
                            best_id = otid
            if best_id:
                resolved_trade_id = best_id
                open_trade = self._open_trades.get(resolved_trade_id)
            elif open_trades_count == 1:
                resolved_trade_id = next(iter(self._open_trades))
                open_trade = self._open_trades.get(resolved_trade_id)
        if not resolved_trade_id:
            resolved_trade_id = str(uuid.uuid4())
            orphan = True

        is_long = side in ("LONG", "BUY")
        notional = abs(entry_price * size)
        if pnl_override is not None:
            pnl = _safe_float(pnl_override)
        else:
            gross_pnl = ((exit_price - entry_price) * size) if is_long else ((entry_price - exit_price) * size)
            pnl = gross_pnl - fees
        pnl_pct = (pnl / notional) if notional > 0 else 0.0

        risk_per_trade = None
        r_method = None
        r_unreliable = False
        used_sl = None

        if features_entry.get("risk_per_trade") not in (None, ""):
            risk_per_trade = _safe_float(features_entry.get("risk_per_trade"))
            if risk_per_trade > 0:
                r_method = "explicit"
            else:
                risk_per_trade = None
        elif stop_loss is not None:
            used_sl = _safe_float(stop_loss)
            risk_per_trade = abs(entry_price - used_sl) * size
            if risk_per_trade > 0:
                r_method = "stop_loss"
            else:
                risk_per_trade = None
        elif "stop_loss" in features_entry and features_entry.get("stop_loss") not in (None, ""):
            used_sl = _safe_float(features_entry.get("stop_loss"))
            risk_per_trade = abs(entry_price - used_sl) * size
            if risk_per_trade > 0:
                r_method = "stop_loss"
            else:
                risk_per_trade = None
        elif "sl" in features_entry and features_entry.get("sl") not in (None, ""):
            used_sl = _safe_float(features_entry.get("sl"))
            risk_per_trade = abs(entry_price - used_sl) * size
            if risk_per_trade > 0:
                r_method = "stop_loss"
            else:
                risk_per_trade = None
        elif notional > 0 and size > 0 and "leverage" in features_entry:
            lev = _safe_float(features_entry.get("leverage"))
            if lev > 0:
                risk_per_trade = notional / lev
                r_method = "implied_position"
        if risk_per_trade is None or not isinstance(risk_per_trade, (float, int)) or risk_per_trade <= 0:
            r_unreliable = True
            r_multiple = None
        else:
            risk_per_trade = max(risk_per_trade, 1e-12)
            r_multiple = pnl / risk_per_trade

        result = "win" if pnl > 0 else "loss"
        entry_regime = str(features_entry.get("regime", "unknown")).lower()
        exit_regime = str(features_exit.get("regime", entry_regime)).lower()
        entry_sec = self._parse_ts(entry_ts)
        exit_sec = self._parse_ts(exit_ts)
        holding_seconds = max(0.0, exit_sec - entry_sec) if entry_sec is not None and exit_sec is not None else 0.0
        trade = {
            "signal": signal,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size": size,
            "confidence": confidence,
            "result": result,
            "pnl": round(pnl, 8),
            "pnl_pct": round(pnl_pct, 8),
            "r_multiple": round(r_multiple, 8) if r_multiple is not None else None,
            "risk_per_trade": round(risk_per_trade, 8) if risk_per_trade is not None else None,
            "r_method": r_method,
            "r_unreliable": r_unreliable,
            "reason": reason,
            "exit_type": exit_type,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "holding_seconds": round(holding_seconds, 6),
            "entry_regime": entry_regime,
            "exit_regime": exit_regime,
            "features_entry": features_entry,
            "features_exit": features_exit,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "stop_loss": stop_loss if stop_loss is not None else used_sl,
            "trade_id": resolved_trade_id,
            "created_at": now_iso,
        }
        if orphan:
            trade["orphan_trade"] = True
        self.closed_trades.append(trade)
        self.holding_time_history.append(holding_seconds)
        self.exit_reason_stats[reason] += 1.0
        self.pnl_history.append(pnl)

        # R-normalized/pure accounting: update all R stats only for valid R
        if r_multiple is not None and _is_valid_r(r_multiple):
            self.signal_stats[signal]["count"] += 1.0
            self.regime_stats[entry_regime]["count"] += 1.0
            self.signal_stats[signal]["pnl"] += r_multiple
            self.regime_stats[entry_regime]["pnl"] += r_multiple
            if result == "win":
                self.signal_stats[signal]["wins"] += 1.0
                self.regime_stats[entry_regime]["wins"] += 1.0
            else:
                self.signal_stats[signal]["losses"] += 1.0
                self.regime_stats[entry_regime]["losses"] += 1.0

        self.state["total_trades"] = int(_safe_float(self.state.get("total_trades", 0))) + 1
        if r_multiple is not None and _is_valid_r(r_multiple):
            self.state["total_r"] = _safe_float(self.state.get("total_r", 0.0)) + r_multiple
        self.state["last_closed_trade_id"] = resolved_trade_id
        self._trade_counter += 1
        if resolved_trade_id in self._open_trades:
            del self._open_trades[resolved_trade_id]
        self._recompute()
        self.win_rate_history.append(self.state.get("last_win_rate", 0.0))
        if self._trade_counter % self.cfg.save_every_n == 0:
            self.save()
        return trade

    def record_exit_quality(
        self,
        mfe_pct: float = 0.0,
        mae_pct: float = 0.0,
        exit_quality_score: float = 0.5,
        exit_classification: str = "unknown",
        holding_seconds: float = 0.0,
        reason: str = "unknown",
        regime: str = "unknown",
        exit_efficiency: float = 0.0,
        realized_pnl: float = 0.0,
        peak_pnl: float = 0.0,
        confidence: float = 0.0,
        side: str = "LONG",
    ) -> None:
        entry = {
            "mfe_pct": _safe_float(mfe_pct),
            "mae_pct": _safe_float(mae_pct),
            "exit_quality_score": _clamp(_safe_float(exit_quality_score), 0.0, 1.0),
            "exit_classification": str(exit_classification or "unknown").lower(),
            "holding_seconds": max(0.0, _safe_float(holding_seconds)),
            "reason": str(reason or "unknown").lower(),
            "regime": str(regime or "unknown").lower(),
            "exit_efficiency": _safe_float(exit_efficiency),
            "realized_pnl": _safe_float(realized_pnl),
            "peak_pnl": _safe_float(peak_pnl),
            "confidence": _safe_float(confidence),
            "side": str(side or "LONG").upper(),
        }
        self.exit_quality_history.append(entry)

        if len(self.exit_quality_history) % self.cfg.save_every_n == 0:
            self._recompute()
            self.save()

    def get_execution_adjustment(self, window: int = 30) -> Dict[str, Any]:
        recent = list(self.exec_quality_scores)[-window:]
        n = len(recent)

        if n < 3:
            return {
                "size_multiplier": 1.0,
                "allow_trading":   True,
                "avg_score":       0.5,
                "sample_count":    n,
                "reason":          "insufficient_samples",
            }

        avg = sum(recent) / n

        if avg < 0.40:
            multiplier    = 0.5
            allow_trading = False
            reason        = f"exec_quality_critical avg={avg:.3f}<0.40"
        elif avg < 0.60:
            multiplier    = 0.7
            allow_trading = True
            reason        = f"exec_quality_low avg={avg:.3f}<0.60"
        elif avg < 0.80:
            multiplier    = 1.0
            allow_trading = True
            reason        = f"exec_quality_normal avg={avg:.3f}"
        else:
            multiplier    = 1.1
            allow_trading = True
            reason        = f"exec_quality_high avg={avg:.3f}>0.80"

        return {
            "size_multiplier": round(multiplier, 6),
            "allow_trading":   allow_trading,
            "avg_score":       round(avg, 6),
            "sample_count":    n,
            "reason":          reason,
        }

    def record_execution_quality(
        self,
        score: float,
        *,
        slippage_bps: float = 0.0,
        latency_ms: float = 0.0,
        spread_bps: float = 0.0,
        side: str = "LONG",
        reason: str = "unknown",
    ) -> None:
        score = _clamp(_safe_float(score, 0.5), 0.0, 1.0)
        self.exec_quality_scores.append(score)

        n = len(self.exec_quality_scores)
        if n % self.cfg.save_every_n == 0:
            self._recompute()
            self.save()

    def summary(self) -> Dict[str, Any]:
        self._recompute()
        trades_list = list(self.closed_trades)
        if not trades_list:
            return {
                "total_trades": 0,
                "valid_r_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "avg_r_multiple": 0.0,
                "avg_slippage_bps": 0.0,
                "avg_fill_quality": 0.0,
                "avg_fill_ratio": 0.0,
                "adaptive_params": {
                    "risk_scale": self.state["risk_scale"],
                    "confidence_threshold": self.state["confidence_threshold"],
                    "meta_strictness": self.state["meta_strictness"],
                    "signal_bias": self.state["signal_bias"],
                    "last_win_rate": self.state["last_win_rate"],
                    "last_expectancy": self.state["last_expectancy"],
                    "last_avg_slippage_bps": self.state["last_avg_slippage_bps"],
                    "last_avg_fill_quality": self.state["last_avg_fill_quality"],
                    "regime_bias": self.state.get("regime_bias", {}),
                },
            }
        valid_r_trades = [t for t in trades_list if not t.get("r_unreliable", False) and _is_valid_r(t.get("r_multiple"))]
        r_values = [t["r_multiple"] for t in valid_r_trades]
        total_r = len(r_values)
        wins = sum(1 for r in r_values if r > 0)
        losses = total_r - wins
        win_rate = wins / total_r if total_r > 0 else 0.0
        avg_r_multiple = sum(r_values) / total_r if total_r > 0 else 0.0

        pnl_values = [_safe_float(t.get("pnl", 0.0)) for t in trades_list]
        avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0

        slippage_bps, fill_quality, fill_ratio = self._avg_exec_metrics()
        return {
            "total_trades": len(trades_list),
            "valid_r_trades": total_r,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 6),
            "avg_pnl": round(avg_pnl, 6),
            "avg_r_multiple": round(avg_r_multiple, 6),
            "avg_slippage_bps": round(slippage_bps, 6),
            "avg_fill_quality": round(fill_quality, 6),
            "avg_fill_ratio": round(fill_ratio, 6),
            "adaptive_params": {
                "risk_scale": self.state["risk_scale"],
                "confidence_threshold": self.state["confidence_threshold"],
                "meta_strictness": self.state["meta_strictness"],
                "signal_bias": self.state["signal_bias"],
                "last_win_rate": self.state["last_win_rate"],
                "last_expectancy": self.state["last_expectancy"],
                "last_avg_slippage_bps": self.state["last_avg_slippage_bps"],
                "last_avg_fill_quality": self.state["last_avg_fill_quality"],
                "regime_bias": self.state.get("regime_bias", {}),
            },
        }

    def update(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if args and isinstance(args[0], dict):
            kwargs = {**args[0], **kwargs}
            args = args[1:]
        return self.record_closed_trade(**{k: v for k, v in kwargs.items()
                                           if k in record_closed_trade_params})

    def observe(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.update(*args, **kwargs)

    def update_from_feedback(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.update(*args, **kwargs)


def _record_closed_trade_params() -> frozenset:
    import inspect
    import learning_engine as _self
    return frozenset(inspect.signature(LearningEngine.record_closed_trade).parameters.keys()) - {"self"}

record_closed_trade_params = _record_closed_trade_params()

Learning = LearningEngine

LEARNING_ENGINE = LearningEngine()

if __name__ == "__main__":
    le = LearningEngine()
    le.state = {"trades_seen": 10, "wins": 6, "total_r": 3.0}
    print(le.get_policy())
