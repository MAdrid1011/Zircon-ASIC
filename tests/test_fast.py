import numpy as np
import pytest
from zircon_asic import *


@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize("op", ["add", "mul", "fma", "div"])
def test_fast_float(fmt, op):
    pytest.importorskip("numba")
    u = FloatingPointUnit(fmt, op)
    rng = np.random.default_rng(8102)
    a, b, c = rng.integers(0, 2**u.format.width, (3, 5000), dtype=np.uint64)
    rm = rng.integers(0, 5, len(a), dtype=np.uint64)
    p, f = u.compute_batch(a, b, c, rounding=rm, backend="python"), u.compute_batch(a, b, c, rounding=rm, backend="numba")
    np.testing.assert_array_equal(p.bits, f.bits)
    np.testing.assert_array_equal(p.flags, f.flags)


@pytest.mark.parametrize("width", [8, 16, 32])
@pytest.mark.parametrize("op", ["add", "mul", "div"])
@pytest.mark.parametrize("signed", [False, True])
def test_fast_integer(width, op, signed):
    pytest.importorskip("numba")
    u, rng = IntegerUnit(width, op, signed=signed), np.random.default_rng(902)
    a, b = rng.integers(0, 2**width, (2, 5000), dtype=np.uint64)
    p, f = u.compute_batch(a, b, backend="python"), u.compute_batch(a, b, backend="numba")
    for name in ("bits", "flags", "remainder"):
        np.testing.assert_array_equal(getattr(p, name), getattr(f, name))
