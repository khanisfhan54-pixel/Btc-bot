import os
import pytest
import importlib

def test_boot_validation(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING","true")
    monkeypatch.setenv("BINANCE_API_KEY","")
    monkeypatch.setenv("BINANCE_SECRET","")
    import main
    with pytest.raises(RuntimeError):
        main.run_live()
