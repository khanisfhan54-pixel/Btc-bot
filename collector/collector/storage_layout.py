"""Immutable raw-segment naming and discovery.

Raw collector output has used both legacy hourly ``.parquet`` files and the
crash-bounded ``.seg`` format.  Readers must use this module rather than
constructing filenames themselves so that old data remains readable.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator


SEGMENT_SUFFIXES = (".seg", ".parquet")
_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<hour>\d{2})(?:-(?P<seq>\d{6}))?(?P<suffix>\.seg|\.parquet)$"
)


def segment_path(base_dir: str | Path, stream: str, hour_str: str, seq: int) -> Path:
    """Return the canonical, crash-bounded segment pathname."""
    return Path(base_dir) / "raw" / stream / f"{hour_str}-{seq:06d}.seg"


def parse_segment_name(path: str | Path) -> tuple[str, int, int] | None:
    """Return ``(date, hour, sequence)`` for a published segment.

    Legacy hourly parquet files have sequence zero.  Temporary and sidecar
    files intentionally do not match and are never returned by discovery.
    """
    match = _NAME.match(Path(path).name)
    if not match:
        return None
    return match["date"], int(match["hour"]), int(match["seq"] or 0)


def iter_segments(
    base_dir: str | Path, stream: str, *, date: str | None = None, hour: int | None = None
) -> Iterator[Path]:
    """Yield published legacy and current raw segments in deterministic order."""
    stream_dir = Path(base_dir) / "raw" / stream
    if not stream_dir.exists():
        return
    found: list[tuple[tuple[str, int, int], Path]] = []
    for path in stream_dir.iterdir():
        parsed = parse_segment_name(path)
        if parsed is None:
            continue
        row_date, row_hour, sequence = parsed
        if date is not None and row_date != date:
            continue
        if hour is not None and row_hour != hour:
            continue
        found.append(((row_date, row_hour, sequence), path))
    yield from (path for _, path in sorted(found))
