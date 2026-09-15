from decimal import Decimal

from collector.collector.book_engine import LocalBook
from collector.collector.canonical import CanonicalOrderBookEvent
from collector.collector.quality_events import BookQuality
from collector.collector.websocket_client import WebSocketClient


def event(u, U, pu=None, bids=(("100.1", "1"),), asks=(("101.1", "1"),)):
    return CanonicalOrderBookEvent("BINANCE", "orderbook", None, None, 1,
        bids=tuple((Decimal(p), Decimal(q)) for p, q in bids), asks=tuple((Decimal(p), Decimal(q)) for p, q in asks),
        update_id=u, first_update_id=U, previous_update_id=pu)


def snapshot():
    return CanonicalOrderBookEvent("BINANCE", "orderbook", None, None, 2,
        bids=((Decimal("100.1"), Decimal("1")),), asks=((Decimal("101.1"), Decimal("1")),),
        update_id=10, is_snapshot=True)


def test_snapshot_does_not_skip_non_bridging_first_candidate_or_mutate_old_book():
    book = LocalBook("BINANCE")
    assert book.snapshot(event(5, 5))
    old_bids = dict(book.bids)
    # First non-stale event starts after the REST snapshot, even though a later event overlaps.
    book.buffer = [event(12, 12), event(13, 10, 12)]
    assert not book.binance_snapshot(10, snapshot())
    assert book.bids == old_bids
    assert book.state.state is BookQuality.RECOVERING
    assert [item.update_id for item in book.buffer] == [12, 13]


def test_sequence_gap_buffers_next_event_without_touching_authoritative_book():
    book = LocalBook("BINANCE")
    assert book.snapshot(event(10, 10))
    assert book.apply(event(12, 12, 9)) is None
    old_bids = dict(book.bids)
    assert book.apply(event(13, 13, 12)) is None
    assert book.bids == old_bids
    assert [item.update_id for item in book.buffer] == [12, 13]


def test_decimal_price_key_deletion_and_replacement_are_exact():
    book = LocalBook("BINANCE")
    assert book.snapshot(event(1, 1, bids=(("100.10", "1"),), asks=(("101.10", "1"),)))
    assert book._apply(event(2, 2, 1, bids=(("100.10", "2"),), asks=()))
    assert book.bids[Decimal("100.10")] == Decimal("2")
    assert book._apply(event(3, 3, 2, bids=(("100.10", "0"),), asks=()))
    assert Decimal("100.10") not in book.bids


def test_websocket_connection_ids_include_stable_stream_group():
    public = WebSocketClient("ws://one", lambda *_: None, stream_group="public")
    market = WebSocketClient("ws://two", lambda *_: None, stream_group="market")
    public._connection_serial = market._connection_serial = 1
    assert f"{public.stream_group}-{public._connection_serial}" != f"{market.stream_group}-{market._connection_serial}"
