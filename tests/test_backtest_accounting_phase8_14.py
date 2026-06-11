from backtest_engine import calculate_funding_payment, calculate_trade_pnl, simulate_queue_fill


def test_positive_funding_credits_short_equity():
    assert calculate_funding_payment(side="SHORT", entry_price=100.0, size=2.0, funding_rate=0.01, bar_interval_hours=8.0, funding_interval_hours=8.0) == 2.0


def test_negative_funding_debits_short_equity():
    assert calculate_funding_payment(side="SHORT", entry_price=100.0, size=2.0, funding_rate=-0.01, bar_interval_hours=8.0, funding_interval_hours=8.0) == -2.0


def test_flat_funding_has_no_equity_effect():
    assert calculate_funding_payment(side="LONG", entry_price=100.0, size=2.0, funding_rate=0.0, bar_interval_hours=8.0, funding_interval_hours=8.0) == 0.0


def test_trade_pnl_uses_position_quantity_not_balance_multiplier():
    pnl, ret = calculate_trade_pnl(side="LONG", entry_price=100.0, exit_price=110.0, size=2.0, fee_pct=0.0, slippage_pct=0.0)
    assert pnl == 20.0
    assert ret == 0.10


def test_queue_fill_100_percent():
    fill, fraction = simulate_queue_fill(side="buy", remaining_qty=4.0, features={"fill_prob_long": 1.0, "fill_confidence": 1.0, "top_ask_qty": 10.0})
    assert fill == 4.0
    assert fraction == 1.0


def test_queue_fill_50_percent():
    fill, fraction = simulate_queue_fill(side="buy", remaining_qty=4.0, features={"fill_prob_long": 0.5, "fill_confidence": 1.0, "top_ask_qty": 10.0})
    assert fill == 2.0
    assert fraction == 0.5


def test_queue_fill_0_percent():
    fill, fraction = simulate_queue_fill(side="sell", remaining_qty=4.0, features={"fill_prob_short": 0.0, "fill_confidence": 1.0, "top_bid_qty": 10.0})
    assert fill == 0.0
    assert fraction == 0.0
