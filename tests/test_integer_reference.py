"""Exhaust INT8 and audit wider arithmetic against mathematical identities."""
import itertools
import numpy as np
import pytest
from zircon_asic import IntegerUnit


@pytest.mark.parametrize("width", [8, 16, 32])
@pytest.mark.parametrize("signed", [False, True])
def test_integer_reference(width, signed):
    modulus = 1 << width
    if width == 8:
        pairs = list(itertools.product(range(modulus), repeat=2))
    else:
        edge = [0, 1, 2, modulus // 2 - 1, modulus // 2, modulus // 2 + 1, modulus - 2, modulus - 1]
        pairs = list(itertools.product(edge, repeat=2))
        pairs += list(map(tuple, np.random.default_rng(6421).integers(0, modulus, (12000, 2), dtype=np.uint64)))
    a, b = np.array(pairs, dtype=np.uint64).T
    low, high = (-modulus // 2, modulus // 2 - 1) if signed else (0, modulus - 1)
    for op in ["add", "mul", "div"]:
        expected = []
        for raw_a, raw_b in pairs:
            x, y = int(raw_a), int(raw_b)
            if signed:
                if x >= modulus // 2: x -= modulus
                if y >= modulus // 2: y -= modulus
            if op == "div":
                if not y:
                    expected.append((modulus - 1, 8, int(raw_a)))
                    continue
                # Convert Python's floor quotient to truncation toward zero.
                q, r = divmod(x, y)
                if r and (x < 0) != (y < 0): q += 1
                r = x - q * y
                assert abs(r) < abs(y) and (not r or (r < 0) == (x < 0))
                value = q
            else:
                value = x + y if op == "add" else x * y
                r = 0
            expected.append((value % modulus, 4 if not low <= value <= high else 0, r % modulus))
        expected = np.array(expected, dtype=np.uint64)
        for backend in ["python", "numba"]:
            actual = IntegerUnit(width, op, signed=signed).compute_batch(a, b, backend=backend)
            np.testing.assert_array_equal(actual.bits, expected[:, 0])
            np.testing.assert_array_equal(actual.flags, expected[:, 1])
            np.testing.assert_array_equal(actual.remainder, expected[:, 2])
