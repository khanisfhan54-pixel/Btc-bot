import main


def test_orderbook_imbalance_uses_argument_not_global_name():
    main.analysis_orderbook = {"bids": [[99999.0, 0.0]], "asks": [[100000.0, 1000.0]]}
    ob = {"bids": [[100.0, 4.0]], "asks": [[101.0, 1.0]]}
    val = main.orderbook_imbalance(ob)
    assert val > 0.0


def test_orderbook_imbalance_malformed_input_falls_back_to_zero():
    assert main.orderbook_imbalance({"bids": "bad", "asks": []}) == 0.0
    assert main.orderbook_imbalance({"bids": [[100.0, "nan"]], "asks": [[101.0, 1.0]]}) == -1.0
