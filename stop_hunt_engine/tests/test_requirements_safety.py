from pathlib import Path


def test_requirements_contains_core_and_shpe_minimums() -> None:
    txt = Path("requirements.txt").read_text()
    for dep in [
        "numpy>=1.26,<2.0.0",
        "scipy>=1.11,<2",
        "prometheus-client>=0.20,<1",
        "websocket-client>=1.8,<2",
        "ccxt>=4.0.0",
        "python-dotenv>=1.0,<2",
        "scikit-learn>=1.4,<2",
        "joblib>=1.3,<2",
    ]:
        assert dep in txt
