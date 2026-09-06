"""Optional Numba integer kernels. No floating point or fastmath is used.

Twenty radix-2**32 limbs cover the full binary32 fused-add exponent span.
Scratch buffers are reused across a batch; only the final rounding window is
reduced to uint64, with an exact sticky bit for all discarded positions.
"""
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


@njit(cache=True)
def _float_one(a, b, c, rm, op, width, eb, fb, bias, encoding, x, y, z):
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
def _batch(a, b, c, rm, op, width, eb, fb, bias, encoding, signed):
    n = len(a)
    out, flags, rem = np.empty(n, np.uint32), np.empty(n, np.uint8), np.zeros(n, np.uint32)
    x, y, z = np.zeros(20, np.uint64), np.zeros(20, np.uint64), np.zeros(20, np.uint64)
    for i in range(n):
        if eb:
            out[i], flags[i] = _float_one(a[i], b[i], c[i], np.int64(rm[i]), op, width, eb, fb, bias, encoding, x, y, z)
        else:
            out[i], flags[i], rem[i] = _int_one(a[i], b[i], op, width, signed)
    return out, flags, rem


def batch_kernel(unit, a, b, c, rm):
    op = {"add": 0, "mul": 1, "fma": 2, "div": 3}[unit.op]
    if hasattr(unit, "format"):
        f = unit.format
        return _batch(a, b, c, rm, op, f.width, f.exponent, f.fraction, f.bias,
                      {"ieee": 0, "finite_nan": 1, "finite": 2}[f.encoding], False)
    return _batch(a, b, c, rm, op, unit.width, 0, 0, 0, 0, unit.signed)
