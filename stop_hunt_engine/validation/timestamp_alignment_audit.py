from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from stop_hunt_engine.training.io import atomic_write_json, ensure_dir

PREDICTION_TIMESTAMP_FIELDS: Tuple[str, ...] = (
    "prediction_timestamp_ms",
    "prediction_ts_ms",
    "prediction_ts",
    "feature_available_ts_ms",
    "timestamp_ms",
)

EXTERNAL_FEATURE_SOURCES: Mapping[str, Mapping[str, Tuple[str, ...]]] = {
    "funding": {
        "feature_fields": ("funding_rate_8h", "funding_z30d", "funding_oi_sign_divergence"),
        "timestamp_fields": ("funding_timestamp_ms", "funding_ts_ms"),
    },
    "open_interest": {
        "feature_fields": ("delta_oi_velocity", "oi_delta_oi_velocity", "oi_pct_change_1h", "oi_buildup_flag", "oi_price_divergence_sign"),
        "timestamp_fields": ("oi_timestamp_ms", "open_interest_timestamp_ms", "open_interest_ts_ms"),
    },
    "liquidations": {
        "feature_fields": ("nearest_long_cluster_dist_pct", "nearest_short_cluster_dist_pct", "cascade_amplification_flag", "liq_nearest_long_cluster_dist_pct", "liq_nearest_short_cluster_dist_pct", "liq_cascade_amplification_flag"),
        "timestamp_fields": ("liquidation_timestamp_ms", "liq_timestamp_ms", "liquidation_ts_ms"),
    },
    "lob": {
        "feature_fields": ("ofi_zscore", "l1_order_flow_proxy_z", "book_imbalance", "imbalance", "depth_replenishment_ratio", "lob_ofi_zscore", "lob_queue_imbalance", "lob_depth_replenishment_ratio"),
        "timestamp_fields": ("last_book_event_ts_ms", "lob_timestamp_ms", "lob_ts_ms", "book_timestamp_ms"),
    },
    "regime": {
        "feature_fields": ("regime", "regime_label", "regime_confidence", "regime_conviction", "regime_edge_score", "regime_signal_valid", "regime_expected_volatility"),
        "timestamp_fields": ("regime_timestamp_ms", "regime_ts_ms"),
    },
}


def _present(row: Mapping[str, Any], key: str) -> bool:
    return key in row and row.get(key) not in (None, "")


def _parse_ts(value: Any, *, row_idx: int, field: str) -> int:
    try:
        ts = int(float(value))
    except Exception as exc:
        raise ValueError(f"timestamp alignment audit invalid timestamp: row={row_idx} field={field} value={value!r}") from exc
    if ts < 0:
        raise ValueError(f"timestamp alignment audit invalid timestamp: row={row_idx} field={field} value={ts}")
    return ts


def _prediction_ts(row: Mapping[str, Any], *, row_idx: int) -> int:
    for field in PREDICTION_TIMESTAMP_FIELDS:
        if _present(row, field):
            return _parse_ts(row[field], row_idx=row_idx, field=field)
    raise ValueError(f"timestamp alignment audit missing prediction timestamp: row={row_idx}")


def _source_timestamps_from_row(row: Mapping[str, Any], feature: str, config: Mapping[str, Tuple[str, ...]], row_idx: int) -> List[int]:
    explicit = row.get("external_feature_timestamps")
    if isinstance(explicit, Mapping) and feature in explicit and explicit.get(feature) not in (None, ""):
        return [_parse_ts(explicit[feature], row_idx=row_idx, field=f"external_feature_timestamps.{feature}")]

    raw_sample = row.get("raw_sample")
    if isinstance(raw_sample, Mapping):
        nested = _source_timestamps_from_row(raw_sample, feature, config, row_idx)
        if nested:
            return nested

    timestamps: List[int] = []
    for field in config["timestamp_fields"]:
        if _present(row, field):
            timestamps.append(_parse_ts(row[field], row_idx=row_idx, field=field))
    return timestamps


def _source_is_present(row: Mapping[str, Any], feature: str, config: Mapping[str, Tuple[str, ...]]) -> bool:
    explicit = row.get("external_feature_timestamps")
    if isinstance(explicit, Mapping) and feature in explicit and explicit.get(feature) not in (None, ""):
        return True
    if any(_present(row, field) for field in config["feature_fields"]):
        return True
    raw_sample = row.get("raw_sample")
    if isinstance(raw_sample, Mapping):
        return _source_is_present(raw_sample, feature, config)
    return False


def normalize_training_rows(payload_or_rows: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload_or_rows, Mapping):
        rows = payload_or_rows.get("samples", payload_or_rows.get("rows"))
        if isinstance(rows, list):
            return rows
        return [payload_or_rows]
    return list(payload_or_rows)


