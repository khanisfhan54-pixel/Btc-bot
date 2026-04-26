import json
import threading

import main


def test_log_trade_atomicity(tmp_path, monkeypatch):
    log_path = tmp_path / "trades.json"
    monkeypatch.setattr(main, "TRADE_LOG_PATH", str(log_path))

    def worker(i: int):
        main.log_trade({"id": i})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    payload = json.loads(log_path.read_text())
    assert len(payload) == 50
