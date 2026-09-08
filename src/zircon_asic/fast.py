"""Optional Numba integer kernels. No floating point or fastmath is used.

Twenty radix-2**32 limbs cover the full binary32 fused-add exponent span.
Scratch buffers are reused across a batch; only the final rounding window is
reduced to uint64, with an exact sticky bit for all discarded positions.
"""
import numpy as np
from numba import njit
from .fast_common import _decode, _round




@njit(cache=True)
def _put(buf, n, shift):
    for i in range(20): buf[i] = np.uint64(0)
    word, offset = shift//32, shift%32
    while n:
        digit = n & np.uint64(0xffffffff)
        if word < 20: buf[word] |= (digit << np.uint64(offset)) & np.uint64(0xffffffff)
        if offset and word+1 < 20: buf[word+1] |= digit >> np.uint64(32-offset)
        n >>= np.uint64(32)
        word += 1


@njit(cache=True)
def _sum(a, ea, sa, b, eb, sb, base, x, y, z):
    _put(x, np.uint64(a), ea-base)
    _put(y, np.uint64(b), eb-base)
    sign, carry = sa, np.uint64(0)
    if sa == sb:
        for i in range(20):
            v = x[i]+y[i]+carry
            z[i] = v & np.uint64(0xffffffff)
            carry = v >> np.uint64(32)
    else:
        order = 0
        for i in range(19, -1, -1):
            if x[i] != y[i]:
                order = 1 if x[i] > y[i] else -1
                break
        if order < 0:
            sign = sb
        for i in range(20):
            hi = x[i] if order >= 0 else y[i]
            lo = (y[i] if order >= 0 else x[i]) + carry
            carry = np.uint64(hi < lo)
            z[i] = (hi-lo) & np.uint64(0xffffffff)
    top = -1
    for i in range(19, -1, -1):
        if z[i]:
            v, bit = z[i], 0
            while v >> np.uint64(1):
                v >>= np.uint64(1)
                bit += 1
            top = i*32+bit
            break
    shift = max(0, top-30)
    n, sticky = np.uint64(0), False
    for i in range(20):
        p = i*32-shift
        if p >= 0:
            if p < 64: n |= z[i] << np.uint64(p)
        elif p > -32:
            n |= z[i] >> np.uint64(-p)
            sticky |= (z[i] & ((np.uint64(1) << np.uint64(-p))-np.uint64(1))) != 0
        else:
            sticky |= z[i] != 0
    return n | np.uint64(sticky), base+shift, sign




