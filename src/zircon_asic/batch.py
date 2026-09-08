"""Broadcasting, contiguous array API, with a complete pure Python fallback."""
import numpy as np
from .types import BatchResponse, Request, Rounding


def _arrays(unit, a, b, c, rounding, tags):
    raw = np.broadcast_arrays(a, b, c, rounding, tags)
    width = unit.format.width if hasattr(unit, "format") else unit.width
    for x in raw[:3]:
        if x.size and (x.dtype.kind not in "biu" or np.any(x < 0) or np.any(x >= (1 << width))):
            raise ValueError(f"raw operands must be integers in [0, 2**{width})")
    if raw[3].dtype.kind not in "biu" or np.any(raw[3] < 0) or np.any(raw[3] > 4):
        raise ValueError("rounding codes must be integers from 0 to 4")
    if unit.op == "exp" and np.any(raw[3] != 0):
        raise ValueError("exp supports RNE only")
    if raw[4].dtype.kind not in "biu" or np.any(raw[4] < 0) or np.any(raw[4] >= (1 << 32)):
        raise ValueError("tags must be unsigned 32-bit integers")
    return raw[0].shape, [np.array(x, dtype=np.uint64, order="C", copy=True).reshape(-1) for x in raw]


def compute_batch(unit, a, b=0, c=0, *, rounding=Rounding.RNE, tags=0, backend="auto"):
    if backend not in ("auto", "python", "numba"):
        raise ValueError("backend must be auto, python or numba")
    shape, arrays = _arrays(unit, a, b, c, rounding, tags)
    if backend != "python":
        try:
            from .fast import batch_kernel
        except ImportError:
            if backend == "numba":
                raise ImportError("Numba backend requires the optional fast dependencies") from None
        else:
            bits, flags, remainder = batch_kernel(unit, *arrays[:4])
            return BatchResponse(bits.reshape(shape), flags.reshape(shape), arrays[4].astype(np.uint32).reshape(shape), remainder.reshape(shape))
    n = arrays[0].size
    bits, flags, remainder = np.empty(n, np.uint32), np.empty(n, np.uint8), np.empty(n, np.uint32)
    for i in range(n):
        result = unit.compute(Request(*(int(x[i]) for x in arrays)))
        bits[i], flags[i], remainder[i] = result.bits, result.flags, result.remainder
    return BatchResponse(bits.reshape(shape), flags.reshape(shape), arrays[4].astype(np.uint32).reshape(shape), remainder.reshape(shape))
