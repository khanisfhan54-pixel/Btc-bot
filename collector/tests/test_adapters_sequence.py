from collector.collector.adapters import BinanceAdapter, BybitAdapter, OKXAdapter
from collector.collector.canonical import CanonicalMarkPriceEvent, CanonicalOIEvent, CanonicalOrderBookEvent
from collector.collector.sequence import BinanceSequenceComparator, BybitSequenceComparator, OKXSequenceComparator

def book(update, previous=None):
    return CanonicalOrderBookEvent("X", "orderbook", 1, None, 1, update_id=update, previous_update_id=previous)

def test_binance_futures_bridge_has_no_spot_plus_one():
    event = CanonicalOrderBookEvent("BINANCE", "orderbook", 1, 1, 1, update_id=100, first_update_id=100)
    assert BinanceAdapter.bridge_accepts(event, 100)
    assert "/fapi/v1/aggTrades" in BinanceAdapter.aggregate_trades_reseed_url()
    assert "/fapi/v1/trades" not in BinanceAdapter.aggregate_trades_reseed_url()

def test_venue_sequence_semantics_are_distinct():
    assert BinanceSequenceComparator().check(book(11, 9), book(10, 8)).is_gap
    reset = BybitSequenceComparator().check(book(1), book(12))
    assert reset.is_resync_signal and not reset.is_gap
    assert OKXSequenceComparator().check(book(50, -1), book(40, 30)).is_gap is False
    assert OKXSequenceComparator().check(book(60, 50), book(50, 1)).is_gap is False

def test_bybit_ticker_fans_out_and_uses_all_liquidations():
    adapter = BybitAdapter()
    events = adapter.normalize({"topic":"tickers.BTCUSDT", "ts":10, "data":{"markPrice":"1", "openInterest":"2"}}, local_receive_ts=11)
    assert {type(event) for event in events} == {CanonicalMarkPriceEvent, CanonicalOIEvent}
    assert "allLiquidation.BTCUSDT" in adapter.channel_event_types
    assert not any(key.startswith("liquidation.") for key in adapter.channel_event_types)

def test_okx_does_not_fabricate_transaction_timestamp():
    event = OKXAdapter().normalize({"arg":{"channel":"books"}, "data":[{"ts":"1", "seqId":20, "prevSeqId":-1, "bids":[["1","2"]], "asks":[["3","4"]]}]}, local_receive_ts=2)[0]
    assert event.exchange_transaction_ts is None


def _diff(u, U=None, pu=None):
    return CanonicalOrderBookEvent("BINANCE", "orderbook", 1, None, 1,
        bids=((100.0, 1.0),), asks=((101.0, 1.0),), update_id=u,
        first_update_id=u if U is None else U, previous_update_id=pu)


def _snapshot(update):
    return CanonicalOrderBookEvent("BINANCE", "orderbook", None, None, 10,
        bids=((100.0, 1.0),), asks=((101.0, 1.0),), update_id=update, is_snapshot=True)


def test_binance_local_book_snapshot_bridges_and_discards_stale_diffs():
    from collector.collector.book_engine import LocalBook
    book = LocalBook("BINANCE")
    # u < lastUpdateId is stale, while equality remains a valid bridge.
    book.buffer = [_diff(9, 9), _diff(10, 9), _diff(11, 11, 10)]
    assert book.binance_snapshot(10, _snapshot(10))
    assert book.previous.update_id == 11
    assert book.state.state.value == "VALID"


def test_binance_local_book_bridge_range_and_gap_retains_unproven_diffs():
    from collector.collector.book_engine import LocalBook
    book = LocalBook("BINANCE")
    book.buffer = [_diff(12, 8), _diff(13, 13, 99)]
    assert not book.binance_snapshot(10, _snapshot(10))
    assert book.last_reason == "pu_mismatch"
    # The temporary chain never committed, therefore neither event is applied
    # and both remain available to the next recovery transaction.
    assert [event.update_id for event in book.buffer] == [12, 13]
    assert book.state.state.value == "SEQUENCE_GAP"


def test_binance_local_book_no_bridge_and_malformed_update_are_not_valid():
    from collector.collector.book_engine import LocalBook
    book = LocalBook("BINANCE")
    book.buffer = [_diff(9, 9)]
    assert not book.binance_snapshot(10, _snapshot(10))
    assert book.state.state.value == "RECOVERING"
    malformed = CanonicalOrderBookEvent("BINANCE", "orderbook", 1, None, 1,
        bids=((100.0, 1.0),), asks=((101.0, 1.0),), update_id=12, first_update_id=None)
    book.buffer = [malformed]
    assert not book.binance_snapshot(10, _snapshot(10))
