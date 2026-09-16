"""Crash-bounded raw parquet storage.

Parquet row groups are only discoverable from the footer.  An open writer is
therefore *not* recoverable after a process crash.  This module deliberately
uses small, closed segments and never reads ``.seg.tmp`` files.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from .utils import logger
from .storage_layout import iter_segments, segment_path


class ParquetWriter:
    """Write immutable, atomically-published Parquet segments for one stream."""

    def __init__(
        self, stream_name: str, schema: pa.Schema, base_dir: str = "data", *,
        segment_rows: int = 5_000, segment_seconds: float = 30.0,
        quality_event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        if segment_rows <= 0 or segment_seconds <= 0:
            raise ValueError("segment_rows and segment_seconds must be positive")
        self.stream_name, self.schema, self.base_dir = stream_name, schema, base_dir
        self.stream_dir = Path(base_dir) / "raw" / stream_name
        self.stream_dir.mkdir(parents=True, exist_ok=True)
        self.segment_rows, self.segment_seconds = segment_rows, segment_seconds
        self.quality_event_sink = quality_event_sink
        self.buffer: List[Dict[str, Any]] = []
        self.current_hour = self._get_current_hour_str()
        self.writer: Optional[pq.ParquetWriter] = None
        self._tmp_filepath: Optional[Path] = None
        self._counter_filepath: Optional[Path] = None
        self._segment_opened_monotonic = time.monotonic()
        self.record_count = 0
        self._first_record_ts: Optional[int] = None
        self._last_record_ts: Optional[int] = None
        self._seq = self._next_sequence(self.current_hour)
        self._recover_orphans()
        self._open_segment()

    def _get_current_hour_str(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d-%H")

    def _next_sequence(self, hour: str) -> int:
        prefix = f"{hour}-"
        values = []
        for path in iter_segments(self.base_dir, self.stream_name):
            if path.suffix != ".seg" or not path.name.startswith(prefix):
                continue
            try:
                values.append(int(path.stem.removeprefix(prefix)))
            except ValueError:
                continue
        return (max(values) + 1) if values else 0

    def _segment_paths(self) -> tuple[Path, Path, Path]:
        final = segment_path(self.base_dir, self.stream_name, self.current_hour, self._seq)
        return final, Path(str(final) + ".tmp"), Path(str(final) + ".count.json")

    def _get_filename(self, hour_str: str) -> str:
        """Compatibility helper: return this writer's current segment pathname."""
        return str(segment_path(self.base_dir, self.stream_name, hour_str, self._seq))

    def _emit_drop(self, rows_lost: Optional[int], reason: str = "crashed_segment_discarded") -> None:
        event = {"exchange": "BINANCE", "stream": self.stream_name, "event_type": "DATA_DROP",
                 "reason": reason, "gap_size_ms": None, "rows_lost": rows_lost,
                 "local_ts": int(time.time() * 1000)}
        logger.warning("segment_data_drop", **event)
        if self.quality_event_sink:
            self.quality_event_sink(event)

    def _recover_orphans(self) -> None:
        # Metadata is advisory; an interrupted publication leaves only an orphan temp.
        for meta_tmp in self.stream_dir.glob("*.meta.json.tmp"):
            try:
                meta_tmp.unlink()
            except OSError as exc:
                logger.error("metadata_orphan_discard_failed", file=str(meta_tmp), error=str(exc))
        # Never attempt pq.read_table/ParquetFile on an unclosed segment.
        for tmp in self.stream_dir.glob("*.seg.tmp"):
            count_path = Path(str(tmp).removesuffix(".tmp") + ".count.json")
            rows: Optional[int] = None
            try:
                with count_path.open(encoding="utf-8") as handle:
                    rows = int(json.load(handle).get("rows"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                rows = None
            try:
                tmp.unlink()
                count_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.error("segment_orphan_discard_failed", file=str(tmp), error=str(exc))
                continue
            # A missing counter is itself material uncertainty; a persisted
            # zero counter proves no rows were flushed and emits no drop.
            if rows is None or rows > 0:
                self._emit_drop(rows)

    def _open_segment(self) -> None:
        _, tmp, counter = self._segment_paths()
        self._tmp_filepath, self._counter_filepath = tmp, counter
        self.writer = pq.ParquetWriter(str(tmp), self.schema, compression="snappy")
        self.record_count = 0
        self._first_record_ts = self._last_record_ts = None
        self._segment_opened_monotonic = time.monotonic()

    def _persist_counter(self) -> None:
        assert self._counter_filepath is not None
        temporary = Path(str(self._counter_filepath) + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"rows": self.record_count}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._counter_filepath)

    def write(self, record: Dict[str, Any]) -> None:
        hour = self._get_current_hour_str()
        if hour != self.current_hour:
            self.close()
            self.current_hour, self._seq = hour, self._next_sequence(hour)
            self._open_segment()
        self.buffer.append(record)
        ts = record.get("timestamp")
        if self._first_record_ts is None:
            self._first_record_ts = ts
        self._last_record_ts = ts
        # A bounded conversion buffer reduces tiny row groups.  The sidecar is
        # persisted on every write_table call in flush().
        if (len(self.buffer) >= 1_000 or self.record_count + len(self.buffer) >= self.segment_rows
                or time.monotonic() - self._segment_opened_monotonic >= self.segment_seconds):
            self.flush()
        if self.record_count >= self.segment_rows or time.monotonic() - self._segment_opened_monotonic >= self.segment_seconds:
            self._close_segment(open_next=True)

    def flush(self) -> None:
        if not self.buffer:
            return
        assert self.writer is not None
        columns = {field.name: [record.get(field.name) for record in self.buffer] for field in self.schema}
        self.writer.write_table(pa.Table.from_pydict(columns, schema=self.schema))
        self.record_count += len(self.buffer)
        self.buffer.clear()
        self._persist_counter()

    def _close_segment(self, *, open_next: bool) -> None:
        if self.writer is None:
            return
        self.flush()
        final, tmp, counter = self._segment_paths()
        self.writer.close()
        self.writer = None
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, final)
        parent_fd = os.open(str(final.parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        # Metadata is advisory: recovery relies only on .seg.tmp and its durable
        # count sidecar. Publish metadata atomically so it never affects data durability.
        counter.unlink(missing_ok=True)
        meta = Path(str(final) + ".meta.json")
        meta_tmp = Path(str(meta) + ".tmp")
        with meta_tmp.open("w", encoding="utf-8") as handle:
            json.dump({"record_count": self.record_count, "first_record_ts": self._first_record_ts,
                       "last_record_ts": self._last_record_ts}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(meta_tmp, meta)
        parent_fd = os.open(str(final.parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        logger.info("closed_parquet_segment", stream=self.stream_name, file=str(final), rows=self.record_count)
        if open_next:
            self._seq += 1
            self._open_segment()

    def close(self) -> None:
        # Avoid publishing empty segments, but remove their harmless temporary state.
        if self.writer is None:
            return
        if self.record_count == 0 and not self.buffer:
            self.writer.close()
            self.writer = None
            if self._tmp_filepath:
                self._tmp_filepath.unlink(missing_ok=True)
            if self._counter_filepath:
                self._counter_filepath.unlink(missing_ok=True)
            return
        self._close_segment(open_next=False)
