from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
import threading
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
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (float, int)) and math.isfinite(float(value))


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
    window_size: int = 100
    min_rolling_samples: int = 50
    alpha: float = 0.03
    min_risk_multiplier: float = 0.5
    max_risk_multiplier: float = 2.0
    min_confidence_threshold: float = 0.3
    max_confidence_threshold: float = 0.9
    min_slippage_tolerance_bps: float = 1.0
    max_slippage_tolerance_bps: float = 20.0
    min_regime_risk: float = 0.0
    max_regime_risk: float = 1.5
    max_latency_ms: float = 5000.0
    max_mae_pct: float = 0.1
    max_mfe_pct: float = 0.1
    save_every_n: int = 5
    # Backward-compatible fields used by legacy recompute logic
    min_risk_scale: float = 0.5
    max_risk_scale: float = 1.5
    min_meta_strictness: float = 0.75
    max_meta_strictness: float = 1.35
    min_signal_bias: float = -0.20
    max_signal_bias: float = 0.20
    slippage_soft_limit_bps: float = 6.0
    fill_quality_soft_limit: float = 0.55


class LearningEngine:
    def __init__(self, state_path: str = LEARNING_STATE_PATH, config: LearningConfig | None = None) -> None:
        self.state_path = state_path
        self.cfg = config or LearningConfig()
        self._lock = threading.RLock()

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
        self._dirty = True
        self._adaptive_update_counter = 0
        self._pending_recompute = False
        self._last_recompute_ts = 0.0
        self._recompute_mutex = threading.Lock()
        self._state_version = 0
        self._save_pending = False
        self._save_force = False
        self._last_save_ts = 0.0
        self._save_thread: Optional[threading.Thread] = None
        self._recompute_thread: Optional[threading.Thread] = None
        self._pending_adaptive_update = False

        self.state: Dict[str, Any] = {
            "risk_scale": 1.0,
            "confidence_threshold": 0.60,
            "meta_strictness": 1.0,
            "signal_bias": 0.0,
            "regime_bias": {},
            "adaptive_params": {
                "risk_multiplier": 1.0,
                "slippage_tolerance_bps": 5.0,
                "min_confidence_threshold": 0.6,
                "regime_risk_map": {
                    "trend": 1.1,
                    "range": 0.8,
                    "toxic": 0.0,
                },
            },
            "last_win_rate": 0.0,
            "last_expectancy": 0.0,
            "last_avg_slippage_bps": 0.0,
            "last_avg_fill_quality": 0.0,
            "total_trades": 0,
            "last_update_reason": "init",
            "valid_r_sample_size": 0,
            "execution_rolling_stats": {
                "slippage_bps_avg": 0.0,
                "latency_ms_avg": 0.0,
                "fill_rate": 0.0,
                "execution_score_avg": 0.5,
                "samples": 0,
            },
        }

        self._load()
        self._start_recompute_worker()

    def _mark_mutated(self, *, dirty: bool = False, pending_recompute: bool = False) -> None:
        self._state_version += 1
        if dirty:
            self._dirty = True
        if pending_recompute:
            self._pending_recompute = True

    def _build_save_payload(self) -> Dict[str, Any]:
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
        return payload

    def _schedule_save(self, force: bool = False) -> None:
        with self._lock:
            self._save_pending = True
            if force:
                self._save_force = True
            thread_running = self._save_thread is not None and self._save_thread.is_alive()
            if thread_running:
                return
            thread = threading.Thread(target=self._save_worker, daemon=True)
            self._save_thread = thread
        thread.start()

    def flush(self) -> None:
        self.save()
        while True:
            with self._lock:
                thread = self._save_thread
                pending = self._save_pending
            if thread is None and not pending:
                break
            if thread is not None:
                thread.join(timeout=0.1)
            else:
                time.sleep(0.05)

    def _start_recompute_worker(self) -> None:
        with self._lock:
            if self._recompute_thread is not None and self._recompute_thread.is_alive():
                return
            thread = threading.Thread(target=self._recompute_worker_loop, daemon=True)
            self._recompute_thread = thread
        thread.start()

    def _recompute_worker_loop(self) -> None:
        while True:
            try:
                self._maybe_recompute()
                should_update_adaptive = False
                with self._lock:
                    if self._pending_adaptive_update:
                        self._pending_adaptive_update = False
                        should_update_adaptive = True
                if should_update_adaptive:
                    self._update_adaptive_params()
            except Exception as exc:
                logger.warning("recompute worker loop failed: %s", exc)
            time.sleep(0.2)

    def _save_worker(self) -> None:
        while True:
            with self._lock:
                if not self._save_pending:
                    self._save_thread = None
                    return
                force_now = self._save_force
                now = time.time()
                if not force_now and (now - self._last_save_ts) < 1.0:
                    delay = 1.0 - (now - self._last_save_ts)
                else:
                    delay = 0.0
                if delay <= 0.0:
                    self._save_pending = False
                    self._save_force = False
                    self._last_save_ts = now
                    payload = self._build_save_payload()
                else:
                    payload = None

            if payload is None:
                time.sleep(delay)
                continue

            try:
                tmp_path = self.state_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, default=str)
                os.replace(tmp_path, self.state_path)
            except Exception as exc:
                logger.warning("learning state save failed: %s", exc)

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

    def _alpha(self) -> float:
        return _clamp(_safe_float(self.cfg.alpha, 0.03), 0.01, 0.05)

    def _sync_legacy_state_from_adaptive(self) -> None:
        adaptive = self.state.get("adaptive_params", {})
        if not isinstance(adaptive, dict):
            return
        self.state["risk_scale"] = round(
            _clamp(
                _safe_float(adaptive.get("risk_multiplier", self.state.get("risk_scale", 1.0))),
                self.cfg.min_risk_multiplier,
                self.cfg.max_risk_multiplier,
            ),
            6,
        )
        self.state["confidence_threshold"] = round(
            _clamp(
                _safe_float(adaptive.get("min_confidence_threshold", self.state.get("confidence_threshold", 0.6))),
                self.cfg.min_confidence_threshold,
                self.cfg.max_confidence_threshold,
            ),
            6,
        )

    def _regime_weight(self, regime: str) -> float:
        mapping = (self.state.get("adaptive_params") or {}).get("regime_risk_map", {})
        if not isinstance(mapping, dict):
            return 1.0
        return _clamp(_safe_float(mapping.get(str(regime).lower(), 1.0), 1.0), self.cfg.min_regime_risk, self.cfg.max_regime_risk)

    def _normalize_execution_score(self, slippage_bps: float, latency_ms: float, fill_ratio: float) -> float:
        slip_n = _clamp(slippage_bps / max(self.cfg.max_slippage_tolerance_bps, 1.0), 0.0, 1.0)
        lat_n = _clamp(latency_ms / max(self.cfg.max_latency_ms, 1.0), 0.0, 1.0)
        fill_n = _clamp(fill_ratio, 0.0, 1.0)
        score = (1.0 - slip_n) * 0.40 + (1.0 - lat_n) * 0.25 + fill_n * 0.35
        return _clamp(score, 0.0, 1.0)

    def _normalize_trade_score(self, pnl_pct: float, mae: float, mfe: float) -> float:
        pnl_n = _clamp((pnl_pct + 0.03) / 0.06, 0.0, 1.0)
        mae_n = _clamp(mae / max(self.cfg.max_mae_pct, 1e-9), 0.0, 1.0)
        mfe_n = _clamp(mfe / max(self.cfg.max_mfe_pct, 1e-9), 0.0, 1.0)
        if mfe_n <= 1e-9:
            mfe_efficiency = 0.0
        else:
            mfe_efficiency = _clamp((pnl_n - mae_n * 0.5) / max(mfe_n, 1e-9), -1.0, 1.0)
            mfe_efficiency = (mfe_efficiency + 1.0) / 2.0
        score = pnl_n * 0.60 - mae_n * 0.25 + mfe_efficiency * 0.15
        return _clamp(score, 0.0, 1.0)

    def _validate_trade_record(self, trade_record: Dict[str, Any]) -> Dict[str, Any]:
        required = {
            "trade_id": str,
            "correlation_id": str,
            "entry_price": float,
            "entry_time": float,
            "side": str,
            "slippage_bps": float,
            "latency_ms": float,
            "fill_ratio": float,
            "exit_price": float,
            "pnl_pct": float,
            "holding_time": float,
            "regime": str,
            "confidence": float,
            "mfe": float,
            "mae": float,
        }
        out: Dict[str, Any] = {}
        for key, type_ in required.items():
            val = trade_record.get(key)
            if type_ is str:
                out[key] = str(val or "")
            else:
                f = _safe_float(val, 0.0)
                if not _is_finite_number(f):
                    f = 0.0
                out[key] = float(f)
        out["side"] = "LONG" if str(out["side"]).upper() not in ("SHORT",) else "SHORT"
        out["fill_ratio"] = _clamp(out["fill_ratio"], 0.0, 1.0)
        out["confidence"] = _clamp(out["confidence"], 0.0, 1.0)
        out["slippage_bps"] = _clamp(abs(out["slippage_bps"]), 0.0, 10000.0)
        out["latency_ms"] = _clamp(out["latency_ms"], 0.0, 60000.0)
        out["holding_time"] = max(0.0, out["holding_time"])
        out["entry_price"] = max(0.0, out["entry_price"])
        out["exit_price"] = max(0.0, out["exit_price"])
        return out

    def _restore_from_data(self, data: Dict[str, Any]) -> None:
        self._reset_safe()
        for k in self.state.keys():
            if k in data:
                self.state[k] = data[k]
        if "valid_r_sample_size" not in self.state or self.state["valid_r_sample_size"] is None:
            self.state["valid_r_sample_size"] = 0
        ers = self.state.get("execution_rolling_stats", {})
        if not isinstance(ers, dict):
            ers = {}
        self.state["execution_rolling_stats"] = {
            "slippage_bps_avg": _clamp(_safe_float(ers.get("slippage_bps_avg", 0.0)), 0.0, 10000.0),
            "latency_ms_avg": _clamp(_safe_float(ers.get("latency_ms_avg", 0.0)), 0.0, 60000.0),
            "fill_rate": _clamp(_safe_float(ers.get("fill_rate", 0.0)), 0.0, 1.0),
            "execution_score_avg": _clamp(_safe_float(ers.get("execution_score_avg", 0.5)), 0.0, 1.0),
            "samples": int(max(0.0, _safe_float(ers.get("samples", 0)))),
        }
        legacy_risk = _clamp(
            _safe_float(self.state.get("risk_scale", 1.0)),
            self.cfg.min_risk_multiplier,
            self.cfg.max_risk_multiplier,
        )
        legacy_conf = _clamp(
            _safe_float(self.state.get("confidence_threshold", 0.6)),
            self.cfg.min_confidence_threshold,
            self.cfg.max_confidence_threshold,
        )
        ap = self.state.get("adaptive_params", {})
        if not isinstance(ap, dict):
            ap = {
                "risk_multiplier": legacy_risk,
                "min_confidence_threshold": legacy_conf,
            }
        regime_map = ap.get("regime_risk_map", {})
        if not isinstance(regime_map, dict):
            regime_map = {}
        self.state["adaptive_params"] = {
            "risk_multiplier": _clamp(_safe_float(ap.get("risk_multiplier", legacy_risk)), self.cfg.min_risk_multiplier, self.cfg.max_risk_multiplier),
            "slippage_tolerance_bps": _clamp(_safe_float(ap.get("slippage_tolerance_bps", 5.0)), self.cfg.min_slippage_tolerance_bps, self.cfg.max_slippage_tolerance_bps),
            "min_confidence_threshold": _clamp(_safe_float(ap.get("min_confidence_threshold", legacy_conf)), self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold),
            "regime_risk_map": {
                "trend": _clamp(_safe_float(regime_map.get("trend", 1.1)), self.cfg.min_regime_risk, self.cfg.max_regime_risk),
                "range": _clamp(_safe_float(regime_map.get("range", 0.8)), self.cfg.min_regime_risk, self.cfg.max_regime_risk),
                "toxic": _clamp(_safe_float(regime_map.get("toxic", 0.0)), self.cfg.min_regime_risk, self.cfg.max_regime_risk),
            },
        }
        self._sync_legacy_state_from_adaptive()

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
        for side, stats in data.get("signal_stats", {}).items():
            if isinstance(stats, dict):
                self.signal_stats[side].update(
                    {
                        "wins": _safe_float(stats.get("wins", 0.0)),
                        "losses": _safe_float(stats.get("losses", 0.0)),
                        "pnl": _safe_float(stats.get("pnl", 0.0)),
                        "count": _safe_float(stats.get("count", 0.0)),
                    }
                )
        for regime, stats in data.get("regime_stats", {}).items():
            if isinstance(stats, dict):
                self.regime_stats[regime].update(
                    {
                        "wins": _safe_float(stats.get("wins", 0.0)),
                        "losses": _safe_float(stats.get("losses", 0.0)),
                        "pnl": _safe_float(stats.get("pnl", 0.0)),
                        "count": _safe_float(stats.get("count", 0.0)),
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
            for _, trade in open_trades_list.items():
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
        self._mark_mutated(dirty=True, pending_recompute=True)
        self._normalize_loaded_state()

    def _normalize_loaded_state(self) -> None:
        sanitized_trades = deque(maxlen=self.cfg.window_size)
        for item in list(self.trades):
            if isinstance(item, dict):
                trade_id = str(item.get("trade_id", "") or "")
                if trade_id:
                    cloned = dict(item)
                    cloned["trade_id"] = trade_id
                    cloned["created_at"] = str(cloned.get("created_at", "") or "")
                    cloned["entry_ts"] = cloned.get("entry_ts")
                    cloned["signal"] = str(cloned.get("signal", "HOLD") or "HOLD").upper()
                    cloned["confidence"] = _clamp(_safe_float(cloned.get("confidence", 0.0)), 0.0, 1.0)
                    cloned["features"] = dict(cloned.get("features", {})) if isinstance(cloned.get("features", {}), dict) else {}
                    cloned["regime"] = str(cloned.get("regime", "unknown") or "unknown").lower()
                    sanitized_trades.append(cloned)
        self.trades = sanitized_trades

        sanitized_closed = deque(maxlen=250)
        for item in list(self.closed_trades):
            if isinstance(item, dict):
                cloned = dict(item)
                cloned["trade_id"] = str(cloned.get("trade_id", "") or "")
                if not cloned["trade_id"]:
                    continue
                cloned["signal"] = str(cloned.get("signal", "HOLD") or "HOLD").upper()
                cloned["side"] = str(cloned.get("side", "LONG") or "LONG").upper()
                cloned["reason"] = str(cloned.get("reason", "unknown") or "unknown").lower()
                cloned["result"] = str(cloned.get("result", "loss") or "loss").lower()
                cloned["pnl"] = _safe_float(cloned.get("pnl", 0.0))
                cloned["pnl_pct"] = _safe_float(cloned.get("pnl_pct", 0.0))
                cloned["r_multiple"] = _safe_float(cloned.get("r_multiple", 0.0)) if _is_valid_r(cloned.get("r_multiple")) else None
                sanitized_closed.append(cloned)
        self.closed_trades = sanitized_closed

        sanitized_feedback = deque(maxlen=250)
        for item in list(self.exec_feedback):
            if not isinstance(item, dict):
                continue
            sanitized_feedback.append(
                {
                    "score": _clamp(_safe_float(item.get("score", 0.5)), 0.0, 1.0),
                    "slippage_bps": _clamp(abs(_safe_float(item.get("slippage_bps", 0.0))), 0.0, 10000.0),
                    "latency_ms": _clamp(_safe_float(item.get("latency_ms", 0.0)), 0.0, 60000.0),
                    "fill_rate": _clamp(_safe_float(item.get("fill_rate", item.get("filled_ratio", 0.0))), 0.0, 1.0),
                    "fill_quality": _clamp(_safe_float(item.get("fill_quality", 0.5)), 0.0, 1.0),
                    "filled_ratio": _clamp(_safe_float(item.get("filled_ratio", item.get("fill_rate", 0.0))), 0.0, 1.0),
                    "spread_bps": _clamp(_safe_float(item.get("spread_bps", 0.0)), 0.0, 500.0),
                    "side": str(item.get("side", "LONG")).upper(),
                    "reason": str(item.get("reason", "unknown")).lower(),
                }
            )
        self.exec_feedback = sanitized_feedback

        sanitized_exit_quality = deque(maxlen=250)
        for item in list(self.exit_quality_history):
            if not isinstance(item, dict):
                continue
            sanitized_exit_quality.append(
                {
                    "mfe_pct": _safe_float(item.get("mfe_pct", 0.0)),
                    "mae_pct": _safe_float(item.get("mae_pct", 0.0)),
                    "exit_quality_score": _clamp(_safe_float(item.get("exit_quality_score", 0.5)), 0.0, 1.0),
                    "exit_classification": str(item.get("exit_classification", "unknown")).lower(),
                    "holding_seconds": max(0.0, _safe_float(item.get("holding_seconds", 0.0))),
                    "reason": str(item.get("reason", "unknown")).lower(),
                    "regime": str(item.get("regime", "unknown")).lower(),
                    "exit_efficiency": _safe_float(item.get("exit_efficiency", 0.0)),
                    "realized_pnl": _safe_float(item.get("realized_pnl", 0.0)),
                    "peak_pnl": _safe_float(item.get("peak_pnl", 0.0)),
                    "confidence": _safe_float(item.get("confidence", 0.0)),
                    "side": str(item.get("side", "LONG")).upper(),
                }
            )
        self.exit_quality_history = sanitized_exit_quality

        sanitized_open = {}
        for key, trade in list(self._open_trades.items()):
            if not isinstance(trade, dict):
                continue
            trade_id = str(trade.get("trade_id") or key or "").strip()
            if not trade_id:
                continue
            cloned = dict(trade)
            cloned["trade_id"] = trade_id
            cloned["signal"] = str(cloned.get("signal", "HOLD") or "HOLD").upper()
            cloned["confidence"] = _clamp(_safe_float(cloned.get("confidence", 0.0)), 0.0, 1.0)
            cloned["regime"] = str(cloned.get("regime", "unknown") or "unknown").lower()
            features = cloned.get("features", {})
            cloned["features"] = dict(features) if isinstance(features, dict) else {}
            sanitized_open[trade_id] = cloned
        self._open_trades = sanitized_open

        for container in (self.signal_stats, self.regime_stats, self.exit_regime_stats):
            for key, stats in list(container.items()):
                if not isinstance(stats, dict):
                    container[key] = {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0}
                    continue
                container[key] = {
                    "wins": max(0.0, _safe_float(stats.get("wins", 0.0))),
                    "losses": max(0.0, _safe_float(stats.get("losses", 0.0))),
                    "pnl": _safe_float(stats.get("pnl", 0.0)),
                    "count": max(0.0, _safe_float(stats.get("count", 0.0))),
                }

        for side, stats in list(self.side_exec_stats.items()):
            if not isinstance(stats, dict):
                self.side_exec_stats[side] = {
                    "slippage_bps_sum": 0.0,
                    "fill_quality_sum": 0.0,
                    "count": 0.0,
                    "filled_ratio_sum": 0.0,
                    "latency_ms_sum": 0.0,
                }
                continue
            self.side_exec_stats[side] = {
                "slippage_bps_sum": max(0.0, _safe_float(stats.get("slippage_bps_sum", 0.0))),
                "fill_quality_sum": max(0.0, _safe_float(stats.get("fill_quality_sum", 0.0))),
                "count": max(0.0, _safe_float(stats.get("count", 0.0))),
                "filled_ratio_sum": max(0.0, _safe_float(stats.get("filled_ratio_sum", 0.0))),
                "latency_ms_sum": max(0.0, _safe_float(stats.get("latency_ms_sum", 0.0))),
            }

        ers = self.state.get("execution_rolling_stats", {})
        if not isinstance(ers, dict):
            ers = {}
        self.state["execution_rolling_stats"] = {
            "slippage_bps_avg": _clamp(_safe_float(ers.get("slippage_bps_avg", 0.0)), 0.0, 10000.0),
            "latency_ms_avg": _clamp(_safe_float(ers.get("latency_ms_avg", 0.0)), 0.0, 60000.0),
            "fill_rate": _clamp(_safe_float(ers.get("fill_rate", 0.0)), 0.0, 1.0),
            "execution_score_avg": _clamp(_safe_float(ers.get("execution_score_avg", 0.5)), 0.0, 1.0),
            "samples": int(max(0.0, _safe_float(ers.get("samples", 0)))),
        }

    def _ensure_fresh_state(self) -> None:
        with self._lock:
            if self._dirty:
                self._pending_recompute = True

    def _maybe_recompute(self) -> None:
        with self._lock:
            pending = self._pending_recompute
            last_ts = self._last_recompute_ts
        now = time.time()
        if not pending or (now - last_ts) < 0.5:
            return
        with self._lock:
            if not self._pending_recompute:
                return
            self._pending_recompute = False
            self._last_recompute_ts = now
        self._recompute()

    def _load(self) -> None:
        with self._lock:
            backup_path = self.state_path + ".bak"
            try:
                if os.path.exists(self.state_path):
                    with open(self.state_path, "r", encoding="utf-8") as f:
                        raw_data = f.read()
                    data = json.loads(raw_data)
                    if isinstance(data, dict):
                        self._restore_from_data(data)
                        try:
                            with open(backup_path, "w", encoding="utf-8") as dst:
                                dst.write(raw_data)
                        except Exception as backup_exc:
                            logger.warning("learning state backup failed: %s", backup_exc)
            except Exception as exc:
                logger.error("CRITICAL: state load failed, attempting backup recovery")
                logger.warning("learning state load failed: %s", exc)
                try:
                    if os.path.exists(backup_path):
                        with open(backup_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            self._restore_from_data(data)
                            return
                except Exception as backup_exc:
                    logger.warning("learning state backup recovery failed: %s", backup_exc)
                self._reset_safe()


    def _reset_safe(self) -> None:
        with self._lock:
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
                "adaptive_params": {
                    "risk_multiplier": 1.0,
                    "slippage_tolerance_bps": 5.0,
                    "min_confidence_threshold": 0.6,
                    "regime_risk_map": {"trend": 1.1, "range": 0.8, "toxic": 0.0},
                },
                "last_win_rate": 0.0,
                "last_expectancy": 0.0,
                "last_avg_slippage_bps": 0.0,
                "last_avg_fill_quality": 0.0,
                "total_trades": 0,
                "last_update_reason": "reset_safe",
                "valid_r_sample_size": 0,
                "execution_rolling_stats": {
                    "slippage_bps_avg": 0.0,
                    "latency_ms_avg": 0.0,
                    "fill_rate": 0.0,
                    "execution_score_avg": 0.5,
                    "samples": 0,
                },
                }
            )
            self._mark_mutated(dirty=True, pending_recompute=True)

    def save(self) -> None:
        self._schedule_save(force=True)

    def _window_metrics(self, trades_list: Optional[List[Dict[str, Any]]] = None) -> Tuple[float, float, float, float]:
        if trades_list is None:
            with self._lock:
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

    def _avg_exec_metrics(self, exec_feedback: Optional[List[Dict[str, Any]]] = None) -> Tuple[float, float, float]:
        if exec_feedback is None:
            with self._lock:
                exec_feedback = list(self.exec_feedback)
        if not exec_feedback:
            return 0.0, 0.0, 0.0
        slippage = sum(_safe_float(x.get("slippage_bps", 0.0)) for x in exec_feedback) / len(exec_feedback)
        fill_quality = sum(_safe_float(x.get("fill_quality", 0.0)) for x in exec_feedback) / len(exec_feedback)
        fill_ratio = sum(_safe_float(x.get("filled_ratio", 0.0)) for x in exec_feedback) / len(exec_feedback)
        return slippage, fill_quality, fill_ratio

    def _update_adaptive_params(self) -> None:
        with self._lock:
            window = max(self.cfg.min_rolling_samples, self.cfg.window_size // 2)
            recent = list(self.closed_trades)[-window:]
            if len(recent) < window:
                return
            alpha = self._alpha()
            avg_slippage = sum(_safe_float(t.get("slippage_bps", 0.0)) for t in recent) / len(recent)
            avg_latency = sum(_safe_float(t.get("latency_ms", 0.0)) for t in recent) / len(recent)
            avg_fill = sum(_safe_float(t.get("fill_ratio", 0.0)) for t in recent) / len(recent)
            avg_pnl = sum(_safe_float(t.get("pnl_pct", 0.0)) for t in recent) / len(recent)
            avg_conf_err = sum(_safe_float(t.get("confidence_error", 0.0)) for t in recent) / len(recent)
            avg_exec_score = sum(_safe_float(t.get("execution_score", 0.5)) for t in recent) / len(recent)

            ap = self.state.setdefault("adaptive_params", {})
            if not isinstance(ap, dict):
                ap = {}
                self.state["adaptive_params"] = ap
            regime_map = ap.setdefault("regime_risk_map", {"trend": 1.1, "range": 0.8, "toxic": 0.0})
            if not isinstance(regime_map, dict):
                regime_map = {"trend": 1.1, "range": 0.8, "toxic": 0.0}
                ap["regime_risk_map"] = regime_map

            before = {
                "risk_multiplier": _safe_float(ap.get("risk_multiplier", 1.0)),
                "slippage_tolerance_bps": _safe_float(ap.get("slippage_tolerance_bps", 5.0)),
                "min_confidence_threshold": _safe_float(ap.get("min_confidence_threshold", 0.6)),
                "regime_risk_map": dict(regime_map),
            }
            target_risk = _clamp(1.0 + avg_pnl * 8.0 - avg_conf_err * 0.5, self.cfg.min_risk_multiplier, self.cfg.max_risk_multiplier)
            target_slippage_tol = _clamp(avg_slippage * 1.15 + (1.0 - avg_fill) * 4.0 + avg_latency / 2000.0, self.cfg.min_slippage_tolerance_bps, self.cfg.max_slippage_tolerance_bps)
            target_conf = _clamp(0.55 + avg_conf_err * 0.45 - max(0.0, avg_pnl) * 0.25, self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold)
            target_risk = _clamp(target_risk + (avg_exec_score - 0.5) * 0.5, self.cfg.min_risk_multiplier, self.cfg.max_risk_multiplier)
            target_slippage_tol = _clamp(
                target_slippage_tol * (1.0 + (0.5 - avg_exec_score) * 0.3),
                self.cfg.min_slippage_tolerance_bps,
                self.cfg.max_slippage_tolerance_bps,
            )

            risk_delta = _clamp((target_risk - before["risk_multiplier"]) * alpha, -0.1, 0.1)
            slip_delta = _clamp((target_slippage_tol - before["slippage_tolerance_bps"]) * alpha, -1.0, 1.0)
            conf_delta = _clamp((target_conf - before["min_confidence_threshold"]) * alpha, -0.05, 0.05)
            ap["risk_multiplier"] = round(_clamp(before["risk_multiplier"] + risk_delta, self.cfg.min_risk_multiplier, self.cfg.max_risk_multiplier), 6)
            ap["slippage_tolerance_bps"] = round(_clamp(before["slippage_tolerance_bps"] + slip_delta, self.cfg.min_slippage_tolerance_bps, self.cfg.max_slippage_tolerance_bps), 6)
            ap["min_confidence_threshold"] = round(_clamp(before["min_confidence_threshold"] + conf_delta, self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold), 6)

            for regime in ("trend", "range", "toxic"):
                regime_trades = [t for t in recent if str(t.get("regime", "")).lower() == regime]
                if not regime_trades:
                    continue
                regime_pnl = sum(_safe_float(t.get("pnl_pct", 0.0)) for t in regime_trades) / len(regime_trades)
                raw_target = 0.0 if regime == "toxic" else (1.0 + regime_pnl * 6.0)
                target = _clamp(raw_target, self.cfg.min_regime_risk, self.cfg.max_regime_risk)
                prev = _safe_float(regime_map.get(regime, 1.0))
                regime_map[regime] = round(_clamp(prev * (1.0 - alpha) + target * alpha, self.cfg.min_regime_risk, self.cfg.max_regime_risk), 6)
            self._sync_legacy_state_from_adaptive()
            self._state_version += 1
            after = {
                "risk_multiplier": _safe_float(ap.get("risk_multiplier", 1.0)),
                "slippage_tolerance_bps": _safe_float(ap.get("slippage_tolerance_bps", 5.0)),
                "min_confidence_threshold": _safe_float(ap.get("min_confidence_threshold", 0.6)),
                "regime_risk_map": dict(regime_map),
            }

        logger.info(
            "[LEARNING_UPDATE] %s",
            json.dumps(
                {
                    "rolling_samples": len(recent),
                    "rolling_avg_pnl_pct": round(avg_pnl, 6),
                    "rolling_avg_slippage_bps": round(avg_slippage, 6),
                    "rolling_avg_latency_ms": round(avg_latency, 3),
                    "rolling_avg_fill_ratio": round(avg_fill, 6),
                    "rolling_avg_confidence_error": round(avg_conf_err, 6),
                    "rolling_avg_execution_score": round(avg_exec_score, 6),
                    "before": before,
                    "after": after,
                },
                sort_keys=True,
                default=str,
            ),
        )

    def _recompute(self) -> None:
        with self._recompute_mutex:
            self._recompute_impl()

    def _recompute_impl(self) -> None:
        with self._lock:
            trades_list = list(self.closed_trades)
            exec_feedback = list(self.exec_feedback)
            exec_scores = list(self.exec_quality_scores)
            exit_quality = list(self.exit_quality_history)
            holding_times = list(self.holding_time_history)
            signal_stats = {k: dict(v) for k, v in self.signal_stats.items()}
            regime_stats = {k: dict(v) for k, v in self.regime_stats.items()}
            side_exec_stats = {k: dict(v) for k, v in self.side_exec_stats.items()}
            snapshot_version = self._state_version
            exec_samples = int(
                max(
                    0.0,
                    _safe_float(
                        (self.state.get("execution_rolling_stats") or {}).get("samples", 0),
                        0.0,
                    ),
                )
            )

        pnl_snapshot = [_safe_float(t.get("pnl", 0.0)) for t in trades_list]
        win_rate, avg_win, avg_loss, expectancy = self._window_metrics(trades_list)
        avg_slippage_bps, avg_fill_quality, avg_fill_ratio = self._avg_exec_metrics(exec_feedback)

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

        risk_scale = 1.0
        conf_thr = 0.60
        strictness = 1.0
        signal_bias = 0.0

        if len(recent_r) > 0:
            if win_rate < 0.40:
                risk_scale *= 0.85
                conf_thr += 0.06
                strictness *= 1.15
            elif win_rate > 0.60:
                risk_scale *= 1.05
                conf_thr -= 0.03
                strictness *= 0.95

        if exec_samples > 0:
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

        long_stats = signal_stats.get("LONG", {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0})
        short_stats = signal_stats.get("SHORT", {"wins": 0.0, "losses": 0.0, "pnl": 0.0, "count": 0.0})
        long_edge = long_stats["pnl"] / max(1.0, long_stats["count"])
        short_edge = short_stats["pnl"] / max(1.0, short_stats["count"])
        if long_edge > short_edge * 1.05:
            signal_bias += 0.05
        elif short_edge > long_edge * 1.05:
            signal_bias -= 0.05

        regime_bias = {}
        for regime, stats in regime_stats.items():
            cnt = max(1.0, stats["count"])
            regime_bias[regime] = {
                "edge": round(stats["pnl"] / cnt, 6),
                "win_rate": round(stats["wins"] / max(1.0, stats["wins"] + stats["losses"]), 6),
                "count": int(stats["count"]),
            }

        try:
            _recent_exec_q = exec_scores[-self.cfg.window_size:]
            if len(_recent_exec_q) >= 3:
                _avg_exec_q = sum(_recent_exec_q) / len(_recent_exec_q)
                avg_exec_quality_score = round(_avg_exec_q, 6)
                if _avg_exec_q < 0.40:
                    risk_scale  *= 0.92
                    conf_thr    += 0.03
                    strictness  *= 1.08
                elif _avg_exec_q > 0.75:
                    risk_scale  *= 1.02
                    conf_thr    -= 0.01
            else:
                avg_exec_quality_score = None
        except Exception:
            avg_exec_quality_score = None

        try:
            _recent_eq = exit_quality[-self.cfg.window_size:]
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
            _recent_closed = trades_list[-self.cfg.window_size:]
            _bad_exits = sum(
                1 for t in _recent_closed
                if str(t.get("reason", "")).lower() in _bad_exit_reasons
            )
            if _bad_exits >= 3:
                risk_scale *= 0.92
                strictness *= 1.08
                conf_thr += 0.02
            if holding_times:
                _avg_hold = sum(holding_times) / len(holding_times)
                if _avg_hold > 1800:
                    strictness *= 1.03
        except Exception:
            pass

        with self._lock:
            if snapshot_version != self._state_version:
                self._pending_recompute = True
                return
            self.pnl_history.clear()
            for pnl_val in pnl_snapshot:
                self.pnl_history.append(pnl_val)
            self.state["valid_r_sample_size"] = len(recent_r)
            self.state["last_win_rate"] = round(win_rate, 6)
            self.state["last_expectancy"] = round(expectancy, 6)
            self.state["last_avg_slippage_bps"] = round(avg_slippage_bps, 6)
            self.state["last_avg_fill_quality"] = round(avg_fill_quality, 6)
            if avg_exec_quality_score is not None:
                self.state["last_avg_exec_quality_score"] = avg_exec_quality_score
            self.state["risk_scale"] = round(_clamp(risk_scale, self.cfg.min_risk_scale, self.cfg.max_risk_scale), 6)
            self.state["confidence_threshold"] = round(_clamp(conf_thr, self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold), 6)
            self.state["meta_strictness"] = round(_clamp(strictness, self.cfg.min_meta_strictness, self.cfg.max_meta_strictness), 6)
            self.state["signal_bias"] = round(_clamp(signal_bias, self.cfg.min_signal_bias, self.cfg.max_signal_bias), 6)
            self.state["regime_bias"] = regime_bias
            self.state["last_update_reason"] = "recomputed"

            for side, stats in side_exec_stats.items():
                if stats["count"] > 0:
                    stats["avg_slippage_bps"] = stats["slippage_bps_sum"] / stats["count"]
                    stats["avg_fill_quality"] = stats["fill_quality_sum"] / stats["count"]
                    stats["avg_filled_ratio"] = stats["filled_ratio_sum"] / stats["count"]
                    stats["avg_latency_ms"] = stats["latency_ms_sum"] / stats["count"]
                    self.side_exec_stats[side].update(stats)
            self._dirty = False
            self._state_version += 1




    def get_adaptive_params(self) -> Dict[str, Any]:
        self._ensure_fresh_state()
        with self._lock:
            self._sync_legacy_state_from_adaptive()
            execution_stats = self.state.get("execution_rolling_stats", {})
            if not isinstance(execution_stats, dict):
                execution_stats = {}
            execution_samples = int(max(0.0, _safe_float(execution_stats.get("samples", 0))))
            adaptive = self.state.get("adaptive_params", {})
            if not isinstance(adaptive, dict):
                adaptive = {}
            regime_map = adaptive.get("regime_risk_map", {})
            if not isinstance(regime_map, dict):
                regime_map = {}
            risk_multiplier = _clamp(_safe_float(adaptive.get("risk_multiplier", 1.0)), self.cfg.min_risk_multiplier, self.cfg.max_risk_multiplier)
            confidence_threshold = _clamp(_safe_float(adaptive.get("min_confidence_threshold", 0.60)), self.cfg.min_confidence_threshold, self.cfg.max_confidence_threshold)
            slippage_tolerance = _clamp(_safe_float(adaptive.get("slippage_tolerance_bps", 5.0)), self.cfg.min_slippage_tolerance_bps, self.cfg.max_slippage_tolerance_bps)
            return {
            "risk_scale": risk_multiplier,
            "risk_multiplier": risk_multiplier,
            "confidence_threshold": confidence_threshold,
            "min_confidence_threshold": confidence_threshold,
            "slippage_tolerance_bps": slippage_tolerance,
            "meta_strictness": _clamp(_safe_float(self.state.get("meta_strictness", 1.0)), 0.75, 1.35),
            "signal_bias": _clamp(_safe_float(self.state.get("signal_bias", 0.0)), self.cfg.min_signal_bias, self.cfg.max_signal_bias),
            "last_win_rate": _safe_float(self.state.get("last_win_rate", 0.0)),
            "last_expectancy": _safe_float(self.state.get("last_expectancy", 0.0)),
            "last_avg_slippage_bps": _safe_float(self.state.get("last_avg_slippage_bps", 0.0)),
            "last_avg_fill_quality": _safe_float(self.state.get("last_avg_fill_quality", 0.0)),
            "regime_bias": self.state.get("regime_bias", {}),
            "execution_quality": _clamp(_safe_float(execution_stats.get("execution_score_avg", 0.5)), 0.0, 1.0),
            "execution_slippage": _clamp(_safe_float(execution_stats.get("slippage_bps_avg", 0.0)), 0.0, 10000.0),
            "execution_latency": _clamp(_safe_float(execution_stats.get("latency_ms_avg", 0.0)), 0.0, 60000.0),
            "execution_fill_rate": _clamp(_safe_float(execution_stats.get("fill_rate", 0.0)), 0.0, 1.0),
            "execution_samples": execution_samples,
            "execution_feedback_samples": execution_samples,
            "regime_risk_map": {
                "trend": _clamp(_safe_float(regime_map.get("trend", 1.1)), self.cfg.min_regime_risk, self.cfg.max_regime_risk),
                "range": _clamp(_safe_float(regime_map.get("range", 0.8)), self.cfg.min_regime_risk, self.cfg.max_regime_risk),
                "toxic": _clamp(_safe_float(regime_map.get("toxic", 0.0)), self.cfg.min_regime_risk, self.cfg.max_regime_risk),
            },
            }

    def get_policy(self):
        self._ensure_fresh_state()
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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
            self._mark_mutated(dirty=True, pending_recompute=True)

            self._trade_counter += 1
            if self._trade_counter % self.cfg.save_every_n == 0:
                self._schedule_save()
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
        correlation_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._lock:
            def _normalize_trade_id(value: Any) -> Optional[str]:
                v = str(value or "").strip()
                return v if v else None

            signal = str(signal or "HOLD").upper()
            side = str(side or "LONG").upper()
            reason = str(reason or "unknown").lower()
            exit_type = str(exit_type or "manual").lower()
            entry_price = _safe_float(entry_price)
            exit_price = _safe_float(exit_price)
            size = max(0.0, _safe_float(size))
            confidence = _clamp(_safe_float(confidence), 0.0, 1.0)
            fees = max(0.0, _safe_float(fees))
            features_entry = features_entry if isinstance(features_entry, dict) else {}
            features_exit = features_exit if isinstance(features_exit, dict) else {}
            now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
            orphan = False
            normalized_trade_id = _normalize_trade_id(trade_id)
            normalized_correlation_id = str(correlation_id or features_entry.get("correlation_id") or features_exit.get("correlation_id") or "").strip()

            resolved_trade_id = None
            open_trades_count = len(self._open_trades)
            if normalized_trade_id:
                resolved_trade_id = normalized_trade_id
            elif _normalize_trade_id(features_entry.get("trade_id")):
                resolved_trade_id = _normalize_trade_id(features_entry.get("trade_id"))
            elif _normalize_trade_id(features_exit.get("trade_id")):
                resolved_trade_id = _normalize_trade_id(features_exit.get("trade_id"))
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
                    oentry_ts_parsed = self._parse_ts(otrade.get("entry_ts") or otrade.get("created_at"))
                    for t in possible_times:
                        if oentry_ts_parsed is not None:
                            diff = abs(oentry_ts_parsed - t)
                            if diff < best_diff:
                                best_diff = diff
                                best_id = otid
                if best_id:
                    resolved_trade_id = best_id
                elif open_trades_count == 1:
                    resolved_trade_id = next(iter(self._open_trades))

            if not resolved_trade_id:
                deterministic_seed = f"{signal}|{side}|{entry_price:.8f}|{exit_price:.8f}|{size:.8f}|{normalized_correlation_id}"
                resolved_trade_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, deterministic_seed))
                orphan = True

            existing = next((t for t in reversed(self.closed_trades) if t.get("trade_id") == resolved_trade_id), None)
            if existing is not None:
                return dict(existing)

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
            fill_ratio = _clamp(_safe_float(features_exit.get("fill_ratio", features_exit.get("filled_ratio", features_entry.get("fill_ratio", 1.0))), 1.0), 0.0, 1.0)
            slippage_bps = abs(_safe_float(features_exit.get("slippage_bps", features_entry.get("slippage_bps", 0.0)), 0.0))
            latency_ms = max(0.0, _safe_float(features_exit.get("latency_ms", features_entry.get("latency_ms", 0.0)), 0.0))
            mfe_val = max(0.0, _safe_float(mfe_pct, 0.0))
            mae_val = max(0.0, _safe_float(mae_pct, 0.0))
            actual_outcome = 1.0 if pnl > 0 else 0.0
            confidence_error = _clamp(abs(confidence - actual_outcome), 0.0, 1.0)
            execution_score = self._normalize_execution_score(slippage_bps, latency_ms, fill_ratio)
            trade_score = self._normalize_trade_score(pnl_pct, mae_val, mfe_val)

            resolved_correlation_id = normalized_correlation_id
            trade_record = self._validate_trade_record(
                {
                    "trade_id": resolved_trade_id,
                    "correlation_id": resolved_correlation_id,
                    "entry_price": entry_price,
                    "entry_time": float(entry_sec or 0.0),
                    "side": side,
                    "slippage_bps": slippage_bps,
                    "latency_ms": latency_ms,
                    "fill_ratio": fill_ratio,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "holding_time": holding_seconds,
                    "regime": entry_regime,
                    "confidence": confidence,
                    "mfe": mfe_val,
                    "mae": mae_val,
                }
            )

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
                "mfe_pct": mfe_val,
                "mae_pct": mae_val,
                "stop_loss": stop_loss if stop_loss is not None else used_sl,
                "trade_id": resolved_trade_id,
                "created_at": now_iso,
                "correlation_id": resolved_correlation_id,
                "slippage_bps": round(slippage_bps, 6),
                "latency_ms": round(latency_ms, 6),
                "fill_ratio": round(fill_ratio, 6),
                "execution_score": round(execution_score, 6),
                "trade_score": round(trade_score, 6),
                "confidence_error": round(confidence_error, 6),
                "trade_record": trade_record,
            }
            if orphan:
                trade["orphan_trade"] = True

            self.closed_trades.append(trade)
            self.holding_time_history.append(holding_seconds)
            self.exit_reason_stats[reason] += 1.0
            self.pnl_history.append(pnl)

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
            self._adaptive_update_counter += 1
            self._mark_mutated(dirty=True, pending_recompute=True)
            if resolved_trade_id in self._open_trades:
                del self._open_trades[resolved_trade_id]

            if self._adaptive_update_counter % 5 == 0:
                self._pending_adaptive_update = True
            self._sync_legacy_state_from_adaptive()
            logger.info(
                "[LEARNING_SCORE] %s",
                json.dumps(
                    {
                        "trade_id": resolved_trade_id,
                        "correlation_id": trade_record["correlation_id"],
                        "execution_score": trade["execution_score"],
                        "trade_score": trade["trade_score"],
                        "confidence_error": trade["confidence_error"],
                        "slippage_bps": trade_record["slippage_bps"],
                        "latency_ms": trade_record["latency_ms"],
                        "fill_ratio": trade_record["fill_ratio"],
                        "regime": trade_record["regime"],
                    },
                    sort_keys=True,
                    default=str,
                ),
            )
            self.win_rate_history.append(self.state.get("last_win_rate", 0.0))
            if self._trade_counter % self.cfg.save_every_n == 0:
                self._schedule_save()
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
        with self._lock:
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
            self._mark_mutated(dirty=True, pending_recompute=True)

            if len(self.exit_quality_history) % self.cfg.save_every_n == 0:
                self._schedule_save()

    def get_execution_adjustment(self, window: int = 30) -> Dict[str, Any]:
        with self._lock:
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
        self.record_execution_feedback(
            score=score,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            fill_rate=1.0,
            spread_bps=spread_bps,
            side=side,
            reason=reason,
        )

    def record_execution_feedback(
        self,
        score: float = 0.5,
        *,
        slippage_bps: float = 0.0,
        latency_ms: float = 0.0,
        fill_rate: Optional[float] = None,
        spread_bps: float = 0.0,
        side: str = "LONG",
        reason: str = "unknown",
        filled_qty: Optional[float] = None,
        requested_qty: Optional[float] = None,
        fill_quality: Optional[float] = None,
        filled_ratio: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        with self._lock:
            score = _clamp(_safe_float(score, 0.5), 0.0, 1.0)
            slippage_bps = _clamp(abs(_safe_float(slippage_bps, 0.0)), 0.0, 500.0)
            latency_ms = _clamp(_safe_float(latency_ms, 0.0), 0.0, 10000.0)
            side = str(side or "LONG").upper()
            reason = str(reason or "unknown").lower()
            spread_bps = _clamp(_safe_float(spread_bps, 0.0), 0.0, 500.0)

            computed_fill_rate: Optional[float] = None
            req = _safe_float(requested_qty, 0.0)
            if req > 0 and filled_qty is not None:
                computed_fill_rate = _safe_float(filled_qty, 0.0) / req
            elif fill_rate is not None:
                computed_fill_rate = _safe_float(fill_rate, 0.0)
            elif filled_ratio is not None:
                computed_fill_rate = _safe_float(filled_ratio, 0.0)
            fill_rate_clamped = _clamp(_safe_float(computed_fill_rate, 0.0), 0.0, 1.0)

            fill_quality_value = _clamp(_safe_float(fill_quality, score), 0.0, 1.0)
            feedback = {
                "score": score,
                "slippage_bps": slippage_bps,
                "latency_ms": latency_ms,
                "fill_rate": fill_rate_clamped,
                "fill_quality": fill_quality_value,
                "filled_ratio": fill_rate_clamped,
                "spread_bps": spread_bps,
                "side": side,
                "reason": reason,
            }
            self.exec_feedback.append(feedback)
            self.exec_quality_scores.append(score)

            stats = self.state.setdefault("execution_rolling_stats", {})
            if not isinstance(stats, dict):
                stats = {}
                self.state["execution_rolling_stats"] = stats
            prev_samples = int(max(0.0, _safe_float(stats.get("samples", 0))))
            stats["samples"] = prev_samples + 1
            samples = prev_samples
            prev_slip = _clamp(_safe_float(stats.get("slippage_bps_avg", 0.0)), 0.0, 10000.0)
            prev_lat = _clamp(_safe_float(stats.get("latency_ms_avg", 0.0)), 0.0, 60000.0)
            prev_fill = _clamp(_safe_float(stats.get("fill_rate", 0.0)), 0.0, 1.0)
            prev_score = _clamp(_safe_float(stats.get("execution_score_avg", 0.5)), 0.0, 1.0)
            new_samples = int(stats["samples"])

            if samples <= 0:
                slippage_avg = slippage_bps
                latency_avg = latency_ms
                fill_avg = fill_rate_clamped
                score_avg = score
            elif samples < 20:
                inv_n = 1.0 / max(1, new_samples)
                slippage_avg = prev_slip + (slippage_bps - prev_slip) * inv_n
                latency_avg = prev_lat + (latency_ms - prev_lat) * inv_n
                fill_avg = prev_fill + (fill_rate_clamped - prev_fill) * inv_n
                score_avg = prev_score + (score - prev_score) * inv_n
            else:
                alpha = 0.10
                slippage_avg = prev_slip + alpha * (slippage_bps - prev_slip)
                latency_avg = prev_lat + alpha * (latency_ms - prev_lat)
                fill_avg = prev_fill + alpha * (fill_rate_clamped - prev_fill)
                score_avg = prev_score + alpha * (score - prev_score)

            self.state["execution_rolling_stats"] = {
                "slippage_bps_avg": round(_clamp(slippage_avg, 0.0, 10000.0), 6),
                "latency_ms_avg": round(_clamp(latency_avg, 0.0, 60000.0), 6),
                "fill_rate": round(_clamp(fill_avg, 0.0, 1.0), 6),
                "execution_score_avg": round(_clamp(score_avg, 0.0, 1.0), 6),
                "samples": new_samples,
            }

            self.side_exec_stats[side]["slippage_bps_sum"] += slippage_bps
            self.side_exec_stats[side]["fill_quality_sum"] += fill_quality_value
            self.side_exec_stats[side]["filled_ratio_sum"] += fill_rate_clamped
            self.side_exec_stats[side]["latency_ms_sum"] += latency_ms
            self.side_exec_stats[side]["count"] += 1.0
            self._mark_mutated(dirty=True, pending_recompute=True)

            if len(self.exec_quality_scores) % self.cfg.save_every_n == 0:
                self._schedule_save()

    def summary(self) -> Dict[str, Any]:
        self._ensure_fresh_state()
        with self._lock:
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
