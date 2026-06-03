import os
import time
from datetime import datetime
from typing import Dict, Any, List
import pyarrow as pa
import pyarrow.parquet as pa_parquet
from collector.utils import logger, send_telegram_alert

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

        self._init_writer()

    def _get_current_hour_str(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d-%H")

    def _get_filename(self, hour_str: str) -> str:
        return os.path.join(self.stream_dir, f"{hour_str}.parquet")

    def _init_writer(self):
        filepath = self._get_filename(self.current_hour)
        try:
            self.writer = pa_parquet.ParquetWriter(
                filepath,
                self.schema,
                compression="snappy"
            )
            logger.info("Initialized parquet writer", stream=self.stream_name, file=filepath)
        except Exception as e:
            msg = f"File Write Failure: Could not initialize parquet writer for {self.stream_name}"
            logger.error(msg, error=str(e))
            send_telegram_alert(msg)
            raise

    def write(self, record: Dict[str, Any]):
        self.buffer.append(record)

        # Check rotation
        now_hour = self._get_current_hour_str()
        if now_hour != self.current_hour:
            self.flush()
            self.close()

            # Re-init for new hour
            self.current_hour = now_hour
            self._init_writer()

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

            table = pa.Table.from_pydict(cols, schema=self.schema)
            self.writer.write_table(table)
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
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                logger.info("Closed parquet file", stream=self.stream_name, file=filepath, size_bytes=file_size)
            except Exception as e:
                logger.error("Failed to close parquet writer", stream=self.stream_name, error=str(e))
