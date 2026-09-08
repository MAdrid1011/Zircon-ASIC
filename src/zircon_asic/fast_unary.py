"""Numba unary kernels with exact wide products and explicit coefficient input."""
import numpy as np
from numba import njit
from .fast_common import _decode, _round


def kernel_resources(units=()):
    from .unary import implementation_config
    overrides = {}
    for unit in units:
        value = getattr(unit,'_sfu_configuration',None)
        if value is not None and unit.op == 'exp':
            key=unit.format_name
            if key in overrides and overrides[key]['exp'] != value['exp']:
                raise ValueError('Numba requires one exp implementation per format in a network')
            overrides[key]=value
    result = np.zeros((3, 256), np.uint64)
    for i, name in enumerate(('fp32', 'fp16', 'bf16')):
        c = overrides.get(name,implementation_config(name,'exp'))['exp']
        result[i, :9] = [c[k] for k in ('fraction_bits','table_bits','degree','constant_fraction','log2e',
                                      'overflow','zero','one_positive','one_negative')]
        result[i,9] = c['tiny']
        result[i,16:16+len(c['coefficients'])] = c['coefficients']
        result[i,32:32+len(c['table'])] = c['table']
    return result


@njit(cache=True)
def mul128(a, b):
    mask = np.uint64(0xffffffff)
    al, ah, bl, bh = a & mask, a >> np.uint64(32), b & mask, b >> np.uint64(32)
    low, p, q, high = al*bl, al*bh, ah*bl, ah*bh
    middle = (low >> np.uint64(32))+(p & mask)+(q & mask)
    return high+(p >> np.uint64(32))+(q >> np.uint64(32))+(middle >> np.uint64(32)), (middle << np.uint64(32)) | (low & mask)


@njit(cache=True)
def shift128(hi, lo, shift):
    if shift <= 0: return lo << np.uint64(-shift), False
    if shift < 64:
        return (lo >> np.uint64(shift)) | (hi << np.uint64(64-shift)), (lo & ((np.uint64(1) << np.uint64(shift))-np.uint64(1))) != 0
    if shift == 64: return hi, lo != 0
    if shift < 128:
        return hi >> np.uint64(shift-64), lo != 0 or (hi & ((np.uint64(1) << np.uint64(shift-64))-np.uint64(1))) != 0
    return np.uint64(0), hi != 0 or lo != 0


@njit(cache=True)
def integer_sqrt(n):
    root, remainder = np.uint64(0), np.uint64(0)
    for i in range(31, -1, -1):
        remainder = (remainder << np.uint64(2)) | ((n >> np.uint64(2*i)) & np.uint64(3))
        trial = (root << np.uint64(2)) | np.uint64(1)
        root <<= np.uint64(1)
        if remainder >= trial:
            remainder -= trial
            root |= np.uint64(1)
    return root, remainder != 0


@njit(cache=True)
def unary_one(raw, rm, op, width, eb, fb, bias, parameters):
    sign, sig, exponent, kind, signaling = _decode(raw, width, eb, fb, bias, 0)
    inf = np.uint64(((1 << eb)-1) << fb)
    signbit = np.uint64(sign) << np.uint64(width-1)
    if kind == 2: return inf | (np.uint64(1) << np.uint64(fb-1)), 16 if signaling else 0
    if op == 4:
        if rm != 0: raise ValueError('exp supports RNE only')
        if kind == 1: return (np.uint64(0) if sign else inf), 0
        if sig == 0: return np.uint64(bias << fb), 0
        cfg = parameters[0 if fb == 23 else 1 if fb == 10 else 2]
        mag = raw & ((np.uint64(1) << np.uint64(width-1))-np.uint64(1))
        if sign == 0 and mag >= cfg[5]: return inf, 5
        if sign and mag >= cfg[6]: return np.uint64(0), 3
        if mag <= cfg[8 if sign else 7]: return np.uint64(bias << fb), 1
        f, k, degree = np.int64(cfg[0]), np.int64(cfg[1]), np.int64(cfg[2])
        hi, lo = mul128(np.uint64(sig), cfg[4])
        scaled, tail = shift128(hi,lo,np.int64(cfg[3])-f-exponent)
        t = -np.int64(scaled)-int(tail) if sign else np.int64(scaled)
        if k == 0: t += 1 << (f-1)
        n = t >> f
        j = (t >> (f-k)) & ((1 << k)-1)
        r = np.uint64(t & ((1 << (f-k))-1))
        p = cfg[16+degree]
        for i in range(degree-1,-1,-1):
            hi, lo = mul128(p,r); term,_ = shift128(hi,lo,f)
            p = cfg[16+i]+term
        hi, lo = mul128(p,cfg[32+j]); value,_ = shift128(hi,lo,f)
        bits, flags = _round(value,n-f,0,0,width,eb,fb,bias,0)
        if bits >= inf: bits = inf-np.uint64(1)
        if bits == 0: bits = np.uint64(1)
        tiny = sign != 0 and mag >= cfg[9]
        normal = np.uint64(1) << np.uint64(fb)
        bits = min(bits,normal-np.uint64(1)) if tiny else max(bits,normal)
        return bits, 3 if tiny else 1
    if op == 5:
        if kind == 1: return signbit, 0
        if sig == 0: return signbit | inf, 8
    else:
        if sig == 0 and kind == 0: return (signbit if op == 6 else signbit | inf), (0 if op == 6 else 8)
        if sign: return inf | (np.uint64(1) << np.uint64(fb-1)), 16
        if kind == 1: return (inf if op == 6 else np.uint64(0)), 0
    while sig < (1 << fb):
        sig <<= 1
        exponent -= 1
    e = exponent+fb
    qbits = fb+4
    if op == 5:
        numerator = np.uint64(1) << np.uint64(qbits+fb)
        q = numerator//np.uint64(sig)
        sticky = numerator%np.uint64(sig) != 0
        result_exp = -e-qbits-1
    else:
        m = np.uint64(sig) << np.uint64(e & 1)
        if op == 6:
            radicand = m << np.uint64(2*qbits-fb)
            q, sticky = integer_sqrt(radicand)
            result_exp = e//2-qbits-1
        else:
            power = 2*qbits+fb
            nh = np.uint64(1) << np.uint64(power-64) if power >= 64 else np.uint64(0)
            nl = np.uint64(0) if power >= 64 else np.uint64(1) << np.uint64(power)
            q = np.uint64(0)
            for bit in range(qbits,-1,-1):
                candidate = q | (np.uint64(1) << np.uint64(bit))
                hi, lo = mul128(candidate*candidate,m)
                if hi < nh or (hi == nh and lo <= nl): q = candidate
            hi, lo = mul128(q*q,m)
            sticky = hi != nh or lo != nl
            result_exp = -(e//2)-qbits-1
        sign = 0
    return _round((q << np.uint64(1)) | np.uint64(sticky),result_exp,sign,rm,width,eb,fb,bias,0)
