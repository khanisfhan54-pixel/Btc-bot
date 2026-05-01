from __future__ import annotations

import json
from pathlib import Path

import pytest

import replay_feature_bridge


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_reads_l2_snapshot_and_preserves_feature_schema(tmp_path: Path):
    replay_file = tmp_path / "l2_replay_data.json"
    out_file = tmp_path / "replay_features.jsonl"
    _write_jsonl(
        replay_file,
        [
            {
                "local_timestamp": 1700000000.0,
                "price": 100000.0,
                "orderbook": {
                    "bids": [[99999.0, 1.5], [99998.0, 2.0]],
                    "asks": [[100001.0, 1.2], [100002.0, 2.5]],
                },
                "trades": [{"price": 100000.0, "size": 0.1, "side": "buy", "timestamp": 1700000000}],
            }
        ],
    )

    summary = replay_feature_bridge.run_replay_feature_bridge(replay_file, out_file)
    assert summary["rows"] == 1

    row = json.loads(out_file.read_text(encoding="utf-8").splitlines()[0])
    assert set(["features", "confidence", "line_no", "timestamp", "price"]).issubset(row.keys())
    assert "mid" in row["features"]
    assert "spread_bps" in row["features"]


def test_empty_or_partial_snapshot_fails_closed():
    with pytest.raises(ValueError, match="non-empty bids/asks"):
        replay_feature_bridge._normalize_snapshot({"orderbook": {"bids": [], "asks": []}, "trades": []})

    with pytest.raises(ValueError, match="trades must be a list"):
        replay_feature_bridge._normalize_snapshot({"bids": [[1, 1]], "asks": [[2, 1]], "trades": {}})


def test_deterministic_output_for_same_input(tmp_path: Path):
    replay_file = tmp_path / "replay.jsonl"
    rows = [
        {"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]], "price": 100.5, "timestamp": 1, "trades": []},
        {"bids": [[100.1, 1.2]], "asks": [[101.1, 1.1]], "price": 100.6, "timestamp": 2, "trades": []},
    ]
    _write_jsonl(replay_file, rows)

    out_a = tmp_path / "a.jsonl"
    out_b = tmp_path / "b.jsonl"
    replay_feature_bridge.run_replay_feature_bridge(replay_file, out_a)
    replay_feature_bridge.run_replay_feature_bridge(replay_file, out_b)

    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")


def test_bridge_does_not_import_live_execution_routes():
    source = Path(replay_feature_bridge.__file__).read_text(encoding="utf-8")
    forbidden = ["import execution", "import order_router", "import main", "ExecutionLogic", "place_order"]
    for token in forbidden:
        assert token not in source
