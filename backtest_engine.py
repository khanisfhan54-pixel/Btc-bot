# backtest_engine.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from feature_engine import FeatureEngine
    from signal_engine import SignalEngine
    from execution import ExecutionLogic
    from meta_filter import MetaFilter
except Exception as _be_import_err:
    import logging as _be_log
    _be_log.getLogger(__name__).warning("backtest_engine: module import failed (%s) — BacktestEngine unusable", _be_import_err)
    FeatureEngine = None  # type: ignore[assignment,misc]
    SignalEngine = None  # type: ignore[assignment,misc]
    ExecutionLogic = None  # type: ignore[assignment,misc]
    MetaFilter = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


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


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.cfg = config or BacktestConfig()
        self.feature_engine = FeatureEngine()
        self.signal_engine = SignalEngine()
        self.execution_logic = ExecutionLogic()
        self.meta_filter = MetaFilter()

    def run_backtest(self, ohlcv_data: List[list], initial_balance: float | None = None) -> Dict[str, Any]:
        data = [row for row in (ohlcv_data or []) if isinstance(row, (list, tuple)) and len(row) >= 6]
        if len(data) < 50:
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

            snapshot = _simulate_snapshot_from_candle(candle, window[-2][4] if len(window) > 1 else None)
            trades = _simulate_trades_from_candle(candle)

            features = self.feature_engine.update(snapshot, trades)
            signal = self.signal_engine.generate(features)
            meta = self.meta_filter.evaluate(features=features, signal=signal, decision=None, router_decision=None, snapshot=snapshot, trades=trades)

            decision = self.execution_logic.decide(
                signal_payload=signal,
                features_payload=features,
                snapshot=snapshot,
                account_equity=balance,
                meta_result=meta,
            )

            if position is None and decision.get("execute"):
                side = str(decision.get("side", "buy")).lower()
                entry = current_price * (1.0 + (self.cfg.slippage_bps / 10_000.0 if side == "buy" else -(self.cfg.slippage_bps / 10_000.0)))
                size = _safe_float(decision.get("position_size", 0.0))
                if size <= 0:
                    continue
                position = {
                    "side": "LONG" if side == "buy" else "SHORT",
                    "entry": entry,
                    "sl": _safe_float(decision.get("sl", 0.0)),
                    "tp": _safe_float(decision.get("tp", 0.0)),
                    "size": size,
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
                fee = self.cfg.fee_bps / 10_000.0
                slippage = self.cfg.slippage_bps / 10_000.0
                net_pnl_pct = gross_pnl_pct - fee - slippage
                pnl = balance * net_pnl_pct * 0.25
                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                returns.append(net_pnl_pct)
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

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 6),
            "pnl": round(balance - (initial_balance if initial_balance is not None else self.cfg.initial_balance), 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(_compute_sharpe(returns), 6),
            "expectancy": round(expectancy, 6),
            "trade_log": trade_log,
        }