from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from collector.run_collector import CollectorApp
from collector.collector.adapters.binance import BinanceAdapter
from collector.collector.book_engine import LocalBook
from collector.collector.canonical import CanonicalOrderBookEvent


def _event(u, U, pu=None):
    return CanonicalOrderBookEvent("BINANCE", "orderbook", 1, None, 2,
        bids=((Decimal("100.10000001"), Decimal("1.00000001")),),
        asks=((Decimal("101"), Decimal("1")),), update_id=u,
        first_update_id=U, previous_update_id=pu)


def _snapshot():
    return CanonicalOrderBookEvent("BINANCE", "orderbook", None, None, 2,
        bids=((Decimal("100"), Decimal("1")),), asks=((Decimal("101"), Decimal("1")),),
        update_id=10, is_snapshot=True)


def test_usdm_equality_is_a_valid_first_snapshot_bridge():
    book = LocalBook("BINANCE")
    # u < L is stale; an equality event that overlaps L is the first bridge.
    book.buffer = [_event(9, 9), _event(10, 9)]
    assert book.binance_snapshot(10, _snapshot())
    assert book.previous.update_id == 10
    assert book.state.state.value == "VALID"


def test_usdm_does_not_skip_invalid_first_non_stale_bridge_candidate():
    book = LocalBook("BINANCE")
    # The first non-stale candidate starts after L, even though a later event
    # would overlap it; recovery must not forward-search for that later bridge.
    book.buffer = [_event(9, 9), _event(12, 12), _event(13, 10, 12)]
    assert not book.binance_snapshot(10, _snapshot())
    assert book.last_reason == "snapshot_bridge_not_found"
    assert [e.update_id for e in book.buffer] == [9, 12, 13]


def test_recovery_provenance_and_decimal_raw_persistence_are_exact():
    app = CollectorApp.__new__(CollectorApp)
    app.raw_book_writer = MagicMock()
    book = LocalBook("BINANCE")
    book.buffer = [_event(11, 10), _event(12, 12, 11)]
    assert book.binance_snapshot(10, _snapshot())
    app._persist_reconstructed_books(book.committed_recovery_events)
    bridge, incremental = [call.args[0] for call in app.raw_book_writer.write.call_args_list]
    assert bridge["event_kind"] == "RECOVERY_BRIDGE"
    assert bridge["recovery_generation"] == 1
    assert incremental["event_kind"] == "RECOVERY_INCREMENTAL"
    assert bridge["bids"][0] == ["100.10000001", "1.00000001"]


@pytest.mark.parametrize("native_id", ["9223372036854775808", None, "venue-id-A"])
def test_raw_trade_is_written_before_nullable_legacy_conversion(native_id):
    app = CollectorApp.__new__(CollectorApp)
    app.stream_counters = {"trades": {"received": 0, "rejected": 0}}
    app.validation_fail_reasons = {"trades": {}}
    app.binance_adapter = BinanceAdapter()
    app.raw_trades_writer = MagicMock()
    app._handle_trade_features = MagicMock()
    app._persist_quality_event = MagicMock()
    raw = {"e": "aggTrade", "E": 1, "T": 1, "a": native_id, "p": "100", "q": "1", "m": False}
    app._handle_binance_trade(raw, "btcusdt@aggtrade", 2)
    row = app.raw_trades_writer.write.call_args.args[0]
    assert row["native_trade_id"] == native_id
    assert row["trade_id"] is None
    app._handle_trade_features.assert_not_called()
