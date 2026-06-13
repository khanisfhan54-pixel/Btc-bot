import os
import sys


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Regime attribution audit capture: additive test instrumentation only.
import json
import pytest

_audit_engines = []
_seen_audit_engine_ids = set()
_regime_engine_init_patched = False


def _register_audit_engine(engine):
    engine_id = id(engine)
    if engine_id not in _seen_audit_engine_ids:
        _seen_audit_engine_ids.add(engine_id)
        _audit_engines.append(engine)


@pytest.fixture(autouse=True)
def _capture_engine_ref(request):
    yield
    for engine in getattr(request.node, "_engines", []):
        _register_audit_engine(engine)


def pytest_configure(config):
    global _regime_engine_init_patched
    if _regime_engine_init_patched:
        return
    import advanced_regime_engine

    original_init = advanced_regime_engine.AdvancedRegimeEngine.__init__

    def _audit_capture_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _register_audit_engine(self)

    advanced_regime_engine.AdvancedRegimeEngine.__init__ = _audit_capture_init
    _regime_engine_init_patched = True


def pytest_sessionfinish(session, exitstatus):
    os.makedirs("reports", exist_ok=True)
    combined_audit = []
    combined_supp = []
    for engine in _audit_engines:
        combined_audit.extend(getattr(engine, "_regime_audit_log", []))
        combined_supp.extend(getattr(engine, "_regime_suppression_log", []))
    with open("reports/regime_audit_log.json", "w", encoding="utf-8") as f:
        json.dump(combined_audit, f)
    with open("reports/regime_suppression_log.json", "w", encoding="utf-8") as f:
        json.dump(combined_supp, f)
    print(f"\nAudit log saved: {len(combined_audit)} records, {len(combined_supp)} suppressions")
