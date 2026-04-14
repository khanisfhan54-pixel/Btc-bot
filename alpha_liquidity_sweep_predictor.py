import math
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
import time

__all__ = ["predict_sweep", "LiquiditySweepAlpha"]
LOGIT_TEMP = 1.2
EPS = 1e-12

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def _is_finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

def _calibrate_prob(p: float) -> float:
    return _clamp(0.5 + (p - 0.5) * 0.8, 0.0, 1.0)

def _safe_logit(p: float, volatility: float = 0.0) -> float:
    """
    Safely compute log-odds mapping for probabilistic combinations.
    Clamps bounds to prevent domain errors or inf scaling.
    """
    p = _clamp(p, 1e-6, 1.0 - 1e-6)
    temp = 1.0 + min(1.0, max(0.0, _safe_float(volatility, 0.0)))
    return math.log(p / (1.0 - p)) / (temp * LOGIT_TEMP)

def _standard_sigmoid(x: float) -> float:
    """
    Standard sigmoid evaluating logits to [0,1] probability space.
    Overflow-safe mapping for negative scalars.
    """
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        ez = math.exp(x)
        return ez / (1.0 + ez)


def _safe_output(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Institutional output contract:
    - No None values
    - Stable schema
    - Normalized probabilities
    """
    prob_above = _clamp(_safe_float(result.get("prob_above"), 0.5), 0.0, 1.0)
    prob_below = _clamp(_safe_float(result.get("prob_below"), 0.5), 0.0, 1.0)
    total = prob_above + prob_below
    if total <= EPS:
        prob_above, prob_below = 0.5, 0.5
    else:
        prob_above /= total
        prob_below /= total

    action = str(result.get("action", "HOLD")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"

    # ✅ FIX 5: Ensure strict numeric casting (institutional safety)
    confidence = float(_safe_float(result.get("confidence"), 0.0))

    return {
        "action": action,
        "confidence": round(_clamp(confidence, 0.0, 1.0), 4),
        "state": str(result.get("state", "NORMAL")),
        "regime": str(result.get("regime", "RANGING")),
        "ofi_zscore": round(_safe_float(result.get("ofi_zscore"), 0.0), 4),
        "hawkes_intensity": round(_safe_float(result.get("hawkes_intensity"), 0.0), 4),
        "logic": str(result.get("logic", "No immediate edge")),
        "micro_prob": round(_clamp(_safe_float(result.get("micro_prob"), 0.5), 0.0, 1.0), 4),
        "macro_prob": round(_clamp(_safe_float(result.get("macro_prob"), 0.5), 0.0, 1.0), 4),
        "prob_above": round(prob_above, 4),
        "prob_below": round(prob_below, 4),
    }

def predict_sweep(
    liquidity: Dict[str, Any],
    market_state: Dict[str, Any],
    volume_intel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Predict which side liquidity will be swept first based on structural context.
    """
    # ✅ FIX 1: Harden liquidity input
    if not isinstance(liquidity, dict):
        liquidity = {}

    # ✅ FIX 2: Harden market_state input (prevents None crash)
    if not isinstance(market_state, dict):
        market_state = {}

    # ✅ FIX 3: Harden volume_intel input
    vol_intel = volume_intel if isinstance(volume_intel, dict) else {}

    nearest_above = liquidity.get("nearest_above")
    nearest_below = liquidity.get("nearest_below")

    # Ensure pools are valid dicts
    if not isinstance(nearest_above, dict):
        nearest_above = None
    if not isinstance(nearest_below, dict):
        nearest_below = None

    dist_above = _safe_float(nearest_above.get("distance_points")) if nearest_above else None
    dist_below = _safe_float(nearest_below.get("distance_points")) if nearest_below else None
    if dist_above is not None and dist_above < 0.0:
        dist_above = 0.0
    if dist_below is not None and dist_below < 0.0:
        dist_below = 0.0

    state = str(market_state.get("state", "CHOPPY")).upper()
    compression = _safe_float(market_state.get("compression", 1.0))
    volatility = _safe_float(market_state.get("volatility", 0.0))
    bias = _safe_float(market_state.get("bias", 0.0))

    vol_spike = bool(vol_intel.get("volume_spike", False))
    vol_strength = _safe_float(vol_intel.get("volume_strength", 0.0))

    prob_above, prob_below = 0.5, 0.5

    if dist_above is not None and dist_below is not None:
        total = dist_above + dist_below
        if total >= 1e-12:
            prob_above += (dist_below / total - 0.5)
            prob_below += (dist_above / total - 0.5)
    elif dist_above is not None:
        prob_above += 0.2
    elif dist_below is not None:
        prob_below += 0.2

    # directionally orient compression towards the closest pool structure
    if dist_above is not None and dist_below is not None:
        compression_bias = (dist_below - dist_above) / (dist_above + dist_below + 1e-6)
    else:
        # [FIX] Fallback for incomplete structural liquidity visibility
        # Forces compression probability mass to tilt logically based on trend/momentum rather than collapsing to neutral
        compression_bias = _clamp(bias, -1.0, 1.0)

    if state == "COMPRESSION":
        prob_above += 0.1 * compression_bias * compression
        prob_below -= 0.1 * compression_bias * compression
    elif state == "TRENDING":
        if bias > 0:
            prob_above += 0.15
            prob_below -= 0.10
        elif bias < 0:
            prob_below += 0.15
            prob_above -= 0.10

    if vol_spike or vol_strength >= 0.7:
        if bias > 0:
            prob_above += 0.1
        elif bias < 0:
            prob_below += 0.1

    # stabilize for non-finite / negative volatility + compression inputs
    if not _is_finite(volatility) or volatility < 0.0:
        volatility = 0.0
    if not _is_finite(compression) or compression < 0.0:
        compression = 0.0
    vol_adj = volatility / (1.0 + volatility)

    # dynamic compression threshold
    comp_threshold = max(vol_adj * 5, 0.05)

    if volatility > 0 and vol_adj < 0.01 and compression < comp_threshold:
        prob_above += 0.1 * compression_bias * compression
        prob_below -= 0.1 * compression_bias * compression

    prob_above = _clamp(prob_above)
    prob_below = _clamp(prob_below)

    total_prob = prob_above + prob_below
    # ✅ FIX 4: Safe probability normalization
    if total_prob <= EPS:
        prob_above, prob_below = 0.5, 0.5
    else:
        prob_above /= total_prob
        prob_below /= total_prob

    if prob_above >= prob_below:
        side = "above"
        probability = prob_above
        target = _safe_float(nearest_above.get("price")) if nearest_above else 0.0
    else:
        side = "below"
        probability = prob_below
        target = _safe_float(nearest_below.get("price")) if nearest_below else 0.0

    return {
        "side": side,
        "probability": round(probability, 4),
        "target_price": round(_safe_float(target, 0.0), 8),
        "prob_above": round(prob_above, 4),
        "prob_below": round(prob_below, 4),
        "state": state,
    }


class LiquiditySweepAlpha:
    """
    Production-grade logic for detecting Liquidity Sweeps, incorporating 
    normalized Order Flow Imbalance, Hawkes Processes, and LOB Resiliency.
    """
    def __init__(self, depth_levels: int = 10, resiliency_threshold: float = 0.7, history_window: int = 100):
        self.levels = depth_levels
        self.resiliency_threshold = resiliency_threshold
        self.history_window = history_window

        self.liquidity_pools = {"high": None, "low": None}
        self.ofi_history = deque(maxlen=history_window)
        self.hawkes_history = deque(maxlen=history_window)
        self.short_ofi = deque(maxlen=5)

        # rolling stats (O(1))
        self.ofi_sum = 0.0
        self.ofi_sq_sum = 0.0
        self.hawkes_sum = 0.0

        # Hawkes Process State
        self.hawkes_lambda = 0.0
        self.last_trade_time = time.time()
        self.hawkes_decay = 0.5 
        self.hawkes_alpha = 0.1 

    def _normalize_thresholds(self, atr: float, price: float) -> Dict[str, float]:
        """
        Dynamic threshold scaling based on volatility regime.
        """
        vol_ratio = atr / (price + 1e-8)

        return {
            "price_move": max(atr * 0.5, price * 0.001),   # displacement threshold
            "compression": max(vol_ratio * 0.5, 0.0005),   # compression threshold
            "trend_buffer": max(vol_ratio * 0.2, 0.001)    # EMA buffer
        }

    def _fast_sigmoid(self, x: float) -> float:
        # bounded + fast; map to [0,1] to behave like a probability score
        y = x / (1 + abs(x))   # [-1, 1]
        return 0.5 * (y + 1.0) # [0, 1]

    def update_liquidity_pools(self, recent_highs: List[float], recent_lows: List[float]):
        if recent_highs is not None and len(recent_highs) > 0:
            self.liquidity_pools['high'] = max(recent_highs[-20:])
        if recent_lows is not None and len(recent_lows) > 0:
            self.liquidity_pools['low'] = min(recent_lows[-20:])

    def _update_hawkes(self, timestamp: float, trade_count: int) -> float:
        # sanitize inputs + protect against timestamp unit mismatches (s vs ms/ns)
        ts = _safe_float(timestamp, self.last_trade_time)
        # order matters: ns (~1e18) must be handled before ms (~1e12-1e13)
        if ts > 1e15:      # likely ns
            ts *= 1e-9
        elif ts > 1e12:    # likely ms
            ts *= 1e-3
        # if initialization wall-clock is far from feed epoch, realign baseline once
        if abs(ts - self.last_trade_time) > 3600.0 and not self.hawkes_history:
            self.last_trade_time = ts

        tc = _safe_float(trade_count, 0.0)
        if tc < 0.0:
            tc = 0.0
        tc = min(tc, 1000.0)

        dt = ts - self.last_trade_time
        if dt < 0.0:
            dt = 0.0
        decay_term = math.exp(-self.hawkes_decay * min(dt, 60.0))
        self.hawkes_lambda = (self.hawkes_lambda * decay_term) + (self.hawkes_alpha * tc)
        if self.hawkes_lambda < 0.0 or not _is_finite(self.hawkes_lambda):
            self.hawkes_lambda = 0.0
        self.hawkes_lambda = min(self.hawkes_lambda, 100.0)
        self.last_trade_time = ts

        old = self.hawkes_history[0] if len(self.hawkes_history) == self.history_window else 0.0
        self.hawkes_history.append(self.hawkes_lambda)
        self.hawkes_sum += self.hawkes_lambda - old

        return self.hawkes_lambda

    def calculate_ofi_zscore(self, prev_book: Dict, curr_book: Dict) -> float:
        ofi_total = 0.0
        if not prev_book or not curr_book:
            return 0.0
        try:
            for i in range(self.levels):
                curr_bid_p, curr_bid_s = _safe_float(curr_book['bids'][i]['price']), _safe_float(curr_book['bids'][i]['size'])
                prev_bid_p, prev_bid_s = _safe_float(prev_book['bids'][i]['price']), _safe_float(prev_book['bids'][i]['size'])

                if curr_bid_p > prev_bid_p: delta_bid = curr_bid_s
                elif curr_bid_p == prev_bid_p: delta_bid = curr_bid_s - prev_bid_s
                else: delta_bid = -prev_bid_s

                curr_ask_p, curr_ask_s = _safe_float(curr_book['asks'][i]['price']), _safe_float(curr_book['asks'][i]['size'])
                prev_ask_p, prev_ask_s = _safe_float(prev_book['asks'][i]['price']), _safe_float(prev_book['asks'][i]['size'])

                if curr_ask_p < prev_ask_p: delta_ask = curr_ask_s
                elif curr_ask_p == prev_ask_p: delta_ask = curr_ask_s - prev_ask_s
                else: delta_ask = -prev_ask_s

                ofi_total += (delta_bid - delta_ask)
        except (KeyError, IndexError, TypeError):
            # treat malformed/partial book as "no signal" to prevent poisoning rolling stats
            return 0.0

        if not _is_finite(ofi_total):
            return 0.0

        old = self.ofi_history[0] if len(self.ofi_history) == self.history_window else 0.0
        self.ofi_history.append(ofi_total)
        self.short_ofi.append(ofi_total)

        # update rolling sums
        self.ofi_sum += ofi_total - old

        # protect rolling variance against overflow / inf poisoning
        if len(self.ofi_history) < self.history_window:
            ofi2 = ofi_total * ofi_total
            if not _is_finite(ofi2):
                # Safeguard: Revert state mutations to prevent desync
                self.ofi_history.pop()
                if self.short_ofi:
                    self.short_ofi.pop()
                self.ofi_sum -= ofi_total
                return 0.0
            self.ofi_sq_sum += ofi2
        else:
            # protect rolling variance against overflow / inf poisoning
            ofi2 = ofi_total * ofi_total
            old2 = old * old
            if not _is_finite(ofi2) or not _is_finite(old2):
                # reset rolling moments safely; keep history deque for continuity
                finite_hist = [v for v in self.ofi_history if _is_finite(v) and _is_finite(v * v)]
                if not finite_hist:
                    self.ofi_sum = 0.0
                    self.ofi_sq_sum = 0.0
                    return 0.0
                self.ofi_sum = sum(finite_hist)
                self.ofi_sq_sum = sum(v * v for v in finite_hist)
            else:
                self.ofi_sq_sum += ofi2 - old2

        if len(self.ofi_history) < 20:
            return 0.0

        n = len(self.ofi_history)
        ofi_mean = self.ofi_sum / n
        if not _is_finite(ofi_mean):
            return 0.0
        var = (self.ofi_sq_sum / n) - (ofi_mean * ofi_mean)
        if not _is_finite(var) or var <= 0.0:
            var = 1e-12
        ofi_std = math.sqrt(var)
        if not _is_finite(ofi_std) or ofi_std <= 1e-12:
            return 0.0

        z = (ofi_total - ofi_mean) / ofi_std
        return 4.0 * math.tanh(z / 3.0)

    def _detect_regime(self, ema_fast: float, ema_slow: float, buffer: float = 0.001) -> str:
        # fully dynamic buffer using normalized thresholds
        if ema_fast > ema_slow * (1 + buffer):
            return "UPTREND"
        elif ema_fast < ema_slow * (1 - buffer):
            return "DOWNTREND"
        return "RANGING"

    def detect_sweep_state(self, price: float, atr: float, hawkes_intensity: float) -> str:
        if self.liquidity_pools['high'] is None or self.liquidity_pools['low'] is None:
            return "NORMAL"
        if atr > 0 and (
            abs(_safe_float(self.liquidity_pools['high'], price) - price) > (atr * 10.0)
            and abs(price - _safe_float(self.liquidity_pools['low'], price)) > (atr * 10.0)
        ):
            self.liquidity_pools['high'] = None
            self.liquidity_pools['low'] = None
            return "NORMAL"

        dist_to_high = abs(self.liquidity_pools['high'] - price)
        dist_to_low = abs(price - self.liquidity_pools['low'])

        is_high_sweep = price >= self.liquidity_pools['high']
        is_low_sweep = price <= self.liquidity_pools['low']

        baseline = (self.hawkes_sum / max(1, len(self.hawkes_history))) if len(self.hawkes_history) > 5 else 1.0
        intensity_spike = hawkes_intensity >= baseline * 2.0

        thresholds = self._normalize_thresholds(atr, price)

        # --- NEW: compression-aware proximity ---
        compression_threshold = thresholds["compression"]

        near_level = (
            dist_to_high < thresholds["price_move"] or
            dist_to_low < thresholds["price_move"]
        )

        # NEW: compression regime filter (tight range = higher sweep probability)
        compression_condition = (
            (dist_to_high / (price + 1e-8) < compression_threshold) or
            (dist_to_low / (price + 1e-8) < compression_threshold)
        )

        if (is_high_sweep or is_low_sweep) and intensity_spike:
            return "ACTIVE_SWEEP"

        if (near_level or compression_condition) and intensity_spike:
            return "PRE_SWEEP_BUILDUP"

        return "NORMAL"

    def _detect_fake_breakout(self, sweep_side: str, close_price: float, ofi_z: float) -> Tuple[bool, float]:
        rejection_score = 0.0
        is_fake = False

        if sweep_side == "high":
            if self.liquidity_pools.get('high') is None:
                return False, 0.0
            if close_price < self.liquidity_pools['high']: 
                rejection_score += 0.5
            if ofi_z < -1.0: 
                rejection_score += 0.5
            is_fake = rejection_score >= 0.5

        elif sweep_side == "low":
            if self.liquidity_pools.get('low') is None:
                return False, 0.0
            if close_price > self.liquidity_pools['low']:
                rejection_score += 0.5
            if ofi_z > 1.0:
                rejection_score += 0.5
            is_fake = rejection_score >= 0.5

        return is_fake, rejection_score

    def check_resiliency(self, pre_depth: float, post_depth: float, time_elapsed: float, max_time: float = 2.0) -> float:
        if time_elapsed > max_time or pre_depth <= 0:
            return 0.0
        # avoid division blow-ups / timestamp jitter
        if time_elapsed <= 1e-3:
            return 0.0
        if post_depth < 0.0:
            post_depth = 0.0
        recovery_ratio = post_depth / pre_depth
        speed = (post_depth - pre_depth) / (time_elapsed + 1e-6)
        speed_score = _clamp(speed / (pre_depth + 1e-6))
        if recovery_ratio < self.resiliency_threshold:
            return 0.0
        return _clamp(0.5 * recovery_ratio + 0.5 * speed_score)

    def _ml_sweep_probability(self, features: Dict[str, float]) -> float:
        # lightweight logistic model (no external dependency)
        ofi = features.get("ofi", 0.0)
        hawkes = features.get("hawkes", 0.0)
        vol = features.get("volatility", 0.0)
        depth = features.get("depth", 1.0)

        # basic normalization
        vol = vol / (1 + vol)
        depth = depth / (1 + depth)

        # normalize inputs to avoid dominance
        ofi = _clamp(ofi / 3.0, -1.0, 1.0)
        hawkes = _clamp(hawkes / (1 + hawkes), 0.0, 1.0)

        z = (0.8 * ofi) + (0.6 * hawkes) + (0.4 * vol) - (0.5 * depth)

        # safe sigmoid
        if z >= 0:
            return 1 / (1 + math.exp(-z))
        else:
            exp_z = math.exp(z)
            return exp_z / (1 + exp_z)

    def _liquidity_forecast(self) -> float:
        if len(self.ofi_history) < 10:
            return 0.0
        val = sum(self.short_ofi) / len(self.short_ofi)
        # scale by rolling std if available to reduce symbol/size regime dependence
        n = len(self.ofi_history)
        if n >= 20:
            ofi_mean = self.ofi_sum / n
            var = (self.ofi_sq_sum / n) - (ofi_mean * ofi_mean)
            if _is_finite(var) and var > 1e-12:
                std = math.sqrt(var)
                val = val / (std + 1e-12)
        return max(-1.0, min(1.0, val))  # preserve directional bias

    def _predict_next_sweep(self, market_data: Dict, ofi_z: float, hawkes_now: float, hawkes_delta: float) -> Dict[str, float]:
        """
        Pure function for predicting directional probability. 
        Requires precomputed variables to avoid mutating history state.
        """
        price = _safe_float(market_data.get("price"))

        # Cold start check: return neutral if no price or uninitialized history
        if price <= 0.0 or len(self.ofi_history) < 10 or self.liquidity_pools.get("high") is None or self.liquidity_pools.get("low") is None:
            return {"prob_up": 0.5, "prob_down": 0.5}

        high_pool = self.liquidity_pools.get("high")
        low_pool = self.liquidity_pools.get("low")

        dist_above = abs(high_pool - price)
        dist_below = abs(price - low_pool)
        if dist_above < 1e-6 and dist_below < 1e-6:
            return {"prob_up": 0.5, "prob_down": 0.5}

        # --- Feature 1: Distance ---
        # FIX: symmetric + controlled scaling (avoid explosion)
        dist_ratio = math.log((dist_above + 1e-6) / (dist_below + 1e-6))
        dist_ratio = _clamp(dist_ratio, -5.0, 5.0)

        # --- Feature 2: OFI ---
        ofi_signal = math.tanh(ofi_z / 2.0)

        # --- Feature 3: Hawkes acceleration ---
        hawkes_norm = hawkes_now / (1.0 + hawkes_now)
        hawkes_signal = math.tanh(hawkes_delta) * hawkes_norm

        # --- Feature 4: Compression ---
        atr = _safe_float(market_data.get("atr", price * 0.01))
        compression = 1.0 - (atr / (price + 1e-6))
        compression = _clamp(compression, 0.0, 1.0)

        # --- Feature 5: Liquidity void ---
        bid_depth = _safe_float(market_data.get("bid_depth", 1.0))
        ask_depth = _safe_float(market_data.get("ask_depth", 1.0))
        if (bid_depth + ask_depth) < 1e-6:
            return {"prob_up": 0.5, "prob_down": 0.5}

        bid_depth = max(0.0, bid_depth)
        ask_depth = max(0.0, ask_depth)

        if ask_depth == 0.0 and bid_depth == 0.0:
            imbalance_norm = 0.0
        elif ask_depth == 0.0:
             imbalance_norm = 1.0
        elif bid_depth == 0.0:
             imbalance_norm = -1.0
        else:
             raw_imb = bid_depth / (ask_depth + 1e-6)
             raw_imb = _clamp(raw_imb, 0.01, 100.0)
             imbalance_norm = math.tanh(math.log(raw_imb))

        # --- Logistic model ---
        z = (
            -1.0 * dist_ratio +   # calibrated distance impact
            0.7 * ofi_signal +
            0.6 * hawkes_signal +
            0.5 * compression +
            0.4 * (-imbalance_norm)
        )

        prob_up = _standard_sigmoid(z)
        prob_down = 1.0 - prob_up

        return {
            "prob_up": prob_up,
            "prob_down": prob_down
        }

    def get_signal(self, market_data: Dict) -> Dict:
        """
        Main Engine Method. 
        Expects: price, close_price, prev_book, curr_book, timestamp, trades_count, 
                 pre_sweep_depth, curr_depth, sweep_time_elapsed, atr, ema_fast, ema_slow,
                 macro_liquidity (optional), macro_market_state (optional), macro_volume_intel (optional)
        """
        md = market_data if isinstance(market_data, dict) else {}  # local alias (latency)
        price = _safe_float(md.get('price'))
        if price <= 0.0:
            return _safe_output({
                "action": "HOLD",
                "confidence": 0.0,
                "state": "NORMAL",
                "regime": "RANGING",
                "ofi_zscore": 0.0,
                "hawkes_intensity": 0.0,
                "logic": "Invalid price",
                "micro_prob": 0.5,
                "macro_prob": 0.5,
                "prob_above": 0.5,
                "prob_below": 0.5,
            })
        close_price = _safe_float(md.get('close_price', price))
        atr = _safe_float(md.get('atr', price * 0.01)) + 1e-8
        if atr < 1e-8:
            atr = 1e-8
        vol_ratio = atr / (price + 1e-8)

        thresholds = self._normalize_thresholds(atr, price)

        # Calculate core signals and cache previous state for deltas
        prev_lambda = self.hawkes_lambda
        ofi_z = self.calculate_ofi_zscore(md.get('prev_book', {}), md.get('curr_book', {}))
        hawkes = self._update_hawkes(md.get('timestamp', time.time()), md.get('trades_count', 0))
        hawkes_delta = hawkes - prev_lambda

        regime = self._detect_regime(
            md.get('ema_fast', price),
            md.get('ema_slow', price),
            buffer=thresholds["trend_buffer"]
        )

        state = self.detect_sweep_state(price, atr, hawkes)

        # Microstructure Predictor
        micro_prediction = self._predict_next_sweep(md, ofi_z, hawkes, hawkes_delta)

        # Macro Structural Predictor
        macro_liquidity = md.get('macro_liquidity', {})
        macro_market_state = md.get('macro_market_state', {'state': regime, 'volatility': atr})
        macro_volume_intel = md.get('macro_volume_intel', {})
        macro_reliability = 1.0
        if not macro_liquidity or not isinstance(macro_liquidity, dict):
            macro_reliability = 0.5
        if not macro_market_state or not isinstance(macro_market_state, dict):
            macro_reliability *= 0.7

        macro_prediction = predict_sweep(macro_liquidity, macro_market_state, macro_volume_intel)

        # Handle macro fallback gracefully if pools are undefined
        macro_prob_up = macro_prediction.get("prob_above", 0.5)
        macro_prob_down = macro_prediction.get("prob_below", 0.5)
        micro_prob = None
        macro_prob = None

        action = "HOLD"
        confidence = 0.0
        logic_path = "No immediate edge"

        # Dynamically define sweep side contextually based on proximity to nearest pool
        high_pool = self.liquidity_pools.get('high')
        low_pool = self.liquidity_pools.get('low')
        if high_pool is not None and low_pool is not None:
            sweep_side = "high" if abs(high_pool - price) <= abs(price - low_pool) else "low"
        elif high_pool is not None:
            sweep_side = "high"
        elif low_pool is not None:
            sweep_side = "low"
        else:
            sweep_side = "high"

        # Progressive Confidence Gating: Replaces hard threshold with continuous scaler based on deque warmth.
        warmup_factor = min(1.0, len(self.ofi_history) / 20.0, len(self.hawkes_history) / 5.0)
        time_decay = math.exp(-0.01 * max(0.0, (time.time() - self.last_trade_time)))
        warmup_factor = _clamp(0.6 * warmup_factor + 0.4 * _clamp(time_decay, 0.3, 1.0), 0.0, 1.0)

        if state == "PRE_SWEEP_BUILDUP":
            # --- Early Anticipation Logic ---
            # For a breakout (anticipation), we want high probability that it continues *through* the level.
            # If approaching 'high', we want prob_up. If 'low', we want prob_down.
            pred_micro = micro_prediction["prob_up"] if sweep_side == "high" else micro_prediction["prob_down"]
            pred_macro = macro_prob_up if sweep_side == "high" else macro_prob_down
            pred_micro = _calibrate_prob(pred_micro)
            pred_macro = _calibrate_prob(pred_macro)
            pred_macro = _clamp(0.5 + (pred_macro - 0.5) * macro_reliability, 0.0, 1.0)
            micro_prob = _clamp(pred_micro, 0.0, 1.0)
            macro_prob = _clamp(pred_macro, 0.0, 1.0)

            # Feature Decorrelation: Softly reduce macro weight when microstructure z-score is highly active
            # This mathematically decorrelates structurally repetitive features mapped in both predictive sets.
            hawkes_term = math.tanh(hawkes / 5.0)
            corr_proxy = _clamp(
                0.6 * abs(ofi_z) / 3.0 +
                0.4 * hawkes_term,
                0.0,
                1.0,
            )
            macro_weight = max(0.15 * macro_reliability, 0.4 * (1.0 - corr_proxy) * macro_reliability)
            micro_weight = 1.0 - macro_weight

            # Logit Ensemble: Ensures proper probabilistic aggregation rather than linear weighting.
            final_logit = (micro_weight * _safe_logit(pred_micro, vol_ratio)) + (macro_weight * _safe_logit(pred_macro, vol_ratio))
            combined_prob = _standard_sigmoid(final_logit)
            min_history_factor = min(1.0, len(self.ofi_history) / 20.0)
            combined_prob *= (0.7 * min_history_factor + 0.3 * warmup_factor)

            # Execution threshold dynamically tightens when the system is cold
            threshold = 0.55 + 0.1 * (1.0 - warmup_factor)

            if combined_prob >= threshold:
                action = "BUY" if sweep_side == "high" else "SELL"
                # Calibrated Confidence: Confidence explicitly maps to normalized probability space.
                confidence = combined_prob
                logic_path = f"Anticipatory early entry on {sweep_side} buildup. Prob: {combined_prob:.2f}"
            else:
                logic_path = "Buildup detected, awaiting breach or stronger confirmation"

        elif state == "ACTIVE_SWEEP":
            if warmup_factor < 0.5:
                action = "HOLD"
                confidence = 0.0
                logic_path = "Active sweep detected but system not warmed up"
                return _safe_output({
                    "action": action,
                    "confidence": round(confidence, 4),
                    "state": state,
                    "regime": regime,
                    "ofi_zscore": round(ofi_z, 4),
                    "hawkes_intensity": round(hawkes, 4),
                    "logic": logic_path,
                    "micro_prob": 0.5,
                    "macro_prob": 0.5,
                    "prob_above": 0.5,
                    "prob_below": 0.5,
                })
            close_price = _safe_float(md.get("close_price", price))
            is_fake, rej_score = self._detect_fake_breakout(sweep_side, close_price, ofi_z)
            res_score = self.check_resiliency(
                md.get('pre_sweep_depth', 1.0), 
                md.get('curr_depth', 1.0), 
                md.get('sweep_time_elapsed', 0.0)
            )

            w_ofi, w_res, w_rej = 0.3, 0.4, 0.3
            ofi_component = _clamp(abs(ofi_z) / 3.0) 
            res_component = _clamp(res_score)

            raw_logit = (w_ofi * ofi_component) + (w_res * res_component) + (w_rej * rej_score)
            reaction_confidence = self._fast_sigmoid(raw_logit)

            trend_penalty = 0.0
            if (sweep_side == "high" and regime == "UPTREND") or (sweep_side == "low" and regime == "DOWNTREND"):
                trend_penalty = 0.2 

            reaction_confidence = _clamp(reaction_confidence - trend_penalty)

            ml_prob = self._ml_sweep_probability({
                "ofi": ofi_z,
                "hawkes": hawkes,
                "volatility": atr,
                "depth": md.get("curr_depth", 1.0)
            })

            liquidity_bias = self._liquidity_forecast()
            liq_prob = (liquidity_bias + 1.0) / 2.0

            # --- Mean Reversion (Fade) Logic ---
            # In an active sweep, we are looking for the fake-out / reversion.
            # If sweeping 'high', we want high prob_down. If 'low', we want prob_up.
            pred_micro = micro_prediction["prob_down"] if sweep_side == "high" else micro_prediction["prob_up"]
            pred_macro = macro_prob_down if sweep_side == "high" else macro_prob_up
            pred_micro = _calibrate_prob(pred_micro)
            pred_macro = _calibrate_prob(pred_macro)
            pred_macro = _clamp(0.5 + (pred_macro - 0.5) * macro_reliability, 0.0, 1.0)
            micro_prob = _clamp(pred_micro, 0.0, 1.0)
            macro_prob = _clamp(pred_macro, 0.0, 1.0)

            hawkes_term = math.tanh(hawkes / 5.0)
            corr_proxy = _clamp(
                0.6 * abs(ofi_z) / 3.0 +
                0.4 * hawkes_term,
                0.0,
                1.0,
            )
            macro_weight = max(0.15 * macro_reliability, 0.4 * (1.0 - corr_proxy) * macro_reliability)
            micro_weight = 1.0 - macro_weight

            # Predictors subset ensemble
            pred_logit = (micro_weight * _safe_logit(pred_micro, vol_ratio)) + (macro_weight * _safe_logit(pred_macro, vol_ratio))

            # Logit Ensemble: Full statistical mapping across all primary system variables.
            ensemble_logit = (
                0.40 * _safe_logit(reaction_confidence, vol_ratio) +
                0.25 * _safe_logit(ml_prob, vol_ratio) +
                0.15 * _safe_logit(liq_prob, vol_ratio) + 
                0.20 * pred_logit
            )
            ensemble_score = _standard_sigmoid(ensemble_logit)

            if ensemble_score >= 0.65 and is_fake:
                action = "SELL" if sweep_side == "high" else "BUY"
                confidence = ensemble_score * warmup_factor
                logic_path = f"Fake {sweep_side} sweep confirmed via logit ensemble."
            else:
                logic_path = f"True breakout / lack of reversion edge on {sweep_side} sweep."

        if regime == "RANGING":
            confidence *= 0.9
        if regime == "UPTREND" and action == "SELL":
            confidence *= 0.9
        if regime == "DOWNTREND" and action == "BUY":
            confidence *= 0.9

        # unify probability schema for engine compatibility
        if micro_prob is None:
            final_prob_up = 0.5
        else:
            if action == "BUY":
                final_prob_up = micro_prob
            elif action == "SELL":
                final_prob_up = 1.0 - micro_prob
            elif sweep_side == "high":
                final_prob_up = micro_prob
            elif sweep_side == "low":
                final_prob_up = 1.0 - micro_prob
            else:
                final_prob_up = 0.5
        final_prob_up = _clamp(_safe_float(final_prob_up, 0.5), 0.0, 1.0)
        final_prob_down = 1.0 - final_prob_up

        return _safe_output({
            "action": action,
            "confidence": confidence,
            "state": state,
            "regime": regime,
            "ofi_zscore": ofi_z,
            "hawkes_intensity": hawkes,
            "logic": logic_path,
            "micro_prob": micro_prob if micro_prob is not None else 0.5,
            "macro_prob": macro_prob if macro_prob is not None else 0.5,
            "prob_above": final_prob_up,
            "prob_below": final_prob_down,
        })
