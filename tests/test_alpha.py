# tests/test_alpha.py

def test_file_import():
    try:
        import alpha_liquidity_sweep_predictor
        assert True
    except Exception as e:
        print(e)
        assert False
