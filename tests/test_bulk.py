import pytest

from NoorSuite.model import apply_numeric_expr


def test_blank_keeps():
    assert apply_numeric_expr(1.8, "") is None
    assert apply_numeric_expr(1.8, "   ") is None


def test_plain_number_sets():
    assert apply_numeric_expr(1.8, "3") == 3.0
    assert apply_numeric_expr(1.8, "0.5") == 0.5


@pytest.mark.parametrize("cur, expr, out", [
    (1.8, "*2", 3.6),
    (2.0, "/4", 0.5),
    (1.0, "+1", 2.0),
    (2.0, "-0.5", 1.5),
    (1.5, "* 2", 3.0),      # tolerates a space
])
def test_expressions_transform(cur, expr, out):
    assert apply_numeric_expr(cur, expr) == pytest.approx(out)


def test_divide_by_zero_keeps_current():
    assert apply_numeric_expr(2.0, "/0") == 2.0


def test_garbage_returns_none():
    assert apply_numeric_expr(1.0, "abc") is None
    assert apply_numeric_expr(1.0, "*x") is None
    assert apply_numeric_expr(1.0, "**2") is None
