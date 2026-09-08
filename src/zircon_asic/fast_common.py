"""Shared compiled float decoding and rounding primitives."""
import numpy as np
from numba import njit


@njit(cache=True)
def _decode(bits, width, eb, fb, bias, encoding):
    sign = np.int64(bits >> np.uint64(width-1))
    frac = np.int64(bits & np.uint64((1 << fb)-1))
    exp = np.int64((bits >> np.uint64(fb)) & np.uint64((1 << eb)-1))
    kind, signaling = 0, 0
    if exp == (1 << eb)-1:
        if encoding == 0:
            kind = 1 if frac == 0 else 2
            signaling = int(frac != 0 and (frac & (1 << (fb-1))) == 0)
        elif encoding == 1 and frac == (1 << fb)-1:
            kind = 2
    sig = frac if exp == 0 else (1 << fb) | frac
    exponent = (1-bias if exp == 0 else exp-bias)-fb
    return sign, sig if kind == 0 else 0, exponent, kind, signaling



@njit(cache=True)
def _round(n, exponent, sign, rm, width, eb, fb, bias, encoding):
    signbit = np.uint64(sign) << np.uint64(width-1)
    if n == 0: return signbit, 0
    t, top = n, 0
    while t >> np.uint64(1):
        t >>= np.uint64(1)
        top += 1
    actual_top = top+exponent
    emin, emax = 1-bias, (1 << eb)-(2 if encoding == 0 else 1)-bias
    maxbits = (1 << (width-1))-1-((1 << fb) if encoding == 0 else (1 if encoding == 1 else 0))
    if encoding != 0:
        maxsig = (1 << (fb+1))-1-(1 if encoding == 1 else 0)
        delta = exponent-(emax-fb)
        overflow = actual_top > emax
        if actual_top == emax:
            if delta >= 0:
                overflow = (n << np.uint64(delta)) > np.uint64(maxsig)
            else:
                overflow = n > (np.uint64(maxsig) << np.uint64(-delta))
        if overflow: return signbit | np.uint64(maxbits), 5
    quantum = max(actual_top, emin)-fb
    tiny_after = actual_top < emin
    if actual_top == emin-1:
        cut0 = top-fb
        q0, rem0, half0 = n, np.uint64(0), np.uint64(1)
        if cut0 > 0:
            q0 = n >> np.uint64(cut0)
            rem0 = n & ((np.uint64(1) << np.uint64(cut0))-np.uint64(1))
            half0 = np.uint64(1) << np.uint64(cut0-1)
        elif cut0 < 0:
            q0 = n << np.uint64(-cut0)
        inc0 = (rm == 0 and (rem0 > half0 or (rem0 == half0 and (q0 & np.uint64(1)) != 0))) or (rm == 4 and rem0 >= half0) or (rm == 2 and sign != 0 and rem0 != 0) or (rm == 3 and sign == 0 and rem0 != 0)
        tiny_after = q0+np.uint64(inc0) < np.uint64(1 << (fb+1))
    shift = quantum-exponent
    rem, half, q = np.uint64(0), np.uint64(0), n
    above, tie = False, False
    if shift > 0:
        if shift < 64:
            q = n >> np.uint64(shift)
            rem = n & ((np.uint64(1) << np.uint64(shift))-np.uint64(1))
            half = np.uint64(1) << np.uint64(shift-1)
            above, tie = rem > half, rem == half
        else:
            q, rem = np.uint64(0), n
            if shift == 64:
                half = np.uint64(1) << np.uint64(63)
                above, tie = n > half, n == half
    elif shift < 0:
        q <<= np.uint64(-shift)
    inc = (rm == 0 and (above or (tie and (q & np.uint64(1)) != 0))) or (rm == 4 and (above or tie)) or (rm == 2 and sign != 0 and rem != 0) or (rm == 3 and sign == 0 and rem != 0)
    q += np.uint64(inc)
    if q >= np.uint64(1 << (fb+1)):
        q >>= np.uint64(1)
        quantum += 1
    if quantum+fb > emax:
        inf = rm == 0 or rm == 4 or (rm == 2 and sign != 0) or (rm == 3 and sign == 0)
        return signbit | np.uint64((((1 << eb)-1) << fb) if inf else maxbits), 5
    tiny = q < np.uint64(1 << fb)
    efield = 0 if tiny else quantum+fb+bias
    flags = (3 if tiny_after else 1) if rem != 0 else 0
    return signbit | (np.uint64(efield) << np.uint64(fb)) | (q & np.uint64((1 << fb)-1)), flags
