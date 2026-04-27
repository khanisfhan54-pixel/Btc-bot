from unittest.mock import patch

import main


def test_execute_liquidity_trade_does_not_double_shift_execution_space():
    candles = {"1h": [[0, 100, 101, 99, 100, 1]] * 20}
    engines = {"liquidity_sweep": {"side": "BUY", "sweep": True}}
    with patch.object(main, "LIVE_TRADING", False), patch.object(main, "send_telegram_message", return_value=True):
        out = main._execute_liquidity_trade(
            execution_signal="LONG",
            price=101.0,
            confidence=0.8,
            candles_by_tf=candles,
            engines_out=engines,
            analysis_price=100.0,
            sl_price=99.0,
            tp_price=103.0,
            sl_tp_price_space="execution",
            position_size=1.0,
        )
    assert out["paper"] is True
    assert abs(out["sl"] - 99.0) < 1e-9
    assert abs(out["tp"] - 103.0) < 1e-9
    assert abs(out["analysis_price"] - 100.0) < 1e-9
    assert abs(out["execution_price"] - 101.0) < 1e-9
    assert abs(out["basis_delta"] - 1.0) < 1e-9


def test_execute_liquidity_trade_converts_analysis_space_once():
    candles = {"1h": [[0, 100, 101, 99, 100, 1]] * 20}
    engines = {"liquidity_sweep": {"side": "BUY", "sweep": True}}
    with patch.object(main, "LIVE_TRADING", False), patch.object(main, "send_telegram_message", return_value=True):
        out = main._execute_liquidity_trade(
            execution_signal="LONG",
            price=101.0,
            confidence=0.8,
            candles_by_tf=candles,
            engines_out=engines,
            analysis_price=100.0,
            sl_price=98.0,
            tp_price=104.0,
            sl_tp_price_space="analysis",
            position_size=1.0,
        )
    assert out["paper"] is True
    assert abs(out["sl"] - 99.0) < 1e-9
    assert abs(out["tp"] - 105.0) < 1e-9


def test_execute_liquidity_trade_live_order_receives_execution_space_values():
    candles = {"1h": [[0, 100, 101, 99, 100, 1]] * 20}
    engines = {"liquidity_sweep": {"side": "BUY", "sweep": True}}
    with patch.object(main, "LIVE_TRADING", True), patch.object(main, "send_telegram_message", return_value=True), patch.object(
        main.engine, "place_order_with_sl_tp", return_value={"id": "oid-1"}
    ) as place_mock:
        out = main._execute_liquidity_trade(
            execution_signal="LONG",
            price=101.0,
            confidence=0.8,
            candles_by_tf=candles,
            engines_out=engines,
            analysis_price=100.0,
            sl_price=99.0,
            tp_price=103.0,
            sl_tp_price_space="execution",
            position_size=1.0,
        )
    assert out["executed"] is True
    args, _kwargs = place_mock.call_args
    assert abs(args[3] - 99.0) < 1e-9
    assert abs(args[4] - 103.0) < 1e-9


def test_execute_liquidity_trade_live_order_analysis_space_converts_once():
    candles = {"1h": [[0, 100, 101, 99, 100, 1]] * 20}
    engines = {"liquidity_sweep": {"side": "BUY", "sweep": True}}
    with patch.object(main, "LIVE_TRADING", True), patch.object(main, "send_telegram_message", return_value=True), patch.object(
        main.engine, "place_order_with_sl_tp", return_value={"id": "oid-2"}
    ) as place_mock:
        out = main._execute_liquidity_trade(
            execution_signal="LONG",
            price=101.0,
            confidence=0.8,
            candles_by_tf=candles,
            engines_out=engines,
            analysis_price=100.0,
            sl_price=98.0,
            tp_price=104.0,
            sl_tp_price_space="analysis",
            position_size=1.0,
        )
    assert out["executed"] is True
    args, _kwargs = place_mock.call_args
    assert abs(args[3] - 99.0) < 1e-9
    assert abs(args[4] - 105.0) < 1e-9


def test_execute_liquidity_trade_invalid_price_space_fails_closed():
    candles = {"1h": [[0, 100, 101, 99, 100, 1]] * 20}
    engines = {"liquidity_sweep": {"side": "BUY", "sweep": True}}
    with patch.object(main, "LIVE_TRADING", False), patch.object(main, "send_telegram_message", return_value=True):
        out = main._execute_liquidity_trade(
            execution_signal="LONG",
            price=101.0,
            confidence=0.8,
            candles_by_tf=candles,
            engines_out=engines,
            analysis_price=100.0,
            sl_price=98.0,
            tp_price=104.0,
            sl_tp_price_space="bad-space",
            position_size=1.0,
        )
    assert out["executed"] is False
    assert out["reason"] == "invalid_sl_tp_price_space"
