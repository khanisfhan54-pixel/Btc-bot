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
