import pandas as pd

from collector.scripts.verify_dataset import verify_date


DATE_STR = "2026-06-01"
STREAMS = ("orderbook", "trades", "markprice")


def _row(stream, hour):
    timestamp = pd.Timestamp(f"{DATE_STR} {hour:02d}:00:00", tz="UTC")
    row = {"timestamp": timestamp}
    if stream == "trades":
        row["trade_id"] = hour
    return row


def _write_dataset(tmp_path, overrides=None):
    overrides = overrides or {}
    for stream in STREAMS:
        stream_dir = tmp_path / "raw" / stream
        stream_dir.mkdir(parents=True)
        for hour in range(24):
            rows = overrides.get((stream, hour), [_row(stream, hour)])
            pd.DataFrame(rows).to_parquet(stream_dir / f"{DATE_STR}-{hour:02d}.parquet")


def test_trades_same_timestamp_different_trade_ids_passes(tmp_path):
    shared_timestamp = pd.Timestamp(f"{DATE_STR} 00:00:00", tz="UTC")
    _write_dataset(
        tmp_path,
        {
            ("trades", 0): [
                {"timestamp": shared_timestamp, "trade_id": 1},
                {"timestamp": shared_timestamp, "trade_id": 2},
            ]
        },
    )

    assert verify_date(DATE_STR, data_dir=str(tmp_path)) is True


def test_trades_same_timestamp_same_trade_id_fails(tmp_path):
    shared_timestamp = pd.Timestamp(f"{DATE_STR} 00:00:00", tz="UTC")
    _write_dataset(
        tmp_path,
        {
            ("trades", 0): [
                {"timestamp": shared_timestamp, "trade_id": 1},
                {"timestamp": shared_timestamp, "trade_id": 1},
            ]
        },
    )

    assert verify_date(DATE_STR, data_dir=str(tmp_path)) is False


def test_orderbook_duplicate_timestamp_fails(tmp_path):
    shared_timestamp = pd.Timestamp(f"{DATE_STR} 00:00:00", tz="UTC")
    _write_dataset(
        tmp_path,
        {
            ("orderbook", 0): [
                {"timestamp": shared_timestamp},
                {"timestamp": shared_timestamp},
            ]
        },
    )

    assert verify_date(DATE_STR, data_dir=str(tmp_path)) is False
