from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from feature_engine import FeatureEngine


def _normalize_snapshot(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise ValueError("snapshot line must be a JSON object")

    orderbook = raw.get("orderbook") if isinstance(raw.get("orderbook"), dict) else raw
    bids = orderbook.get("bids")
    asks = orderbook.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
        raise ValueError("snapshot must include non-empty bids/asks")

    snapshot = {
        "bids": bids,
        "asks": asks,
        "timestamp": raw.get("timestamp") or raw.get("local_timestamp") or raw.get("ts"),
        "price": raw.get("price"),
    }

    trades = raw.get("trades", [])
    if trades is None:
        trades = []
    if not isinstance(trades, list):
        raise ValueError("trades must be a list")

    regime_context = raw.get("regime_context")
    if regime_context is not None and not isinstance(regime_context, dict):
        raise ValueError("regime_context must be a dict when present")

    return snapshot, trades, regime_context


def iter_replay_features(replay_file: str | Path, *, max_levels: int = 10) -> Iterator[Dict[str, Any]]:
    engine = FeatureEngine(max_levels=max_levels)
    path = Path(replay_file)
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            raw = json.loads(stripped)
            snapshot, trades, regime_context = _normalize_snapshot(raw)
            payload = engine.update(snapshot, trades, regime_context=regime_context)
            yield {
                "line_no": line_no,
                "timestamp": snapshot.get("timestamp"),
                "price": snapshot.get("price"),
                "features": payload.get("features", {}),
                "confidence": payload.get("confidence", 0.0),
            }


def run_replay_feature_bridge(
    replay_file: str | Path = "l2_replay_data.json",
    output_file: str | Path = "replay_features.jsonl",
    *,
    max_levels: int = 10,
) -> Dict[str, Any]:
    in_path = Path(replay_file)
    out_path = Path(output_file)

    count = 0
    with out_path.open("w", encoding="utf-8") as out:
        for item in iter_replay_features(in_path, max_levels=max_levels):
            out.write(json.dumps(item, sort_keys=True) + "\n")
            count += 1

    return {"input": str(in_path), "output": str(out_path), "rows": count}


if __name__ == "__main__":
    summary = run_replay_feature_bridge()
    print(json.dumps(summary, indent=2, sort_keys=True))
