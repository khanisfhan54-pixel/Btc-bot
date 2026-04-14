# tests/test_alpha.py

import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_file_import():
    try:
        import alpha_liquidity_sweep_predictor
        assert True
    except Exception as e:
        print(e)
        assert False
