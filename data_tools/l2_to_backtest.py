"""Strict read-only L2 helpers for research/backtest validation."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class BookSnapshot:
    timestamp: int
    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float
    spread_bps: float
    imbalance: float
    ofi_z: float


def _f(v: Any) -> float:
    return float(v)


def _finite(v: float) -> bool:
    return isinstance(v, float) and math.isfinite(v)


def load_l2_csv(path: str) -> list[BookSnapshot]:
    """Strictly load CSV rows into canonical snapshots; fail closed on bad OFI."""
    out: list[BookSnapshot] = []
    with open(path, "r", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for i, row in enumerate(rd, start=2):
            ts_raw = row.get("timestamp") or row.get("ts") or row.get("T")
            if ts_raw is None:
                raise ValueError(f"{path}: missing timestamp at csv line {i}")
            try:
                ts = int(float(ts_raw))
            except Exception as e:
                raise ValueError(f"{path}: invalid timestamp at csv line {i}: {ts_raw}") from e
            try:
                bid = _f(row.get("bidPrice", row.get("bid_price", row.get("bid"))))
                ask = _f(row.get("askPrice", row.get("ask_price", row.get("ask"))))
                bq = _f(row.get("bidQty", row.get("bid_qty", row.get("bid_size"))))
                aq = _f(row.get("askQty", row.get("ask_qty", row.get("ask_size"))))
            except Exception as e:
                raise ValueError(f"{path}: invalid bid/ask fields at csv line {i}") from e
            if bid <= 0.0 or ask <= 0.0 or ask < bid:
                raise ValueError(f"{path}: invalid top-of-book prices at csv line {i} (bid={bid}, ask={ask})")
            ofi_raw = row.get("ofi_z")
            if ofi_raw is None or str(ofi_raw).strip() == "":
                raise ValueError(f"{path}: missing required ofi_z at csv line {i}")
            try:
                ofi_z = float(ofi_raw)
            except Exception as e:
                raise ValueError(f"{path}: invalid ofi_z at csv line {i}: {ofi_raw}") from e
            if not _finite(ofi_z):
                raise ValueError(f"{path}: non-finite ofi_z at csv line {i}: {ofi_raw}")
            mid = (bid + ask) * 0.5
            spread_bps = ((ask - bid) / mid) * 10000.0
            imbalance = (bq - aq) / max(bq + aq, 1e-12)
            out.append(BookSnapshot(ts, bid, ask, bq, aq, spread_bps, imbalance, ofi_z))
    out.sort(key=lambda s: s.timestamp)
    return out


def align_book_to_bars(bars: Sequence[Sequence[Any]], snaps: Iterable[Any]) -> list[BookSnapshot]:
    """Strict deterministic aligner: each bar must map to a valid snapshot <= bar ts."""
    rows = sorted(list(snaps), key=lambda x: int(x.timestamp if hasattr(x, "timestamp") else x["timestamp"]))
    if not rows:
        raise RuntimeError("BLOCKER: no snapshots provided")
    out: list[BookSnapshot] = []
    j = 0
    cur: Any = None
    for idx, bar in enumerate(bars):
        ts = int(bar[0])
        while j < len(rows) and int(rows[j].timestamp if hasattr(rows[j], "timestamp") else rows[j]["timestamp"]) <= ts:
            cur = rows[j]
            j += 1
        if cur is None:
            raise RuntimeError(f"BLOCKER: no snapshot aligned to bar {idx} ts={ts}")
        snap = cur if isinstance(cur, BookSnapshot) else BookSnapshot(
            timestamp=int(cur["timestamp"]), bid_price=float(cur["bid_price"]), ask_price=float(cur["ask_price"]),
            bid_qty=float(cur["bid_qty"]), ask_qty=float(cur["ask_qty"]), spread_bps=float(cur["spread_bps"]),
            imbalance=float(cur["imbalance"]), ofi_z=float(cur["ofi_z"]),
        )
        if snap.timestamp > ts:
            raise RuntimeError(f"BLOCKER: forward-looking alignment at bar_index={idx} bar_ts={ts} snap_ts={snap.timestamp}")
        if not _finite(float(snap.ofi_z)):
            raise RuntimeError(f"BLOCKER: ofi_z non-finite at bar {idx}")
        out.append(snap)
    return out
