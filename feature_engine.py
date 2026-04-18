# feature_engine.py
"""
feature_engine.py — Institutional-grade market microstructure feature extraction.

Public API (unchanged):
    feature_engine = FeatureEngine(max_levels=10)
    result = feature_engine.update(snapshot, trades)
    # result == {"features": dict, "confidence": float}

Features computed:
    best_bid, best_ask, mid, spread, spread_bps
    microprice, vamp, vamp_deviation_bps, fair_price_deviation_bps
    ofi, ofi_delta, ofi_velocity, ofi_acceleration
    mlofi_vector
    aggressor_imbalance, buy_volume, sell_volume, trade_volume
    trade_burst, queue_churn, resiliency
    hidden_liquidity (iceberg-style absorption signal)
    liquidity_score
    regime: "toxic" | "trend" | "range" | "accumulation"
    urgency (float 0-1)
    gap_proxy_bps, largest_gap_bps, latency_ms
    confidence (data-quality confidence score)

Backward-compat aliases (for signal_engine.py, execution.py, QueueFillModel, ToxicityFilter):
    ofi_norm, mlofi_signed, mlofi_strength
    vamp_bias_bps, order_imbalance, trade_imbalance
    bid_depth_n, ask_depth_n, total_depth_n
    top_bid_qty, top_ask_qty
    spoofing_intensity, book_slope_proxy, avg_level_depth
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

Level = Tuple[float, float]

MAX_SPREAD_BPS_QUALITY = 15.0
LIQUIDITY_DEPTH_SCALE  = 25.0


def _clamp(x: float, lo: float, hi: float) -> float:
    xf = _safe_float(x, lo)
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, xf))


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        aa = _safe_float(a, default)
        bb = _safe_float(b, 0.0)
        out = aa / max(abs(bb), 1e-9)
        return out if math.isfinite(out) else default
    except Exception:
        return default


@dataclass
class FeatureConfig:
    max_levels: int                      = 10
    liquidity_window: int                = 20
    hidden_liquidity_min_volume: float   = 25.0
    hidden_liquidity_mid_move_bps: float = 1.5
    hidden_liquidity_depth_change_ratio: float = 0.12
    toxic_spread_bps: float              = 20.0
    low_liquidity_score: float           = 0.35


# ---------------------------------------------------------------------------
# Regime Detection v3
# ---------------------------------------------------------------------------

@dataclass
class RegimeConfig:
    toxic_spread_bps:              float = 20.0
    illiquid_spread_bps:           float = 12.0
    toxic_vpin:                    float = 0.70
    high_latency_ms:               float = 2500.0
    min_liquidity_score:           float = 0.35
    trend_ofi_accel:               float = 0.015
    trend_aggressor_imbalance:     float = 0.18
    burst_threshold:               float = 0.60


def _regime_score(
    liquidity_score: float,
    spread_bps: float,
    vpin: float,
    latency_ms: float,
    ofi_acceleration: float,
    aggressor_imbalance: float,
    trade_burst: float,
    hidden_liquidity: bool,
    resiliency: float,
    queue_churn: float,
    microprice_dev_bps: float,
    vamp_dev_bps: float,
) -> Dict[str, Any]:
    """
    Regime Detection v3: scores six market regimes and returns trading parameters.
    Called inside FeatureEngine._compute(); also usable stand-alone.
    """
    cfg = RegimeConfig()

    liquidity_score    = _clamp(liquidity_score, 0.0, 1.0)
    spread_bps         = max(0.0, spread_bps)
    vpin               = _clamp(vpin, 0.0, 1.0)
    latency_ms         = max(0.0, latency_ms)
    trade_burst        = _clamp(trade_burst, 0.0, 1.0)
    resiliency         = _clamp(resiliency, 0.0, 1.0)
    queue_churn        = _clamp(queue_churn, 0.0, 2.0)
    microprice_dev_bps = abs(microprice_dev_bps)
    vamp_dev_bps       = abs(vamp_dev_bps)

    # --- Toxic ---
    toxic_score  = 0.0
    toxic_score += 0.30 if spread_bps >= cfg.toxic_spread_bps else 0.0
    toxic_score += 0.30 if vpin >= cfg.toxic_vpin else 0.0
    toxic_score += 0.15 if latency_ms >= cfg.high_latency_ms else 0.0
    toxic_score += 0.10 if liquidity_score <= cfg.min_liquidity_score else 0.0
    toxic_score += 0.05 if queue_churn > 1.0 else 0.0
    toxic_score += 0.10 if hidden_liquidity and trade_burst > 0.55 else 0.0

    # --- Illiquid ---
    illiquid_score  = 0.0
    illiquid_score += 0.35 if liquidity_score < cfg.min_liquidity_score else 0.0
    illiquid_score += 0.25 if spread_bps >= cfg.illiquid_spread_bps else 0.0
    illiquid_score += 0.15 if resiliency < 0.35 else 0.0
    illiquid_score += 0.10 if latency_ms >= cfg.high_latency_ms else 0.0
    illiquid_score += 0.15 if queue_churn > 1.2 else 0.0

    # --- Trend ---
    trend_score  = 0.0
    trend_score += min(0.35, abs(ofi_acceleration) * 12.0)
    trend_score += min(0.20, abs(aggressor_imbalance))
    trend_score += 0.15 if trade_burst >= cfg.burst_threshold else 0.0
    trend_score += 0.10 if microprice_dev_bps > 2.0 else 0.0
    trend_score += 0.10 if vamp_dev_bps > 2.5 else 0.0
    trend_score += 0.10 if resiliency < 0.50 else 0.0

    # --- Range ---
    range_score  = 0.0
    range_score += 0.25 if liquidity_score >= 0.50 else 0.0
    range_score += 0.20 if spread_bps < cfg.illiquid_spread_bps else 0.0
    range_score += 0.20 if abs(ofi_acceleration) < cfg.trend_ofi_accel else 0.0
    range_score += 0.15 if abs(aggressor_imbalance) < cfg.trend_aggressor_imbalance else 0.0
    range_score += 0.10 if trade_burst < cfg.burst_threshold else 0.0
    range_score += 0.10 if resiliency >= 0.50 else 0.0

    # --- Accumulation ---
    accumulation_score  = 0.0
    accumulation_score += 0.30 if hidden_liquidity else 0.0
    accumulation_score += 0.20 if liquidity_score >= 0.45 else 0.0
    accumulation_score += 0.15 if abs(ofi_acceleration) < cfg.trend_ofi_accel else 0.0
    accumulation_score += 0.15 if trade_burst < cfg.burst_threshold else 0.0
    accumulation_score += 0.10 if microprice_dev_bps > 1.0 else 0.0
    accumulation_score += 0.10 if vamp_dev_bps > 1.0 else 0.0

    # --- Distribution ---
    distribution_score  = 0.0
    distribution_score += 0.30 if hidden_liquidity else 0.0
    distribution_score += 0.20 if liquidity_score >= 0.45 else 0.0
    distribution_score += 0.15 if abs(ofi_acceleration) < cfg.trend_ofi_accel else 0.0
    distribution_score += 0.15 if trade_burst < cfg.burst_threshold else 0.0
    distribution_score += 0.10 if microprice_dev_bps > 1.0 else 0.0
    distribution_score += 0.10 if vamp_dev_bps > 1.0 else 0.0

    scores = {
        "toxic":        toxic_score,
        "illiquid":     illiquid_score,
        "trend":        trend_score,
        "range":        range_score,
        "accumulation": accumulation_score,
        "distribution": distribution_score,
    }
    regime            = max(scores.items(), key=lambda x: x[1])[0]
    regime_confidence = _clamp(scores[regime], 0.0, 1.0)

    allow_trade   = regime not in ("toxic", "illiquid") and liquidity_score >= cfg.min_liquidity_score
    position_scale = 1.0
    cooldown_bars  = 1
    max_hold_bars  = 12
    trade_mode     = "balanced"
    entry_bias     = "NEUTRAL"
    exit_bias      = "hold"

    if regime == "trend":
        trade_mode     = "trend_follow"
        position_scale = 1.10
        cooldown_bars  = 1
        max_hold_bars  = 20
        entry_bias     = "FOLLOW"
        exit_bias      = "trail"
    elif regime == "range":
        trade_mode     = "mean_revert"
        position_scale = 0.90
        cooldown_bars  = 2
        max_hold_bars  = 8
        entry_bias     = "FADE"
        exit_bias      = "fast_tp"
    elif regime == "accumulation":
        trade_mode     = "accumulate"
        position_scale = 0.80
        cooldown_bars  = 3
        max_hold_bars  = 18
        entry_bias     = "LONG_BIAS"
        exit_bias      = "patient"
    elif regime == "distribution":
        trade_mode     = "distribute"
        position_scale = 0.80
        cooldown_bars  = 3
        max_hold_bars  = 18
        entry_bias     = "SHORT_BIAS"
        exit_bias      = "patient"
    elif regime in ("toxic", "illiquid"):
        trade_mode     = "stand_down"
        position_scale = 0.0
        cooldown_bars  = 4
        max_hold_bars  = 0
        allow_trade    = False

    return {
        "regime":            regime,
        "regime_confidence": round(regime_confidence, 4),
        "allow_trade":       bool(allow_trade),
        "trade_mode":        trade_mode,
        "position_scale":    round(position_scale, 4),
        "cooldown_bars":     int(cooldown_bars),
        "max_hold_bars":     int(max_hold_bars),
        "entry_bias":        entry_bias,
        "exit_bias":         exit_bias,
        "scores":            {k: round(v, 4) for k, v in scores.items()},
        "regime_reasons": [
            f"liq={liquidity_score:.3f}",
            f"spread_bps={spread_bps:.2f}",
            f"vpin={vpin:.3f}",
            f"latency_ms={latency_ms:.0f}",
            f"ofi_accel={ofi_acceleration:.5f}",
            f"aggr_imb={aggressor_imbalance:.4f}",
            f"burst={trade_burst:.3f}",
            f"hidden_liq={hidden_liquidity}",
            f"resiliency={resiliency:.3f}",
            f"queue_churn={queue_churn:.3f}",
        ],
    }


class FeatureEngine:
    """
    Computes real-time microstructure features from order-book snapshots and trade prints.

    Returns:
        {"features": dict, "confidence": float}

    Never raises on malformed input — returns safe defaults instead.
    """

    def __init__(self, max_levels: int = 10, config: Optional[FeatureConfig] = None) -> None:
        self.cfg = config or FeatureConfig(max_levels=max_levels)

        # State carried between ticks
        self.prev_mid:           Optional[float] = None
        self.prev_ofi:           float           = 0.0
        self.prev_ofi_velocity:  float           = 0.0
        self.prev_total_depth:   Optional[float] = None
        self.prev_ts_ms:         Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        snapshot: Dict[str, Any],
        trades: Optional[List[Dict[str, Any]]] = None,
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process one market snapshot.

        Args:
            snapshot: order-book dict with bids/asks
            trades:   recent trade prints (optional)

        Returns:
            {"features": dict, "confidence": float}
        """
        trades = trades or []

        try:
            bids, asks = self._extract_levels(snapshot)
        except Exception:
            return self._empty_output(snapshot, regime_context=regime_context)

        if not bids or not asks:
            return self._empty_output(snapshot, regime_context=regime_context)

        try:
            return self._compute(snapshot, bids, asks, trades, regime_context=regime_context)
        except Exception:
            return self._empty_output(snapshot)

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def _compute(
        self,
        snapshot: Dict[str, Any],
        bids: List[Level],
        asks: List[Level],
        trades: List[Dict[str, Any]],
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        best_bid, best_ask, mid, spread = self._best_prices(bids, asks)
        spread_bps = _safe_div(spread, mid, 0.0) * 10_000.0

        top_bids = bids[: self.cfg.max_levels]
        top_asks = asks[: self.cfg.max_levels]

        bid_depth   = sum(q for _, q in top_bids)
        ask_depth   = sum(q for _, q in top_asks)
        total_depth = bid_depth + ask_depth

        top_bid_qty = bids[0][1] if bids else 0.0
        top_ask_qty = asks[0][1] if asks else 0.0

        microprice = self._microprice(best_bid, best_ask, top_bids, top_asks)
        vamp       = self._vamp(top_bids, top_asks)

        ofi, mlofi_vector = self._ofi_and_mlofi(top_bids, top_asks)
        ofi_delta, ofi_velocity, ofi_acceleration = self._ofi_dynamics(ofi, snapshot)

        aggressor_imbalance, buy_volume, sell_volume, trade_volume = self._aggressor_imbalance(
            trades, best_bid, best_ask
        )

        trade_burst   = self._trade_burst(trades, snapshot)
        queue_churn   = self._queue_churn(top_bids, top_asks)
        resiliency    = self._resiliency(total_depth)
        hidden_liq    = self._hidden_liquidity(trades, mid, total_depth, self.prev_total_depth)

        liquidity_score = self._liquidity_score(
            best_bid, best_ask, spread_bps, total_depth, resiliency, queue_churn
        )
        # Compute deviations before regime call (needed as inputs to _regime_score)
        fair_price_deviation_bps = _safe_div(microprice - mid, mid, 0.0) * 10_000.0
        vamp_deviation_bps       = _safe_div(vamp - mid, mid, 0.0) * 10_000.0

        latency_ms = self._latency_ms(snapshot)

        # Regime Detection v3 — replaces the old single-string _regime() method
        regime_info = _regime_score(
            liquidity_score    = liquidity_score,
            spread_bps         = spread_bps,
            vpin               = 0.0,          # enriched later by ToxicityFilter
            latency_ms         = latency_ms,
            ofi_acceleration   = ofi_acceleration,
            aggressor_imbalance= aggressor_imbalance,
            trade_burst        = trade_burst,
            hidden_liquidity   = hidden_liq,
            resiliency         = resiliency,
            queue_churn        = queue_churn,
            microprice_dev_bps = fair_price_deviation_bps,
            vamp_dev_bps       = vamp_deviation_bps,
        )
        regime = regime_info["regime"]   # keep for backward compat inside this function

        gap_proxy_bps, largest_gap_bps = self._gap_metrics(top_bids, top_asks, mid)
        urgency = self._urgency(
            liquidity_score, spread_bps, ofi_delta, ofi_acceleration,
            aggressor_imbalance, trade_burst, hidden_liq,
        )
        confidence = self._confidence(spread_bps, liquidity_score, total_depth, queue_churn, latency_ms)

        # --- Backward-compat derived scalars ---
        ofi_norm    = _clamp(ofi, -1.0, 1.0)
        mlofi_signed  = mlofi_vector[0] if mlofi_vector else 0.0
        mlofi_strength = _safe_div(
            sum(abs(x) for x in mlofi_vector), max(len(mlofi_vector), 1), 0.0
        )
        spoofing_intensity = _clamp(queue_churn * 0.5 + trade_burst * 0.5, 0.0, 1.0)
        book_slope_proxy   = _safe_div(spread_bps, 1.0 + total_depth, 0.0)
        avg_level_depth    = _safe_div(total_depth, max(1, self.cfg.max_levels * 2), 0.0)

        # --- Update state for next tick ---
        self.prev_mid         = mid
        self.prev_ofi         = ofi
        self.prev_ofi_velocity = ofi_velocity
        self.prev_total_depth = total_depth
        self.prev_ts_ms       = self._snapshot_ts_ms(snapshot)

        features: Dict[str, Any] = {
            # ----- Prices -----
            "best_bid":                   best_bid,
            "best_ask":                   best_ask,
            "mid":                        mid,
            "spread":                     spread,
            "spread_bps":                 spread_bps,

            # ----- Book depth -----
            "bid_depth":                  bid_depth,
            "ask_depth":                  ask_depth,
            "total_depth":                total_depth,
            "top_bid_qty":                top_bid_qty,
            "top_ask_qty":                top_ask_qty,
            "avg_level_depth":            avg_level_depth,
            "top_bids":                   top_bids,
            "top_asks":                   top_asks,

            # ----- Fair value -----
            "microprice":                 microprice,
            "vamp":                       vamp,
            "vamp_deviation_bps":         vamp_deviation_bps,
            "fair_price_deviation_bps":   fair_price_deviation_bps,

            # ----- OFI / MLOFI -----
            "ofi":                        ofi,
            "ofi_delta":                  ofi_delta,
            "ofi_velocity":               ofi_velocity,
            "ofi_acceleration":           ofi_acceleration,
            "mlofi_vector":               mlofi_vector,
            "mlofi_strength":             mlofi_strength,

            # ----- Trade flow -----
            "aggressor_imbalance":        aggressor_imbalance,
            "buy_volume":                 buy_volume,
            "sell_volume":                sell_volume,
            "trade_volume":               trade_volume,
            "trade_burst":                trade_burst,

            # ----- Book dynamics -----
            "queue_churn":                queue_churn,
            "resiliency":                 resiliency,
            "hidden_liquidity":           hidden_liq,

            # ----- Liquidity / quality -----
            "liquidity_score":            liquidity_score,
            "gap_proxy_bps":              gap_proxy_bps,
            "largest_gap_bps":            largest_gap_bps,
            "book_slope_proxy":           book_slope_proxy,

            # ----- Regime / meta (v3) -----
            "regime":                     regime,
            "regime_confidence":          regime_info["regime_confidence"],
            "allow_trade":                regime_info["allow_trade"],
            "trade_mode":                 regime_info["trade_mode"],
            "position_scale":             regime_info["position_scale"],
            "cooldown_bars":              regime_info["cooldown_bars"],
            "max_hold_bars":              regime_info["max_hold_bars"],
            "entry_bias":                 regime_info["entry_bias"],
            "exit_bias":                  regime_info["exit_bias"],
            "regime_scores":              regime_info["scores"],
            "regime_reasons":             regime_info["regime_reasons"],
            "urgency":                    urgency,
            "latency_ms":                 latency_ms,
            "timestamp_ms":               self._snapshot_ts_ms(snapshot),

            # ----- Backward-compat aliases (signal_engine, execution, fill_model, tox_filter) -----
            "ofi_norm":                   ofi_norm,
            "mlofi_signed":               mlofi_signed,
            "vamp_bias_bps":              vamp_deviation_bps,
            "order_imbalance":            aggressor_imbalance,
            "trade_imbalance":            aggressor_imbalance,
            "bid_depth_n":                bid_depth,
            "ask_depth_n":                ask_depth,
            "total_depth_n":              total_depth,
            "spoofing_intensity":         spoofing_intensity,
        }

        if isinstance(regime_context, dict):
            ctx_features = regime_context.get("features", {})
            if not isinstance(ctx_features, dict):
                ctx_features = {}
            vol_reg = ctx_features.get("volatility_regime", regime_context.get("regime", "unknown"))
            liq_reg = ctx_features.get("liquidity_regime", regime_context.get("regime", "unknown"))
            trend   = _clamp(
                _safe_float(ctx_features.get("trend_strength", regime_context.get("confidence", 0.0)), 0.0),
                0.0,
                1.0
            )

            if isinstance(vol_reg, str):
                features["volatility_regime"] = vol_reg
            if isinstance(liq_reg, str):
                features["liquidity_regime"] = liq_reg
            if isinstance(trend, (int, float)) and math.isfinite(trend):
                features["trend_strength"] = trend

        features = self._sanitize_features(features)
        confidence = _clamp(confidence, 0.0, 1.0)
        return {"features": features, "confidence": confidence}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_levels(self, snapshot: Dict[str, Any]) -> Tuple[List[Level], List[Level]]:
        def normalize(raw: Any) -> List[Level]:
            out: List[Level] = []
            for lvl in raw or []:
                try:
                    if isinstance(lvl, dict):
                        p = _safe_float(lvl.get("price"))
                        q = _safe_float(
                            lvl.get("size") or lvl.get("amount")
                            or lvl.get("qty") or lvl.get("quantity")
                        )
                    else:
                        p = _safe_float(lvl[0])
                        q = _safe_float(lvl[1])
                    if p > 0 and q >= 0:
                        out.append((p, q))
                except Exception:
                    continue
            return out

        if isinstance(snapshot, dict):
            if "bids" in snapshot and "asks" in snapshot:
                bids = normalize(snapshot.get("bids"))
                asks = normalize(snapshot.get("asks"))
            elif "order_book" in snapshot and isinstance(snapshot["order_book"], dict):
                ob   = snapshot["order_book"]
                bids = normalize(ob.get("bids", []))
                asks = normalize(ob.get("asks", []))
            else:
                bids = normalize(snapshot.get("bid_levels") or snapshot.get("bid") or [])
                asks = normalize(snapshot.get("ask_levels") or snapshot.get("ask") or [])
        else:
            bids, asks = [], []

        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        return bids, asks

    def _best_prices(
        self, bids: List[Level], asks: List[Level]
    ) -> Tuple[float, float, float, float]:
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid      = max(0.0, (best_bid + best_ask) / 2.0)
        spread   = max(0.0, best_ask - best_bid)
        return best_bid, best_ask, mid, spread

    def _microprice(
        self,
        best_bid: float,
        best_ask: float,
        bids: List[Level],
        asks: List[Level],
    ) -> float:
        n       = max(1, min(3, len(bids), len(asks)))
        bid_qty = sum(q for _, q in bids[:n])
        ask_qty = sum(q for _, q in asks[:n])
        denom   = bid_qty + ask_qty
        if denom <= 0:
            return (best_bid + best_ask) / 2.0
        return (best_bid * ask_qty + best_ask * bid_qty) / denom

    def _vamp(self, bids: List[Level], asks: List[Level]) -> float:
        bid_qty = sum(q for _, q in bids)
        ask_qty = sum(q for _, q in asks)
        bid_notional = sum(p * q for p, q in bids)
        ask_notional = sum(p * q for p, q in asks)
        bid_vwap = _safe_div(bid_notional, bid_qty, bids[0][0] if bids else 0.0)
        ask_vwap = _safe_div(ask_notional, ask_qty, asks[0][0] if asks else 0.0)
        if bid_vwap > 0 and ask_vwap > 0:
            return max(0.0, (bid_vwap + ask_vwap) / 2.0)
        return max(0.0, bid_vwap or ask_vwap)

    def _ofi_and_mlofi(
        self, bids: List[Level], asks: List[Level]
    ) -> Tuple[float, List[float]]:
        vec: List[float] = []
        ofi = 0.0
        n   = min(self.cfg.max_levels, max(len(bids), len(asks)))
        for i in range(n):
            bid_q = bids[i][1] if i < len(bids) else 0.0
            ask_q = asks[i][1] if i < len(asks) else 0.0
            weight = 1.0 / (i + 1.0)
            level_imbalance = _safe_div(bid_q - ask_q, bid_q + ask_q, 0.0)
            vec.append(level_imbalance)
            ofi += weight * (bid_q - ask_q)

        norm = (
            sum(q for _, q in bids[: self.cfg.max_levels])
            + sum(q for _, q in asks[: self.cfg.max_levels])
        )
        if norm > 0:
            ofi /= norm
        return ofi, vec

    def _ofi_dynamics(
        self, ofi: float, snapshot: Dict[str, Any]
    ) -> Tuple[float, float, float]:
        now_ts = self._snapshot_ts_ms(snapshot)
        if self.prev_ts_ms is None or now_ts is None:
            return 0.0, 0.0, 0.0
        dt_ms            = max(1.0, now_ts - self.prev_ts_ms)
        dt_s             = dt_ms / 1000.0
        ofi_delta        = ofi - self.prev_ofi
        ofi_velocity     = _safe_div(ofi_delta, dt_s, 0.0)
        ofi_acceleration = _safe_div(ofi_velocity - self.prev_ofi_velocity, dt_s, 0.0)
        return ofi_delta, ofi_velocity, ofi_acceleration

    def _aggressor_imbalance(
        self,
        trades: List[Dict[str, Any]],
        best_bid: float,
        best_ask: float,
    ) -> Tuple[float, float, float, float]:
        buy_vol = sell_vol = total_vol = 0.0
        for tr in trades[-self.cfg.liquidity_window:]:
            price = _safe_float(tr.get("price"))
            size  = _safe_float(
                tr.get("size") or tr.get("amount") or tr.get("qty") or tr.get("quantity")
            )
            if size <= 0:
                continue
            side = str(tr.get("side", "")).lower()
            if side in ("buy", "b", "bid", "maker_buy", "taker_buy", "buyer"):
                buy_vol += size
            elif side in ("sell", "s", "ask", "maker_sell", "taker_sell", "seller"):
                sell_vol += size
            else:
                if price >= best_ask:
                    buy_vol += size
                elif price <= best_bid:
                    sell_vol += size
                else:
                    if abs(price - best_ask) < abs(price - best_bid):
                        buy_vol += size
                    else:
                        sell_vol += size
            total_vol += size

        if total_vol <= 0:
            return 0.0, 0.0, 0.0, 0.0
        imb = _safe_div(buy_vol - sell_vol, total_vol, 0.0)
        return imb, buy_vol, sell_vol, total_vol

    def _trade_burst(
        self, trades: List[Dict[str, Any]], snapshot: Dict[str, Any]
    ) -> float:
        if not trades:
            return 0.0
        now_ms     = self._snapshot_ts_ms(snapshot) or (time.time() * 1000.0)
        window_ms  = 5_000.0
        recent     = []
        for tr in trades:
            ts = _safe_float(tr.get("timestamp") or tr.get("ts") or tr.get("time"), 0.0)
            if ts <= 0:
                recent.append(tr)
                continue
            tr_ms = ts if ts > 1e12 else (ts * 1000.0 if ts > 1e9 else now_ms)
            if now_ms - tr_ms <= window_ms:
                recent.append(tr)
        count_rate = len(recent) / max(window_ms / 1000.0, 1.0)
        vol        = sum(
            _safe_float(t.get("size") or t.get("amount") or t.get("qty") or t.get("quantity"))
            for t in recent
        )
        vol_rate   = vol / max(window_ms / 1000.0, 1.0)
        burst = 0.6 * _clamp(count_rate / 10.0, 0.0, 1.0) + 0.4 * _clamp(vol_rate / 50.0, 0.0, 1.0)
        return _clamp(burst, 0.0, 1.0)

    def _queue_churn(self, bids: List[Level], asks: List[Level]) -> float:
        current_depth = (
            sum(q for _, q in bids[: self.cfg.max_levels])
            + sum(q for _, q in asks[: self.cfg.max_levels])
        )
        if self.prev_total_depth is None or self.prev_total_depth <= 0:
            return 0.0
        depth_change = abs(current_depth - self.prev_total_depth)
        return _clamp(_safe_div(depth_change, self.prev_total_depth, 0.0), 0.0, 2.0)

    def _resiliency(self, current_total_depth: float) -> float:
        if self.prev_total_depth is None:
            return 0.5
        change = abs(current_total_depth - self.prev_total_depth)
        ratio  = _safe_div(current_total_depth, max(change, 1.0), current_total_depth)
        return _clamp(math.log1p(ratio) / 4.0, 0.0, 1.0)

    def _hidden_liquidity(
        self,
        trades: List[Dict[str, Any]],
        mid: float,
        total_depth: float,
        prev_total_depth: Optional[float],
    ) -> bool:
        """Iceberg-style absorption: large trade volume, small depth change, small price move."""
        if not trades:
            return False
        vol = sum(
            _safe_float(t.get("size") or t.get("amount") or t.get("qty") or t.get("quantity"))
            for t in trades[-20:]
        )
        if vol < self.cfg.hidden_liquidity_min_volume:
            return False
        if prev_total_depth is None or prev_total_depth <= 0:
            return False
        depth_change_ratio = _safe_div(abs(total_depth - prev_total_depth), prev_total_depth, 1.0)
        if depth_change_ratio > self.cfg.hidden_liquidity_depth_change_ratio:
            return False
        prices = [
            _safe_float(t.get("price")) for t in trades
            if _safe_float(t.get("price")) > 0
        ]
        if len(prices) < 2 or mid <= 0:
            return False
        move_bps = _safe_div(abs(prices[0] - prices[-1]), mid, 0.0) * 10_000.0
        return move_bps <= self.cfg.hidden_liquidity_mid_move_bps

    def _liquidity_score(
        self,
        best_bid: float,
        best_ask: float,
        spread_bps: float,
        total_depth: float,
        resiliency: float,
        queue_churn: float,
    ) -> float:
        depth_score   = _clamp(math.log1p(total_depth) / 10.0, 0.0, 1.0)
        spread_score  = _clamp(1.0 - _safe_div(spread_bps, self.cfg.toxic_spread_bps, 1.0), 0.0, 1.0)
        churn_penalty = _clamp(_safe_div(queue_churn, 1.5, 0.0), 0.0, 1.0)
        score = 0.40 * depth_score + 0.35 * spread_score + 0.25 * resiliency
        score *= (1.0 - 0.20 * churn_penalty)
        return _clamp(score, 0.0, 1.0)

    def _regime(
        self,
        spread_bps: float,
        liquidity_score: float,
        ofi: float,
        aggressor_imbalance: float,
        trade_burst: float,
        hidden_liquidity: bool,
        ofi_acceleration: float,
        microprice: float,
        mid: float,
    ) -> str:
        if liquidity_score < self.cfg.low_liquidity_score or spread_bps > self.cfg.toxic_spread_bps:
            return "toxic"
        directional_pressure = (
            abs(ofi) + abs(aggressor_imbalance) + min(1.0, abs(ofi_acceleration) / 5.0)
        )
        price_dislocation = _safe_div(abs(microprice - mid), mid, 0.0) * 10_000.0 if mid > 0 else 0.0
        if hidden_liquidity and directional_pressure > 0.8 and price_dislocation < 3.0:
            return "accumulation"
        if directional_pressure > 0.9 or trade_burst > 0.75:
            return "trend"
        return "range"

    def _urgency(
        self,
        liquidity_score: float,
        spread_bps: float,
        ofi_delta: float,
        ofi_acceleration: float,
        aggressor_imbalance: float,
        trade_burst: float,
        hidden_liquidity: bool,
    ) -> float:
        urgency  = 0.35
        urgency += (1.0 - liquidity_score) * 0.35
        urgency += _clamp(abs(ofi_delta) * 0.20, 0.0, 0.20)
        urgency += _clamp(abs(ofi_acceleration) * 0.05, 0.0, 0.10)
        urgency += _clamp(abs(aggressor_imbalance) * 0.15, 0.0, 0.15)
        urgency += trade_burst * 0.10
        if hidden_liquidity:
            urgency += 0.05
        urgency -= _clamp(spread_bps / 100.0, 0.0, 0.10)
        return _clamp(urgency, 0.0, 1.0)

    def _gap_metrics(
        self, bids: List[Level], asks: List[Level], mid: float
    ) -> Tuple[float, float]:
        def largest_gap(levels: List[Level]) -> float:
            if len(levels) < 2 or mid <= 0:
                return 0.0
            gaps = [
                abs(levels[i][0] - levels[i + 1][0]) / mid * 10_000.0
                for i in range(len(levels) - 1)
                if levels[i][0] > 0 and levels[i + 1][0] > 0
            ]
            return max(gaps) if gaps else 0.0

        bid_gap = largest_gap(bids)
        ask_gap = largest_gap(asks)
        proxy   = (bid_gap + ask_gap) / 2.0
        largest = max(bid_gap, ask_gap)
        return proxy, largest

    def _confidence(
        self,
        spread_bps: float,
        liquidity_score: float,
        total_depth: float,
        queue_churn: float,
        latency_ms: float,
    ) -> float:
        spread_q  = math.exp(-spread_bps / max(MAX_SPREAD_BPS_QUALITY, 1e-9))
        depth_q   = math.tanh(_safe_div(total_depth, LIQUIDITY_DEPTH_SCALE, 0.0))
        churn_q   = 1.0 - _clamp(queue_churn / 2.0, 0.0, 1.0)
        stale_ms  = self.cfg.toxic_spread_bps * 1000.0  # reuse as staleness threshold (2s proxy)
        stale_pen = 1.0 if latency_ms <= stale_ms else max(
            0.0, 1.0 - (latency_ms - stale_ms) / max(stale_ms, 1.0)
        )
        conf = (
            0.35 * spread_q
            + 0.30 * liquidity_score
            + 0.20 * churn_q
            + 0.15 * depth_q
        ) * stale_pen
        return _clamp(conf, 0.0, 1.0)

    def _latency_ms(self, snapshot: Dict[str, Any]) -> float:
        ts = self._snapshot_ts_ms(snapshot)
        if ts is None:
            return 0.0
        return max(0.0, time.time() * 1000.0 - ts)

    def _snapshot_ts_ms(self, snapshot: Dict[str, Any]) -> Optional[float]:
        if not isinstance(snapshot, dict):
            return None
        raw = (
            snapshot.get("timestamp") or snapshot.get("ts")
            or snapshot.get("time") or snapshot.get("datetime")
        )
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                raw = float(raw)
            except Exception:
                return None
        t = _safe_float(raw, 0.0)
        if t <= 0:
            return None
        if t > 1e12:
            return t
        if t > 1e9:
            return t * 1000.0
        return None

    def _empty_output(
        self,
        snapshot: Dict[str, Any],
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = self._snapshot_ts_ms(snapshot)
        latency = max(0.0, time.time() * 1000.0 - ts) if ts else 0.0
        f: Dict[str, Any] = {
            "best_bid": 0.0, "best_ask": 0.0, "mid": 0.0,
            "spread": 0.0, "spread_bps": 0.0,
            "bid_depth": 0.0, "ask_depth": 0.0, "total_depth": 0.0,
            "top_bid_qty": 0.0, "top_ask_qty": 0.0, "avg_level_depth": 0.0,
            "top_bids": [], "top_asks": [],
            "microprice": 0.0, "vamp": 0.0,
            "vamp_deviation_bps": 0.0, "fair_price_deviation_bps": 0.0,
            "ofi": 0.0, "ofi_delta": 0.0, "ofi_velocity": 0.0, "ofi_acceleration": 0.0,
            "mlofi_vector": [], "mlofi_strength": 0.0,
            "aggressor_imbalance": 0.0, "buy_volume": 0.0, "sell_volume": 0.0,
            "trade_volume": 0.0, "trade_burst": 0.0,
            "queue_churn": 0.0, "resiliency": 0.0, "hidden_liquidity": False,
            "liquidity_score": 0.0, "gap_proxy_bps": 0.0, "largest_gap_bps": 0.0,
            "book_slope_proxy": 0.0, "regime": "unknown", "urgency": 0.5,
            "regime_confidence": 0.0, "allow_trade": False, "trade_mode": "stand_down",
            "position_scale": 0.0, "cooldown_bars": 0, "max_hold_bars": 0,
            "entry_bias": "NEUTRAL", "exit_bias": "hold", "regime_scores": {},
            "regime_reasons": [],
            "latency_ms": latency, "timestamp_ms": ts,
            # Backward-compat aliases
            "ofi_norm": 0.0, "mlofi_signed": 0.0, "vamp_bias_bps": 0.0,
            "order_imbalance": 0.0, "trade_imbalance": 0.0,
            "bid_depth_n": 0.0, "ask_depth_n": 0.0, "total_depth_n": 0.0,
            "spoofing_intensity": 0.0,
        }
        # Preserve regime_context overlay so downstream consumers see the
        # last-known regime even when the orderbook is temporarily empty.
        if isinstance(regime_context, dict):
            ctx_features = regime_context.get("features", {})
            if not isinstance(ctx_features, dict):
                ctx_features = {}
            vol_reg = ctx_features.get("volatility_regime", regime_context.get("regime", "unknown"))
            liq_reg = ctx_features.get("liquidity_regime", regime_context.get("regime", "unknown"))
            trend = _clamp(
                _safe_float(ctx_features.get("trend_strength", regime_context.get("confidence", 0.0)), 0.0),
                0.0,
                1.0,
            )
            if isinstance(vol_reg, str):
                f["volatility_regime"] = vol_reg
            if isinstance(liq_reg, str):
                f["liquidity_regime"] = liq_reg
            if isinstance(trend, (int, float)) and math.isfinite(trend):
                f["trend_strength"] = trend
        return {"features": self._sanitize_features(f), "confidence": 0.0}

    def _sanitize_features(self, features: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for k, v in features.items():
            if isinstance(v, bool):
                sanitized[k] = v
            elif isinstance(v, (int, float)):
                sanitized[k] = self._sanitize_numeric_feature(k, v)
            elif k in ("top_bids", "top_asks") and isinstance(v, list):
                lvls: List[Level] = []
                for lvl in v:
                    if not isinstance(lvl, (list, tuple)) or len(lvl) < 2:
                        continue
                    p = _clamp(_safe_float(lvl[0], 0.0), 0.0, 10_000_000.0)
                    q = _clamp(_safe_float(lvl[1], 0.0), 0.0, 1_000_000_000.0)
                    if p > 0:
                        lvls.append((p, q))
                sanitized[k] = lvls
            elif k == "mlofi_vector" and isinstance(v, list):
                sanitized[k] = [_clamp(_safe_float(x, 0.0), -1.0, 1.0) for x in v]
            elif k == "regime_scores" and isinstance(v, dict):
                sanitized[k] = {
                    str(sk): _clamp(_safe_float(sv, 0.0), 0.0, 1.0)
                    for sk, sv in v.items()
                }
            else:
                sanitized[k] = v
        return sanitized

    def _sanitize_numeric_feature(self, key: str, value: float) -> float:
        v = _safe_float(value, 0.0)
        if key in {"ofi", "ofi_norm", "aggressor_imbalance", "order_imbalance", "trade_imbalance", "mlofi_signed"}:
            return _clamp(v, -1.0, 1.0)
        if key in {"trade_burst", "liquidity_score", "urgency", "resiliency", "regime_confidence", "spoofing_intensity"}:
            return _clamp(v, 0.0, 1.0)
        if key == "queue_churn":
            return _clamp(v, 0.0, 2.0)
        if key in {"spread", "spread_bps", "gap_proxy_bps", "largest_gap_bps", "latency_ms"}:
            hi = 600_000.0 if key == "latency_ms" else 10_000.0
            return _clamp(v, 0.0, hi)
        if key in {"vamp_deviation_bps", "fair_price_deviation_bps", "vamp_bias_bps", "book_slope_proxy"}:
            return _clamp(v, -10_000.0, 10_000.0)
        if key in {"ofi_delta", "ofi_velocity", "ofi_acceleration"}:
            return _clamp(v, -100.0, 100.0)
        if key in {"position_scale"}:
            return _clamp(v, 0.0, 2.0)
        if key in {"cooldown_bars", "max_hold_bars"}:
            return int(_clamp(v, 0.0, 10_000.0))
        if key in {"buy_volume", "sell_volume", "trade_volume", "bid_depth", "ask_depth", "total_depth",
                   "bid_depth_n", "ask_depth_n", "total_depth_n", "top_bid_qty", "top_ask_qty", "avg_level_depth"}:
            return _clamp(v, 0.0, 1_000_000_000.0)
        if key in {"best_bid", "best_ask", "mid", "microprice", "vamp", "timestamp_ms"}:
            return _clamp(v, 0.0, 10_000_000_000_000.0)
        if key in {"mlofi_strength"}:
            return _clamp(v, 0.0, 10.0)
        return _clamp(v, -1_000_000_000.0, 1_000_000_000.0)
