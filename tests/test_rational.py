import itertools
import numpy as np
import pytest
from zircon_asic import *
from rational_reference import compute


@pytest.mark.parametrize("op",["add","mul","fma","div"])
def test_fp4_exhaustive(op):
    requests = list(itertools.product(range(16),range(16),range(16) if op == "fma" else [0],range(5)))
    a,b,c,rm = np.array(requests,dtype=np.uint64).T
    expected = np.array([compute("e2m1",op,*map(int,r)) for r in requests],dtype=np.uint32)
    u = FloatingPointUnit("e2m1",op)
    for backend in ("python","numba"):
        result = u.compute_batch(a,b,c,rounding=rm,backend=backend)
        np.testing.assert_array_equal(result.bits,expected[:,0])
        np.testing.assert_array_equal(result.flags,expected[:,1])


@pytest.mark.parametrize("name",["e4m3fn","e5m2"])
@pytest.mark.parametrize("op",["add","mul","fma","div"])
def test_fp8_rational(name,op):
    rng = np.random.default_rng(119)
    a,b,c = rng.integers(0,256,(3,3000),dtype=np.uint64)
    rm = rng.integers(0,5,3000,dtype=np.uint64)
    expected = np.array([compute(name,op,*map(int,r)) for r in zip(a,b,c,rm)],dtype=np.uint32)
    for backend in ("python","numba"):
        result = FloatingPointUnit(name,op).compute_batch(a,b,c,rounding=rm,backend=backend)
        np.testing.assert_array_equal(result.bits,expected[:,0])
        np.testing.assert_array_equal(result.flags,expected[:,1])