def run_timestamp_alignment_audit(payload_or_rows: Any, out_dir: Optional[str] = None, *, fail_on_violation: bool = True) -> Dict[str, Any]:
    rows = normalize_training_rows(payload_or_rows)
    violations: List[Dict[str, int | str]] = []
    rows_audited = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"timestamp alignment audit row is not a mapping: row={idx}")
        prediction_ts = _prediction_ts(row, row_idx=idx)
        row_has_source = False
        try:
            row_id = int(float(row.get("row_index", idx)))
        except Exception:
            row_id = idx
        for feature, config in EXTERNAL_FEATURE_SOURCES.items():
            if not _source_is_present(row, feature, config):
                continue
            row_has_source = True
            source_timestamps = _source_timestamps_from_row(row, feature, config, idx)
            if not source_timestamps:
                raise ValueError(
                    "timestamp alignment audit missing external feature timestamp: "
                    f"row={row_id} feature={feature}"
                )
            for feature_ts in source_timestamps:
                if feature_ts > prediction_ts:
                    violations.append({
                        "feature": feature,
                        "row": row_id,
                        "prediction_ts": prediction_ts,
                        "feature_ts": feature_ts,
                        "leak_ms": feature_ts - prediction_ts,
                    })
        if row_has_source:
            rows_audited += 1

    leaks = [int(v["leak_ms"]) for v in violations]
    summary = {
        "total_rows": len(rows),
        "rows_audited": rows_audited,
        "violations": len(violations),
        "violation_rate": (len(violations) / rows_audited) if rows_audited else 0.0,
        "max_leak_ms": max(leaks) if leaks else 0,
        "average_leak_ms": (sum(leaks) / len(leaks)) if leaks else 0.0,
    }
    report = {
        "status": "FAIL" if violations else "PASS",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "violations": violations,
    }
    if out_dir is not None:
        write_timestamp_alignment_reports(report, out_dir)
    if violations and fail_on_violation:
        raise ValueError("Timestamp leakage detected")
    return report


def write_timestamp_alignment_reports(report: Mapping[str, Any], out_dir: str) -> Dict[str, str]:
    base = ensure_dir(out_dir)
    json_path = os.path.join(base, "timestamp_alignment_audit.json")
    md_path = os.path.join(base, "timestamp_alignment_summary.md")
    atomic_write_json(dict(report), json_path)
    violations = list(report.get("violations", []))
    summary = dict(report.get("summary", {}))
    features = sorted({str(v.get("feature")) for v in violations})
    rows = [str(v.get("row")) for v in violations]
    leak_values = [int(v.get("leak_ms", 0)) for v in violations]
    lines = [
        "# Timestamp Alignment Audit",
        "",
        f"## A. PASS / FAIL",
        f"{report.get('status', 'FAIL')}",
        "",
        "## B. Violating features",
        ", ".join(features) if features else "None",
        "",
        "## C. Leak magnitude",
        f"- Max leak (ms): {summary.get('max_leak_ms', 0)}",
        f"- Average leak (ms): {summary.get('average_leak_ms', 0.0)}",
        "",
        "## D. Exact rows affected",
        ", ".join(rows) if rows else "None",
        "",
        "## E. Recommended fixes",
        "- Enforce an as-of join keyed by prediction timestamp for every external feature source.",
        "- Reject or delay rows whose funding, open interest, liquidation, LOB, or regime timestamp is newer than the prediction timestamp.",
        "- Preserve source event timestamps in training artifacts so leakage checks remain auditable.",
        "",
        "## Summary metrics",
        f"- Total Rows: {summary.get('total_rows', 0)}",
        f"- Rows Audited: {summary.get('rows_audited', 0)}",
        f"- Violations: {summary.get('violations', 0)}",
        f"- Violation Rate: {summary.get('violation_rate', 0.0)}",
        f"- Max Leak (ms): {summary.get('max_leak_ms', 0)}",
        f"- Average Leak (ms): {summary.get('average_leak_ms', 0.0)}",
    ]
    if violations:
        lines.extend(["", "## Violations"])
        for v in violations:
            lines.append(f"- feature={v.get('feature')} row={v.get('row')} prediction_ts={v.get('prediction_ts')} feature_ts={v.get('feature_ts')} leak_ms={v.get('leak_ms')}")
    tmp = md_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, md_path)
    return {"json": json_path, "markdown": md_path}


def assert_no_timestamp_leakage(payload_or_rows: Any) -> None:
    run_timestamp_alignment_audit(payload_or_rows, fail_on_violation=True)
