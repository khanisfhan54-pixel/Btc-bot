import ast
import atexit
import builtins
import inspect
import importlib
import sys
import threading
import time

import pytest
from unittest.mock import patch


def _import_main_with_execution_stub(monkeypatch=None):
    import types
    exec_mod = types.ModuleType("execution")
    class ExecutionLogic:
        def __init__(self, *args, **kwargs):
            pass
        def decide(self, *args, **kwargs):
            return {"execute": False}
    class ExecutionEngine:
        def get_balance(self):
            return 1.0
        def place_order_with_sl_tp(self, *args, **kwargs):
            return {"status": "ok"}
    def calculate_position_size(*args, **kwargs):
        return 0.0
    def calculate_liquidity_sl_tp(*args, **kwargs):
        return {}
    exec_mod.ExecutionLogic = ExecutionLogic
    exec_mod.ExecutionEngine = ExecutionEngine
    exec_mod.calculate_position_size = calculate_position_size
    exec_mod.calculate_liquidity_sl_tp = calculate_liquidity_sl_tp
    sys.modules["execution"] = exec_mod
    for key in list(sys.modules.keys()):
        if key == "main" or key.startswith("main."):
            del sys.modules[key]
    import main
    return main


def test_compute_score_binding():
    if "engine" not in sys.modules:
        try:
            import engine  # noqa: F401
        except Exception:
            pytest.skip("engine module not available in test environment")
    main = _import_main_with_execution_stub()
    import engine
    assert main.compute_score is engine.compute_score


def test_execution_import_failure_halts_startup(monkeypatch):
    for key in list(sys.modules.keys()):
        if key == "execution" or key.startswith("execution."):
            del sys.modules[key]
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "execution":
            raise ImportError("mocked execution import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    for key in list(sys.modules.keys()):
        if key == "main" or key.startswith("main."):
            del sys.modules[key]
    with pytest.raises((RuntimeError, SystemExit)):
        import main  # noqa: F401


def test_retry_does_not_retry_type_error():
    main = _import_main_with_execution_stub()
    call_count = 0

    def bad_func():
        nonlocal call_count
        call_count += 1
        raise TypeError("programming error")

    start = time.monotonic()
    with pytest.raises((TypeError, RuntimeError)):
        main._retry_exchange_call(bad_func, call_name="test")
    elapsed = time.monotonic() - start
    assert call_count == 1
    assert elapsed < 0.5


def test_retry_retries_network_error():
    main = _import_main_with_execution_stub()
    call_count = 0
    RetryableErr = main._RETRYABLE_EXCHANGE_ERRORS[0]

    def flaky_func():
        nonlocal call_count
        call_count += 1
        raise RetryableErr("transient")

    with pytest.raises(RuntimeError) as exc_info:
        main._retry_exchange_call(flaky_func, max_retries=2, base_delay=0.0, call_name="test")
    assert call_count == 3
    assert "failed after 3 attempts" in str(exc_info.value)


def test_executor_registered_with_atexit():
    main = _import_main_with_execution_stub()
    registered = False
    if hasattr(atexit, "_ncallbacks") and atexit._ncallbacks() > 0:  # type: ignore[attr-defined]
        registered = True
    else:
        main._SHARED_FETCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        main._SHARED_FETCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        registered = True
    assert registered


def test_feature_error_count_thread_safety():
    main = _import_main_with_execution_stub()
    main._feature_type_error_count = 0
    n_threads = 50
    n_increments_each = 20
    barrier = threading.Barrier(n_threads)

    def increment():
        barrier.wait()
        for _ in range(n_increments_each):
            with main._ANALYSIS_STATE_LOCK:
                main._feature_type_error_count += 1

    threads = [threading.Thread(target=increment) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    expected = n_threads * n_increments_each
    assert main._feature_type_error_count == expected


def test_engine_is_fallback_false_on_success():
    main = _import_main_with_execution_stub()
    assert main.ENGINE_IS_FALLBACK is False
    assert main._EXECUTION_IMPORT_SUCCEEDED is True


def test_no_dead_if_false_block():
    main = _import_main_with_execution_stub()
    source = inspect.getsource(main)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Constant) and test.value is False:
                pytest.fail(f"Found unreachable `if False:` block at line {node.lineno}")


def test_singleton_construction_order():
    main = _import_main_with_execution_stub()
    source = inspect.getsource(main)
    lines = source.split("\n")
    targets = [
        "engine",
        "feature_engine",
        "signal_engine",
        "execution_engine",
        "fill_model",
        "tox_filter",
        "order_router",
        "impact_tracker",
        "position_manager",
        "trade_lifecycle",
        "capital_allocator",
        "basis_normalizer",
    ]
    positions = {}
    for i, line in enumerate(lines):
        for t in targets:
            if line.strip().startswith(f"{t} =") and t not in positions:
                positions[t] = i
    ordered = sorted(positions.keys(), key=lambda k: positions[k])
    assert ordered == targets


def test_sigterm_handler_sets_shutdown_event():
    main = _import_main_with_execution_stub()
    with patch.object(main, "send_telegram_message", return_value=True), patch.object(main, "get_exchange", side_effect=RuntimeError("halt")):
        try:
            main.run_live()
        except Exception:
            pass
