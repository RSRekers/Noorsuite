import numpy as np
import pytest

from NoorSuite.model import apply_y_transform

Y = np.array([1.0, 10.0, 100.0])


def test_identity():
    np.testing.assert_allclose(apply_y_transform(Y, "1x"), Y)


@pytest.mark.parametrize("token, factor", [
    ("1e3 (kilo)", 1e3),
    ("1e-3 (milli)", 1e-3),
    ("1e-6 (micro)", 1e-6),
    ("1e-9 (nano)", 1e-9),
])
def test_scale_factors(token, factor):
    np.testing.assert_allclose(apply_y_transform(Y, token), Y * factor)


def test_log10():
    np.testing.assert_allclose(apply_y_transform(Y, "Log10"), [0.0, 1.0, 2.0])


def test_log10_clips_nonpositive():
    out = apply_y_transform(np.array([0.0, -5.0, 1.0]), "Log10")
    assert np.isfinite(out).all()
    assert out[2] == 0.0


def test_norm_maps_to_unit_interval():
    out = apply_y_transform(Y, "Norm (0-1)")
    assert out.min() == 0.0
    assert out.max() == 1.0


def test_norm_handles_zero_range():
    out = apply_y_transform(np.array([7.0, 7.0, 7.0]), "Norm")
    np.testing.assert_allclose(out, [0.0, 0.0, 0.0])


def test_milli_not_confused_with_kilo():
    # "1e-3" must not match the "1e3" branch
    np.testing.assert_allclose(apply_y_transform(Y, "1e-3"), Y * 1e-3)
