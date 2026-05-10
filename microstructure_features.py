from collections import deque
import numpy as np


class MicrostructureFeatureEngine:
    """
    Features (in order, indices 0–5):
        0: OFI      — Order Flow Imbalance (signed, delta bid_size - delta ask_size)
        1: VWOI     — Volume-Weighted Order Imbalance (bounded [-1,1])
        2: RV_5m    — 5-minute realized volatility (sqrt of sum of squared log-returns)
        3: Kyle_lam — Price impact per unit flow (log-transformed OLS slope)
        4: Spread   — Bid-ask spread ratio (ask-bid)/mid
        5: TradeImb — Buy/sell trade imbalance (signed, bounded [-1,1])
    """

    def __init__(self, lookback_bars: int = 200, rv_window_bars: int = 5):
        self._ofi_hist = deque(maxlen=lookback_bars)
        self._rv_hist = deque(maxlen=lookback_bars)
        self._kyle_hist = deque(maxlen=lookback_bars)
        self._spread_hist = deque(maxlen=lookback_bars)
        self._price_buf = deque(maxlen=rv_window_bars + 1)
        self._flow_buf = deque(maxlen=20)
        self._dp_buf = deque(maxlen=20)
        self._prev_bid_size = None
        self._prev_ask_size = None
        self._prev_mid_price = None

    def _compute_ofi(self, bid_size: float, ask_size: float) -> float:
        if self._prev_bid_size is None or self._prev_ask_size is None:
            self._prev_bid_size = float(bid_size)
            self._prev_ask_size = float(ask_size)
            return 0.0
        d_bid = float(bid_size) - float(self._prev_bid_size)
        d_ask = float(ask_size) - float(self._prev_ask_size)
        self._prev_bid_size = float(bid_size)
        self._prev_ask_size = float(ask_size)
        return float(d_bid - d_ask)

    def _compute_rv(self) -> float:
        if len(self._price_buf) < 2:
            return 0.0
        prices = np.asarray(self._price_buf, dtype=float)
        returns = np.diff(np.log(np.clip(prices, 1e-10, None)))
        return float(np.sqrt(np.sum(returns * returns)))

    def _compute_kyle_lambda(self, dp: float, flow: float) -> float:
        self._dp_buf.append(float(dp))
        self._flow_buf.append(float(flow))
        if len(self._flow_buf) < 5:
            return 0.0
        dp_arr = np.asarray(self._dp_buf, dtype=float)
        flow_arr = np.asarray(self._flow_buf, dtype=float)
        cov = float(np.mean((dp_arr - dp_arr.mean()) * (flow_arr - flow_arr.mean())))
        var_flow = float(np.mean((flow_arr - flow_arr.mean()) ** 2))
        if var_flow < 1e-10:
            raw = 0.0
        else:
            raw = cov / max(var_flow, 1e-10)
        return float(np.sign(raw) * np.log1p(abs(raw)))

    def _z_score(self, history: deque, value: float) -> float:
        if len(history) < 5:
            return 0.0
        arr = np.asarray(history, dtype=float)
        std = float(arr.std())
        if std < 1e-10:
            return 0.0
        z = (float(value) - float(arr.mean())) / max(std, 1e-10)
        return float(np.clip(z, -3.0, 3.0))

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
        ofi_raw = self._compute_ofi(bid_size=bid_size, ask_size=ask_size)
        self._ofi_hist.append(ofi_raw)
        ofi = self._z_score(self._ofi_hist, ofi_raw)

        total_vol = float(buy_volume) + float(sell_volume)
        vwoi = (float(buy_volume) - float(sell_volume)) / max(total_vol, 1e-10)
        vwoi = float(np.clip(vwoi, -1.0, 1.0))

        self._price_buf.append(float(mid_price))
        rv_raw = self._compute_rv()
        self._rv_hist.append(rv_raw)
        rv = self._z_score(self._rv_hist, rv_raw)

        dp = 0.0 if self._prev_mid_price is None else float(mid_price) - float(self._prev_mid_price)
        self._prev_mid_price = float(mid_price)
        kyle_raw = self._compute_kyle_lambda(dp=dp, flow=float(trade_flow))
        self._kyle_hist.append(kyle_raw)
        kyle = self._z_score(self._kyle_hist, kyle_raw)

        spread_raw = (float(ask_price) - float(bid_price)) / max(float(mid_price), 1e-10)
        self._spread_hist.append(spread_raw)
        spread = self._z_score(self._spread_hist, spread_raw)

        trade_imb = np.sign(float(trade_flow)) * min(abs(float(trade_flow)) / max(total_vol, 1e-10), 1.0)
        trade_imb = float(np.clip(trade_imb, -1.0, 1.0))

        return np.asarray([ofi, vwoi, rv, kyle, spread, trade_imb], dtype=float)


if __name__ == "__main__":
    import numpy as np
    eng = MicrostructureFeatureEngine(lookback_bars=50)
    for i in range(30):
        feats = eng.update(
            mid_price=50000.0 + i,
            bid_price=49999.0 + i,
            ask_price=50001.0 + i,
            bid_size=10.0 + i * 0.1,
            ask_size=9.5 + i * 0.1,
            trade_flow=float(1 if i % 2 == 0 else -1) * (i + 1),
            buy_volume=500.0 + i * 5,
            sell_volume=450.0 + i * 3,
        )
    assert feats.shape == (6,), f"Expected shape (6,), got {feats.shape}"
    assert np.all(np.isfinite(feats)), f"Non-finite features: {feats}"
    print("MicrostructureFeatureEngine smoke test PASSED:", feats)
