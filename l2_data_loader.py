"""
L2 Data Loader (U-03)
=====================
Real L2 CSV replay loader with strict book-format validation.
Never crashes the backtest — invalid rows are logged and skipped.

CSV schema (configurable column names):
    timestamp, bid_1_price, bid_1_size, ..., bid_N_price, bid_N_size,
               ask_1_price, ask_1_size, ..., ask_N_price, ask_N_size
"""
from __future__ import annotations

import csv
import logging
import math
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("l2_data_loader")


def _is_finite_number(x) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v)


def validate_book_format(book: dict) -> Tuple[bool, str]:
    """Strict per-row validation. Returns (ok, reason)."""
    if not isinstance(book, dict):
        return False, "book is not a dict"
    bids = book.get("bids")
    asks = book.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return False, "bids/asks missing or not lists"
    if len(bids) == 0 or len(asks) == 0:
        return False, "empty bids or asks"
    for side_name, side in (("bid", bids), ("ask", asks)):
        for i, lvl in enumerate(side):
            if not isinstance(lvl, dict):
                return False, f"{side_name}[{i}] not a dict"
            p = lvl.get("price")
            s = lvl.get("size")
            if not _is_finite_number(p) or not _is_finite_number(s):
                return False, f"{side_name}[{i}] non-finite price/size"
            if float(s) < 0.0:
                return False, f"{side_name}[{i}] negative size"
    if float(bids[0]["price"]) >= float(asks[0]["price"]):
        return False, "crossed book (best bid >= best ask)"
    return True, "ok"


class L2CSVReplayLoader:
    """
    Reads an L2 CSV file and yields a list of validated book snapshots.

    Args:
        filepath:         Path to CSV.
        price_col:        Name of price column when CSV uses long format
                          (one row per level). Default 'price'.
        bid_col_prefix:   Column prefix for bid levels in wide-format CSVs.
        ask_col_prefix:   Column prefix for ask levels in wide-format CSVs.
        levels:           Max L2 levels to extract per snapshot.
        ts_col:           Timestamp column name (default 'timestamp').
    """

    def __init__(self,
                 filepath: str,
                 price_col: str = "price",
                 bid_col_prefix: str = "bid",
                 ask_col_prefix: str = "ask",
                 levels: int = 10,
                 ts_col: str = "timestamp",
                 detect_schema: bool = True):
        self.filepath = filepath
        self.price_col = price_col
        self.bid_col_prefix = bid_col_prefix
        self.ask_col_prefix = ask_col_prefix
        self.levels = int(levels)
        self.ts_col = ts_col
        self.n_rows_read = 0
        self.detect_schema = detect_schema
        self._schema_verdict: Optional[str] = None
        self.n_rows_skipped = 0
        self.skip_reasons: Dict[str, int] = {}

    def _parse_row_wide(self, row: dict) -> Optional[dict]:
        """Wide-format: columns 'bid_1_price','bid_1_size',...,'ask_1_price',..."""
        bids: List[dict] = []
        asks: List[dict] = []
        for i in range(1, self.levels + 1):
            bp = row.get(f"{self.bid_col_prefix}_{i}_price")
            bs = row.get(f"{self.bid_col_prefix}_{i}_size")
            ap = row.get(f"{self.ask_col_prefix}_{i}_price")
            asize = row.get(f"{self.ask_col_prefix}_{i}_size")
            if bp is None or bs is None or ap is None or asize is None:
                break
            try:
                bids.append({"price": float(bp), "size": float(bs)})
                asks.append({"price": float(ap), "size": float(asize)})
            except (TypeError, ValueError):
                return None
        if not bids or not asks:
            return None
        ts = row.get(self.ts_col)
        try:
            ts_val = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_val = 0.0
        return {"timestamp": ts_val, "bids": bids, "asks": asks}


    def _probe_schema(self) -> str:
        """Read first 20 rows to classify CSV schema. Returns 'absolute', 'relative_pct', or 'unknown'."""
        vals: List[float] = []
        try:
            with open(self.filepath, "r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    v = row.get(f"{self.bid_col_prefix}_1_price")
                    if v is None:
                        continue
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        continue
                    if len(vals) >= 20:
                        break
        except Exception:
            return "unknown"
        if not vals:
            return "unknown"
        if any(abs(v) > 100 for v in vals):
            return "absolute"
        in_small = all(-100 < v < 100 for v in vals)
        negatives = sum(1 for v in vals if v < 0)
        spans_zero = (min(vals) < 0 < max(vals))
        if in_small and (negatives > (len(vals) / 2) or spans_zero):
            return "relative_pct"
        return "unknown"

    def load(self) -> List[Dict]:
        """Read the file and return validated book snapshots."""
        snapshots: List[Dict] = []
        self._schema_verdict = self._probe_schema()
        if self.detect_schema and self._schema_verdict == "relative_pct":
            log.error("L2CSVReplayLoader: SCHEMA MISMATCH detected in '%s'. CSV contains relative percentage depths, not absolute prices. OFI calculations would be invalid. Refusing to load. Re-export data with absolute bid/ask prices.", self.filepath)
            raise ValueError(f"L2 schema mismatch: '{self.filepath}' uses relative percentage depths. Pass detect_schema=False only if you have verified the data independently.")
        try:
            with open(self.filepath, "r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    self.n_rows_read += 1
                    book = self._parse_row_wide(row)
                    if book is None:
                        self.n_rows_skipped += 1
                        self.skip_reasons["parse_error"] = (
                            self.skip_reasons.get("parse_error", 0) + 1
                        )
                        log.warning("L2 row %d: parse error — skipped",
                                    self.n_rows_read)
                        continue
                    ok, reason = validate_book_format(book)
                    if not ok:
                        self.n_rows_skipped += 1
                        self.skip_reasons[reason] = (
                            self.skip_reasons.get(reason, 0) + 1
                        )
                        log.warning("L2 row %d: %s — skipped",
                                    self.n_rows_read, reason)
                        continue
                    snapshots.append(book)
        except FileNotFoundError:
            log.error("L2 CSV not found: %s", self.filepath)
            raise
        return snapshots

    def stats(self) -> Dict[str, object]:
        return {
            "n_rows_read": self.n_rows_read,
            "n_rows_skipped": self.n_rows_skipped,
            "skip_reasons": dict(self.skip_reasons),
            "schema_verdict": self._schema_verdict,
        }
