from pathlib import Path


def test_requirements_contains_core_and_shpe_minimums() -> None:
    txt = Path("requirements.txt").read_text()
    for dep in [
        "numpy>=2.4.6,<3.0.0",
        "scipy>=1.17.1,<2.0.0",
        "prometheus-client>=0.25.0,<1.0.0",
        "websocket-client>=1.9.0,<2.0.0",
        "ccxt>=4.5.56,<5.0.0",
        "python-dotenv>=1.2.2,<2.0.0",
        "scikit-learn>=1.9.0,<2.0.0",
        "joblib>=1.5.3,<2.0.0",
    ]:
        assert dep in txt
