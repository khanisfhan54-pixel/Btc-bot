"""
_metrics.py
===========
AUDIT-FIX Phase 4 #14 — Pluggable metrics shim for engine.py.

Provides a tiny `MetricsBackend` Protocol with two built-in implementations:

  * NoOpMetrics       — default, zero-cost, unit-test safe.
  * PrometheusMetrics — wraps `prometheus_client` IFF the dependency is
                        actually importable. Falls back to NoOp otherwise.

engine.py may call:

    from _metrics import metrics, ALERT_FALLBACK_RATE_THRESHOLD
    metrics.gauge("engine_fallback_rate", rate, {"symbol": "BTCUSDT"})
    metrics.counter("engine_fallback_total", {"reason": "ValueError.x"})
    metrics.histogram("engine_bar_latency_ms", dt_ms, {"engine": "smc"})

Alert thresholds are exposed as module constants so operators / dashboards
can read the same numbers the engine code uses.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from typing import Protocol
except ImportError:  # py<3.8 fallback
    Protocol = object  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Alert thresholds (read by engine, dashboards, alertmanager rules).
# ---------------------------------------------------------------------------
ALERT_FALLBACK_RATE_THRESHOLD: float = 0.05
ALERT_ALLOW_TRADE_ZERO_MINUTES: int = 30
ALERT_ALPHA_DISAGREEMENT_THRESH: float = 0.50


# ---------------------------------------------------------------------------
# Backend protocol + default no-op.
# ---------------------------------------------------------------------------
class MetricsBackend(Protocol):
    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None: ...
    def counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> None: ...
    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None: ...


class NoOpMetrics:
    """Default backend. Safe for unit tests and production environments
    that do not have prometheus_client installed.
    """

    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        return None

    def counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> None:
        return None

    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        return None


class PrometheusMetrics:
    """Thin wrapper over prometheus_client.

    Lazily creates Gauge/Counter/Histogram objects keyed by metric name.
    All methods are best-effort: any internal failure is swallowed so
    observability never breaks the trading path.
    """

    def __init__(self) -> None:
        try:
            import prometheus_client  # type: ignore
        except Exception as exc:  # pragma: no cover — optional dep
            raise RuntimeError(f"prometheus_client unavailable: {exc}")
        self._pc = prometheus_client
        self._gauges: Dict[str, Any] = {}
        self._counters: Dict[str, Any] = {}
        self._histograms: Dict[str, Any] = {}

    @staticmethod
    def _label_keys(labels: Optional[Dict[str, str]]) -> tuple:
        return tuple(sorted((labels or {}).keys()))

    @staticmethod
    def _label_values(labels: Optional[Dict[str, str]]) -> Dict[str, str]:
        return {k: str(v) for k, v in (labels or {}).items()}

    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        try:
            keys = self._label_keys(labels)
            g = self._gauges.get((name, keys))
            if g is None:
                g = self._pc.Gauge(name, name, labelnames=keys)
                self._gauges[(name, keys)] = g
            (g.labels(**self._label_values(labels)) if keys else g).set(float(value))
        except Exception:
            return None

    def counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> None:
        try:
            keys = self._label_keys(labels)
            c = self._counters.get((name, keys))
            if c is None:
                c = self._pc.Counter(name, name, labelnames=keys)
                self._counters[(name, keys)] = c
            (c.labels(**self._label_values(labels)) if keys else c).inc()
        except Exception:
            return None

    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        try:
            keys = self._label_keys(labels)
            h = self._histograms.get((name, keys))
            if h is None:
                h = self._pc.Histogram(name, name, labelnames=keys)
                self._histograms[(name, keys)] = h
            (h.labels(**self._label_values(labels)) if keys else h).observe(float(value))
        except Exception:
            return None


def _build_default_backend() -> MetricsBackend:
    """Return the default metrics backend.

    Picks PrometheusMetrics only if `prometheus_client` is importable AND
    the operator has explicitly opted in via the `ENGINE_METRICS=prometheus`
    environment variable. Otherwise returns NoOpMetrics so engine.py stays
    a zero-dependency module by default.
    """
    import os
    try:
        if os.environ.get("ENGINE_METRICS", "").lower() == "prometheus":
            try:
                return PrometheusMetrics()
            except Exception:
                return NoOpMetrics()
    except Exception:
        pass
    return NoOpMetrics()


metrics: MetricsBackend = _build_default_backend()
