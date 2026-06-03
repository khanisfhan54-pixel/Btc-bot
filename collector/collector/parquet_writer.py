import os
import datetime
import pyarrow as pa
import pyarrow.parquet as pq
from typing import Dict, List, Any
from collector import config
from collector.utils import get_logger

logger = get_logger("parquet_writer")

class HourlyParquetWriter:
    def __init__(self, stream_name: str, schema: pa.Schema, output_dir: str):
        self.stream_name = stream_name
        self.schema = schema
        self.output_dir = output_dir
        self.buffer: List[Dict[str, Any]] = []
        self.current_hour: str = ""
        os.makedirs(self.output_dir, exist_ok=True)

    def write_record(self, record: Dict[str, Any]):
        self.buffer.append(record)

        # Check if we need to rotate (based on system time of writes)
        now_hour = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d-%H")
        if not self.current_hour:
            self.current_hour = now_hour
        elif now_hour != self.current_hour:
            self.flush()
            self.current_hour = now_hour

    def flush(self):
        if not self.buffer:
            return

        filename = f"{self.current_hour}.parquet"
        filepath = os.path.join(self.output_dir, filename)

        try:
            # Add metadata
            metadata = {
                "schema_version": config.SCHEMA_VERSION.encode(),
                "stream_name": self.stream_name.encode(),
                "symbol": config.SYMBOL.encode(),
                "interval_start": self.current_hour.encode()
            }

            schema_with_meta = self.schema.with_metadata(metadata)
            table = pa.Table.from_pylist(self.buffer, schema=schema_with_meta)

            # Write with Snappy compression
            pq.write_table(table, filepath, compression='SNAPPY')

            file_size = os.path.getsize(filepath)
            logger.info("file_rotated",
                        stream=self.stream_name,
                        filename=filename,
                        records=len(self.buffer),
                        size_bytes=file_size)

            self.buffer.clear()
        except Exception as e:
            logger.error("file_write_failed", stream=self.stream_name, error=str(e))
            from collector.utils import send_telegram_alert
            send_telegram_alert(f"File write failed for {self.stream_name}:\n{str(e)}")

    def close(self):
        self.flush()

class ParquetManager:
    def __init__(self):
        self.writers = {
            "orderbook": HourlyParquetWriter("orderbook", config.ORDERBOOK_SCHEMA, config.ORDERBOOK_DIR),
            "trades": HourlyParquetWriter("trades", config.TRADES_SCHEMA, config.TRADES_DIR),
            "markprice": HourlyParquetWriter("markprice", config.MARKPRICE_SCHEMA, config.MARKPRICE_DIR)
        }

    def write(self, stream_name: str, record: Dict[str, Any]):
        if stream_name in self.writers:
            self.writers[stream_name].write_record(record)

    def flush_all(self):
        for writer in self.writers.values():
            writer.flush()

    def close_all(self):
        for writer in self.writers.values():
            writer.close()
