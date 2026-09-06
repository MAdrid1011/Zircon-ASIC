import itertools
import struct
import numpy as np
import pytest
from zircon_asic import *


def test_ieee_edges():
    u = FP32Add()
    assert u.compute(Request(0x3f800000, 0x33800000)).bits == 0x3f800000
    assert u.compute(Request(0x3f800001, 0x33800000)).bits == 0x3f800002
    assert u.compute(Request(1, 1)).bits == 2
    assert u.compute(Request(0x80000000, 0x80000000)).bits == 0x80000000
    assert u.compute(Request(0x3f800000, 0xbf800000, rounding=Rounding.RDN)).bits == 0x80000000
    assert u.compute(Request(0x7f800000, 0xff800000)).flags == Flags.NV
    assert u.compute(Request(0x7fa00001, 0)).bits == 0x7fc00000
    assert FP32Mul().compute(Request(0, 0x7f800000)).flags == Flags.NV
    assert FP32Div().compute(Request(0x3f800000, 0)).flags == Flags.DZ
    assert FP32Div().compute(Request(1, 0x40000000)).flags == Flags.UF | Flags.NX
    assert FP32Mul().compute(Request(0x7f7fffff, 0x40000000, rounding=Rounding.RTZ)).bits == 0x7f7fffff


def test_fma_single_rounding():
    # (1+2^-23)*(1-2^-23)-1 = -2^-46, not zero.
    assert FP32Fma().compute(Request(0x3f800001, 0x3f7ffffe, 0xbf800000)).bits == 0xa8800000


def test_finite_formats():
    assert FP4Div().compute(Request(1, 0)) == Response(7, Flags.DZ)
    assert FP4Div().compute(Request(0, 0)) == Response(0, Flags.NV)
    assert FP4Mul().compute(Request(7, 7)) == Response(7, Flags.OF | Flags.NX)
    assert FP8E4M3FNAdd().compute(Request(0x7e, 0x01)) == Response(0x7e, Flags.OF | Flags.NX)
    assert FP8E4M3FNAdd().compute(Request(0x7f, 0)) == Response(0x7f)


@pytest.mark.parametrize("width", [8, 16, 32])
@pytest.mark.parametrize("signed", [False, True])
def test_integer(width, signed):
    mask = (1 << width)-1
    assert IntDiv(width, signed=signed).compute(Request(7, 0)) == Response(mask, Flags.DZ, remainder=7)
    if signed:
        assert IntDiv(width).compute(Request(mask-6, 3)) == Response(mask-1, remainder=mask)
        assert IntDiv(width).compute(Request(1 << (width-1), mask)) == Response(1 << (width-1), Flags.OF)
    assert IntMul(width, signed=signed).compute(Request(mask, mask)).bits == 1


def test_batch_broadcast():
    u = INT8Add()
    r = u.compute_batch(np.array([[1], [127]]), [0, 1], backend="python")
    assert r.bits.tolist() == [[1, 2], [127, 128]]
    assert r.flags.tolist() == [[0, 0], [0, 4]]
    assert u.compute_batch([], [], backend="python").bits.size == 0


def test_numpy_binary32_rne():
    rng = np.random.default_rng(410)
    a, b = rng.integers(0, 2**32, (2, 12000), dtype=np.uint32)
    with np.errstate(all="ignore"):
        for op, fn in [("add", np.add), ("mul", np.multiply), ("div", np.divide)]:
            got = FloatingPointUnit("fp32", op).compute_batch(a, b, backend="python").bits
            ref = fn(a.view(np.float32), b.view(np.float32)).view(np.uint32)
            ref[(ref & 0x7fffffff) > 0x7f800000] = 0x7fc00000
            np.testing.assert_array_equal(got, ref)
