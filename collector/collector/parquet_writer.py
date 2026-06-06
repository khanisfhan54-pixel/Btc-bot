import os
from datetime import datetime, timedelta
import datetime as _datetime_module
from typing import Dict, Any, List
import pyarrow as pa
import pyarrow.parquet as pa_parquet
from .utils import logger, send_telegram_alert

class ParquetWriter:
    def __init__(self, stream_name: str, schema: pa.Schema, base_dir: str = "data"):
        self.stream_name = stream_name
        self.schema = schema
        self.base_dir = base_dir
        self.stream_dir = os.path.join(base_dir, "raw", stream_name)
        os.makedirs(self.stream_dir, exist_ok=True)

        self.buffer: List[Dict[str, Any]] = []
        self.current_hour = self._get_current_hour_str()
        self.writer: pa_parquet.ParquetWriter = None
        self.active_schema = self.schema
        self.record_count = 0

        self._init_writer()

    def _get_current_hour_str(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d-%H")

    def _get_filename(self, hour_str: str) -> str:
        return os.path.join(self.stream_dir, f"{hour_str}.parquet")

    def _init_writer(self):
        filepath = self._get_filename(self.current_hour)

        # BUG 2 FIX: On same-hour restart, load existing rows back into buffer
        # so they are rewritten when flush() is called, preventing data loss.
        if os.path.exists(filepath):
            try:
                existing = pa_parquet.read_table(filepath)
                existing_dict = existing.to_pydict()
                n = existing.num_rows
                pre_records = [
                    {col: existing_dict[col][i] for col in existing_dict}
                    for i in range(n)
                ]
                self.buffer = pre_records + self.buffer
                logger.warning(
                    "Same-hour restart: reloaded existing records into buffer",
                    stream=self.stream_name,
                    file=filepath,
                    reloaded_count=n,
                )
            except Exception as e:
                logger.error(
                    "Could not reload existing parquet file; data may be incomplete",
                    stream=self.stream_name,
                    file=filepath,
                    error=str(e),
                )

        try:
            interval_start_dt = _datetime_module.datetime.strptime(self.current_hour, "%Y-%m-%d-%H")
            interval_end_dt = interval_start_dt + timedelta(hours=1)
            existing_metadata = dict(self.schema.metadata or {})
            interval_metadata = {
                b"interval_start": (interval_start_dt.isoformat() + "Z").encode(),
                b"interval_end": (interval_end_dt.isoformat() + "Z").encode(),
            }
            self.active_schema = self.schema.with_metadata(
                {**existing_metadata, **interval_metadata}
            )
            self.writer = pa_parquet.ParquetWriter(
                filepath,
                self.active_schema,
                compression="snappy",
            )
            logger.info(
                "Initialized parquet writer", stream=self.stream_name, file=filepath
            )
        except Exception as e:
            msg = f"File Write Failure: Could not initialize parquet writer for {self.stream_name}"
            logger.error(msg, error=str(e))
            send_telegram_alert(msg)
            raise

    def write(self, record: Dict[str, Any]):
        # Check rotation
        now_hour = self._get_current_hour_str()
        if now_hour != self.current_hour:
            self.flush()
            self.close()

            # Re-init for new hour
            self.current_hour = now_hour
            self._init_writer()

        self.buffer.append(record)

        # Flush buffer if it gets too large
        if len(self.buffer) >= 1000:
            self.flush()

    def flush(self):
        if not self.buffer:
            return

        try:
            # Convert buffer to list of dicts grouped by column
            cols = {field.name: [] for field in self.schema}
            for rec in self.buffer:
                for k in cols.keys():
                    cols[k].append(rec.get(k))

            table = pa.Table.from_pydict(cols, schema=self.active_schema)
            self.writer.write_table(table)
            self.record_count += len(self.buffer)
            self.buffer.clear()
        except Exception as e:
            msg = f"File Write Failure: Could not flush to parquet for {self.stream_name}"
            logger.error(msg, error=str(e))
            send_telegram_alert(msg)

    def close(self):
        self.flush()
        if self.writer:
            try:
                filepath = self._get_filename(self.current_hour)
                self.writer.close()
                self.writer = None  # BUG 2 FIX: prevent write to closed writer
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                logger.info(
                    "Closed parquet file",
                    stream=self.stream_name,
                    file=filepath,
                    size_bytes=file_size,
                )
                self.record_count = 0
            except Exception as e:
                logger.error("Failed to close parquet writer", stream=self.stream_name, error=str(e))
