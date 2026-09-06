from pathlib import Path
import ctypes
import numpy as np
import pytest
from zircon_asic import *


@pytest.mark.oracle
@pytest.mark.parametrize("width", [16, 32])
@pytest.mark.parametrize("op", ["add", "mul", "fma", "div"])
def test_softfloat(width, op):
    paths = list((Path(__file__).resolve().parents[1]/"build/oracles").glob("liboracle.*"))
    if not paths: pytest.skip("run python scripts/build_oracles.py")
    lib = ctypes.CDLL(str(paths[0]))
    ref = lib.zircon_oracle
    ref.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int]
    ref.restype = ctypes.c_uint64
    u, rng = FloatingPointUnit(f"fp{width}", op), np.random.default_rng(11509)
    a, b, c = rng.integers(0, 2**width, (3, 20000), dtype=np.uint32)
    # Random bits plus cross-product of boundary encodings exercise cancellation,
    # tininess after rounding, directed overflow and all NaN operand positions.
    f = u.format
    edge = [0, 1, (1 << f.fraction)-1, 1 << f.fraction, f.max_bits,
            f.inf_bits, f.nan_bits, f.inf_bits+1, f.bias << f.fraction]
    edge += [v | (1 << (width-1)) for v in edge]
    triples = np.array([(x, y, z) for x in edge for y in edge for z in edge], dtype=np.uint32).T
    a, b, c = [np.concatenate((x, y)) for x, y in zip((a, b, c), triples)]
    opid = ["add", "mul", "fma", "div"].index(op)
    for rm in Rounding:
        p = u.compute_batch(a, b, c, rounding=rm, backend="python")
        f = u.compute_batch(a, b, c, rounding=rm, backend="numba")
        expected = np.array([ref(width, opid, int(x), int(y), int(z), rm) for x, y, z in zip(a, b, c)], dtype=np.uint64)
        np.testing.assert_array_equal(p.bits, expected & 0xffffffff, err_msg=f"{width} {op} {rm}")
        np.testing.assert_array_equal(p.flags, expected >> 32)
        np.testing.assert_array_equal(f.bits, p.bits)
        np.testing.assert_array_equal(f.flags, p.flags)
