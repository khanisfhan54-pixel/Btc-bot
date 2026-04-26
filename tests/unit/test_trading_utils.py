import math
import pytest

from trading_utils import safe_float, clamp, validate_alpha


def test_safe_float_handles_none_nan_inf():
    assert safe_float(None, default=1.2) == 1.2
    assert safe_float(float("nan"), default=2.3) == 2.3
    assert safe_float(float("inf"), default=2.3) == 2.3


def test_clamp_and_validate_alpha():
    assert clamp(5, 0, 1) == 1.0
    with pytest.raises(ValueError):
        clamp(1, 2, 1)
    assert validate_alpha(0.5) == 0.5
    with pytest.raises(ValueError):
        validate_alpha(1.5)