@njit(cache=True)
def _float_one(a, b, c, rm, op, width, eb, fb, bias, encoding, x, y, z, sfu):
    if op >= 4:
        return unary_one(a, rm, op, width, eb, fb, bias, sfu)
    sa, ma, ea, ka, na = _decode(a, width, eb, fb, bias, encoding)
    sb, mb, ebx, kb, nb = _decode(b, width, eb, fb, bias, encoding)
    sc, mc, ec, kc, nc = _decode(c, width, eb, fb, bias, encoding)
    if op != 2: sc, mc, ec, kc, nc = 0, 0, 0, 0, 0
    sign = sa ^ sb
    az, bz = ka == 0 and ma == 0, kb == 0 and mb == 0
    invalid = na or nb or nc
    if op == 1 or op == 2: invalid |= (ka == 1 and bz) or (kb == 1 and az)
    nanbits = (((1 << eb)-1) << fb) | (1 << (fb-1)) if encoding == 0 else ((1 << (width-1))-1 if encoding == 1 else 0)
    if ka == 2 or kb == 2 or kc == 2: return np.uint64(nanbits), 16 if invalid else 0
    if op == 0: invalid |= ka == 1 and kb == 1 and sa != sb
    if op == 2: invalid |= (ka == 1 or kb == 1) and kc == 1 and sign != sc
    if op == 3: invalid |= (az and bz) or (ka == 1 and kb == 1)
    if invalid: return np.uint64(nanbits), 16
    infbits = ((1 << eb)-1) << fb
    if op == 0 and (ka == 1 or kb == 1):
        return np.uint64(infbits | ((sa if ka == 1 else sb) << (width-1))), 0
    if (op == 1 or op == 2) and (ka == 1 or kb == 1):
        return np.uint64(infbits | (sign << (width-1))), 0
    if op == 2 and kc == 1: return np.uint64(infbits | (sc << (width-1))), 0
    if op == 3:
        if ka == 1: return np.uint64(infbits | (sign << (width-1))), 0
        if kb == 1: return np.uint64(sign << (width-1)), 0
        if bz:
            maxbits = (1 << (width-1))-1-(1 if encoding == 1 else 0)
            return np.uint64((infbits if encoding == 0 else maxbits) | (sign << (width-1))), 8
    base = 2 * (1-bias-fb)
    if op == 0:
        n, e, sign = _sum(ma, ea, sa, mb, ebx, sb, base, x, y, z)
        if n == 0: sign = sa if az and bz and sa == sb else int(rm == 2)
    elif op == 1 or op == 2:
        n, e = np.uint64(ma)*np.uint64(mb), ea+ebx
        if op == 2:
            pzero, psign = n == 0, sign
            n, e, sign = _sum(n, e, sign, mc, ec, sc, base, x, y, z)
            if n == 0: sign = psign if pzero and mc == 0 and psign == sc else int(rm == 2)
    else:
        if ma:
            while ma < (1 << fb):
                ma <<= 1
                ea -= 1
        while mb < (1 << fb):
            mb <<= 1
            ebx -= 1
        numerator = np.uint64(ma) << np.uint64(fb+5)
        n = numerator // np.uint64(mb)
        if numerator % np.uint64(mb): n |= np.uint64(1)
        e = ea-ebx-(fb+5)
    return _round(n, e, sign, rm, width, eb, fb, bias, encoding)


@njit(cache=True)
def _int_one(aa, bb, op, width, signed):
    mask = (np.uint64(1) << np.uint64(width))-np.uint64(1)
    a, b = np.int64(aa), np.int64(bb)
    if signed:
        if aa & (np.uint64(1) << np.uint64(width-1)): a -= np.int64(1 << width)
        if bb & (np.uint64(1) << np.uint64(width-1)): b -= np.int64(1 << width)
    if op == 3:
        if b == 0: return mask, 8, aa
        q = abs(a)//abs(b)
        if (a < 0) != (b < 0): q = -q
        r = a-q*b
        overflow = signed and a == -(1 << (width-1)) and b == -1
        return np.uint64(q) & mask, 4 if overflow else 0, np.uint64(r) & mask
    if signed:
        v = a+b if op == 0 else a*b
        overflow = v < -(1 << (width-1)) or v >= (1 << (width-1))
        return np.uint64(v) & mask, 4 if overflow else 0, np.uint64(0)
    v = aa+bb if op == 0 else aa*bb
    return v & mask, 4 if v > mask else 0, np.uint64(0)


@njit(cache=True)
def _batch(a, b, c, rm, op, width, eb, fb, bias, encoding, signed, sfu):
    n = len(a)
    out, flags, rem = np.empty(n, np.uint32), np.empty(n, np.uint8), np.zeros(n, np.uint32)
    x, y, z = np.zeros(20, np.uint64), np.zeros(20, np.uint64), np.zeros(20, np.uint64)
    for i in range(n):
        if eb:
            out[i], flags[i] = _float_one(a[i], b[i], c[i], np.int64(rm[i]), op, width, eb, fb, bias, encoding, x, y, z, sfu)
        else:
            out[i], flags[i], rem[i] = _int_one(a[i], b[i], op, width, signed)
    return out, flags, rem


def batch_kernel(unit, a, b, c, rm):
    op = OPERATIONS[unit.op]
    if hasattr(unit, "format"):
        f = unit.format
        return _batch(a, b, c, rm, op, f.width, f.exponent, f.fraction, f.bias,
                      {"ieee": 0, "finite_nan": 1, "finite": 2}[f.encoding], False, kernel_resources((unit,)))
    return _batch(a, b, c, rm, op, unit.width, 0, 0, 0, 0, unit.signed, kernel_resources((unit,)))


from .unary import OPERATIONS
from .fast_unary import unary_one, kernel_resources
