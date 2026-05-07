import numpy as np
from collections import deque


class MicrostructureFeatureEngine:
    """
    Produces a normalized feature vector for each tick.
    Output: np.ndarray of shape (6,) — feed directly as x_t into AdvancedRegimeEngine.

    Features (indices 0–5):
        0: OFI      — Order Flow Imbalance (z-scored)
        1: VWOI     — Volume-Weighted Order Imbalance (clipped [-1, 1])
        2: RV_5m    — 5-minute realized volatility (z-scored)
        3: Kyle_lam — Price impact per unit flow, log-transformed (z-scored)
        4: Spread   — Bid-ask spread ratio (z-scored)
        5: TradeImb — Buy/sell trade imbalance (clipped [-1, 1])
    """

    N_FEATURES: int = 6

    def __init__(self, lookback_bars: int = 200, rv_window_bars: int = 5):
        self.lookback = lookback_bars
        self.rv_window = rv_window_bars
        self._returns     = deque(maxlen=lookback_bars)
        self._ofi_hist    = deque(maxlen=lookback_bars)
        self._vwoi_hist   = deque(maxlen=lookback_bars)
        self._rv_hist     = deque(maxlen=lookback_bars)
        self._lam_hist    = deque(maxlen=lookback_bars)
        self._spread_hist = deque(maxlen=lookback_bars)
        self._timb_hist   = deque(maxlen=lookback_bars)
        self._prev_bid_size = None
        self._prev_ask_size = None
        self._price_buf   = deque(maxlen=rv_window_bars + 1)
        self._flow_buf    = deque(maxlen=20)
        self._dprice_buf  = deque(maxlen=20)

    def _compute_ofi(self, bid_size: float, ask_size: float) -> float:
        if self._prev_bid_size is None:
            self._prev_bid_size = bid_size
            self._prev_ask_size = ask_size
            return 0.0
        delta_bid = bid_size - self._prev_bid_size
        delta_ask = ask_size - self._prev_ask_size
        self._prev_bid_size = bid_size
        self._prev_ask_size = ask_size
        return float(delta_bid - delta_ask)

    def _compute_rv(self) -> float:
        buf = list(self._price_buf)
        if len(buf) < 2:
            return 0.0
        rets = np.diff(np.log(np.clip(buf, 1e-8, None)))
        return float(np.sqrt(np.sum(rets ** 2)))

    def _compute_kyle_lambda(self, dp: float, flow: float) -> float:
        self._dprice_buf.append(dp)
        self._flow_buf.append(flow)
        if len(self._dprice_buf) < 10:
            return 0.0
        dp_arr   = np.asarray(self._dprice_buf, dtype=float)
        flow_arr = np.asarray(self._flow_buf, dtype=float)
        flow_std = float(np.std(flow_arr))
        if flow_std < 1e-10:
            return 0.0
        cov = float(np.mean(dp_arr * flow_arr))
        var = float(np.mean(flow_arr ** 2))
        if var < 1e-12:
            return 0.0
        lam = cov / var
        return float(np.log1p(abs(lam)) * np.sign(lam))

    def _z_score(self, history: deque, value: float) -> float:
        arr = np.asarray(history, dtype=float)
        if len(arr) < 5:
            return 0.0
        mu  = float(arr.mean())
        std = float(arr.std())
        if std < 1e-10:
            return 0.0
        return float(np.clip((value - mu) / std, -3.0, 3.0))

    def update(
        self,
        mid_price: float,
        bid_price: float,
        ask_price: float,
        bid_size: float,
        ask_size: float,
        trade_flow: float,
        buy_volume: float,
        sell_volume: float,
    ) -> np.ndarray:
        """
        Call once per bar with current L2 snapshot.
        Returns normalized feature vector of shape (6,).
        No future data is used. All normalization is strictly backward-looking.
        """
        ofi_raw  = self._compute_ofi(bid_size, ask_size)

        total_vol = buy_volume + sell_volume + 1e-10
        vwoi_raw  = float((buy_volume - sell_volume) / total_vol)

        self._price_buf.append(mid_price)
        rv_raw = self._compute_rv()

        price_buf_list = list(self._price_buf)
        dp = float(mid_price - price_buf_list[-2]) if len(price_buf_list) >= 2 else 0.0
        lam_raw = self._compute_kyle_lambda(dp, trade_flow)

        spread_raw = float((ask_price - bid_price) / max(mid_price, 1e-8))

        timb_raw = float(np.sign(trade_flow) * min(abs(trade_flow) / (total_vol + 1e-10), 1.0))

        self._ofi_hist.append(ofi_raw)
        self._vwoi_hist.append(vwoi_raw)
        self._rv_hist.append(rv_raw)
        self._lam_hist.append(lam_raw)
        self._spread_hist.append(spread_raw)
        self._timb_hist.append(timb_raw)

        features = np.array([
            self._z_score(self._ofi_hist,    ofi_raw),
            float(np.clip(vwoi_raw, -1.0, 1.0)),
            self._z_score(self._rv_hist,     rv_raw),
            self._z_score(self._lam_hist,    lam_raw),
            self._z_score(self._spread_hist, spread_raw),
            float(np.clip(timb_raw, -1.0, 1.0)),
        ], dtype=float)

        # --- Production safety assertions ---
        assert not np.isnan(features).any(), "NaN in microstructure features"
        assert np.isfinite(features).all(), "Non-finite in microstructure features"
        assert features.shape == (self.N_FEATURES,), f"Shape mismatch: {features.shape}"

        return features
