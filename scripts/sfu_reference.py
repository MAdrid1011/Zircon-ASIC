"""Independent MPFR oracle for unary functions (development dependency: gmpy2).

The decoder, destination packing and flags deliberately do not import the
production arithmetic. Directed MPFR enclosures are refined until both ends
give the same destination result; RMM is handled by the packer's midpoint test.
"""
import gmpy2 as g

FORMATS = {"fp32": (32, 8, 23, 127), "fp16": (16, 5, 10, 15), "bf16": (16, 8, 7, 127)}


def decode(name, bits):
    w, eb, fb, bias = FORMATS[name]
    sign, e, m = bits >> (w-1), (bits >> fb) & ((1 << eb)-1), bits & ((1 << fb)-1)
    if e == (1 << eb)-1:
        return sign, "nan" if m else "inf", bool(m and not m & (1 << (fb-1))), None
    with g.context(g.get_context(), precision=128):
        value = g.mul_2exp(g.mpfr(m | ((1 << fb) if e else 0)), (e-bias if e else 1-bias)-fb)
        return sign, "finite", False, -value if sign else value


def special(name, op, raw):
    w, eb, fb, bias = FORMATS[name]
    sign, kind, signaling, value = decode(name, raw)
    inf = ((1 << eb)-1) << fb
    nan = inf | (1 << (fb-1))
    signed = sign << (w-1)
    if kind == "nan": return nan, 16 if signaling else 0
    if op == "exp":
        if kind == "inf": return (0 if sign else inf), 0
        if value == 0: return bias << fb, 0
    elif op == "rcp":
        if kind == "inf": return signed, 0
        if value == 0: return signed | inf, 8
    else:
        if value == 0: return (signed if op == "sqrt" else signed | inf), (0 if op == "sqrt" else 8)
        if sign: return nan, 16
        if kind == "inf": return (inf if op == "sqrt" else 0), 0
    return None


def pack(name, value, rm):
    w, eb, fb, bias = FORMATS[name]
    sign = value < 0
    v = abs(value)
    top = int(g.get_exp(v))-1
    emin, emax = 1-bias, (1 << eb)-2-bias
    quantum = max(top, emin)-fb
    scaled = g.mul_2exp(v, -quantum)
    q = int(g.floor(scaled)); tail = scaled-q
    def increment(q, tail):
        return ((rm == 0 and (tail > .5 or (tail == .5 and q & 1))) or
                (rm == 4 and tail >= .5) or (tail != 0 and ((rm == 2 and sign) or (rm == 3 and not sign))))
    tiny_after = top < emin
    if top == emin-1:
        norm = g.mul_2exp(v, fb-top); nq = int(g.floor(norm))
        tiny_after = nq+int(increment(nq, norm-nq)) < 1 << (fb+1)
    q += int(increment(q, tail))
    if q >= 1 << (fb+1): q >>= 1; quantum += 1
    flags = (3 if tiny_after else 1) if tail else 0
    if quantum+fb > emax:
        to_inf = rm in (0, 4) or (rm == 2 and sign) or (rm == 3 and not sign)
        mag = (((1 << eb)-1) << fb) if to_inf else (((1 << eb)-1) << fb)-1
        return (int(sign) << (w-1)) | mag, 5
    efield = 0 if q < 1 << fb else quantum+fb+bias
    return (int(sign) << (w-1)) | (efield << fb) | (q & ((1 << fb)-1)), flags


def compute(name, op, raw, rm=0):
    s = special(name, op, raw)
    if s is not None: return s
    _, _, _, x = decode(name, raw)
    # Classify remote exp inputs before asking MPFR to form enormous exponents.
    if op == "exp" and abs(x) > 1024:
        w, eb, fb, _ = FORMATS[name]
        return (0, 3) if x < 0 else (((1 << eb)-1) << fb, 5)
    precision = 128
    while True:
        results = []
        for rounding in (g.RoundDown, g.RoundUp):
            with g.context(g.get_context(), precision=precision, round=rounding):
                xx = g.mpfr(x)
                y = g.exp(xx) if op == "exp" else 1/xx if op == "rcp" else g.sqrt(xx) if op == "sqrt" else g.rec_sqrt(xx)
                result = pack(name, y, rm)
                if op == "exp": result = result[0], result[1] | 1
                results.append(result)
        if results[0] == results[1]: return results[0]
        precision *= 2
        if precision > 16384: raise ArithmeticError((name, op, raw, rm, "unresolved oracle enclosure"))
