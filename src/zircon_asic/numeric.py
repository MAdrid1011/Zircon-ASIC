"""Independent integer-only production arithmetic; no host floating operations."""
from .formats import FloatFormat, Decoded
from .types import Flags, Request, Response, Rounding


def _compare_scaled(n: int, d: int, shift: int) -> int:
    a, b = (n << shift, d) if shift >= 0 else (n, d << -shift)
    return (a > b) - (a < b)


def round_ratio(fmt: FloatFormat, n: int, d: int, exponent: int, sign: bool,
                mode: Rounding) -> tuple[int, Flags]:
    """Round n/d * 2**exponent exactly, including gradual underflow."""
    sign_bits = int(sign) << (fmt.width - 1)
    if not n:
        return sign_bits, Flags.NONE
    top = n.bit_length() - d.bit_length() + exponent
    if _compare_scaled(n, d, exponent - top) < 0:
        top -= 1
    if fmt.encoding != "ieee":
        maximum = fmt.decode(fmt.max_bits)
        if _compare_scaled(n, d * maximum.significand, exponent - maximum.exponent) > 0:
            return sign_bits | fmt.max_bits, Flags.OF | Flags.NX
    quantum = max(top, fmt.emin) - fmt.fraction
    # IEEE tininess-after-rounding uses an unbounded exponent range at the
    # destination precision, before gradual-underflow rounding to the grid.
    tiny_after = top < fmt.emin
    if top == fmt.emin-1:
        sh = exponent-(top-fmt.fraction)
        nn0, dd0 = (n << sh, d) if sh >= 0 else (n, d << -sh)
        q0, r0 = divmod(nn0, dd0)
        inc0 = (mode == Rounding.RNE and (2*r0 > dd0 or (2*r0 == dd0 and q0 & 1))) or \
               (mode == Rounding.RMM and 2*r0 >= dd0) or \
               (mode == Rounding.RUP and not sign and bool(r0)) or \
               (mode == Rounding.RDN and sign and bool(r0))
        tiny_after = q0 + int(bool(inc0)) < (1 << (fmt.fraction+1))
    shift = exponent - quantum
    nn, dd = (n << shift, d) if shift >= 0 else (n, d << -shift)
    q, r = divmod(nn, dd)
    increment = (mode == Rounding.RNE and (2*r > dd or (2*r == dd and bool(q & 1)))) or \
                (mode == Rounding.RMM and 2*r >= dd) or \
                (mode == Rounding.RUP and not sign and bool(r)) or \
                (mode == Rounding.RDN and sign and bool(r))
    q += int(increment)
    if q >= (1 << (fmt.fraction + 1)):
        q >>= 1
        quantum += 1
    result_top = quantum + fmt.fraction
    if result_top > fmt.emax:
        to_inf = mode in (Rounding.RNE, Rounding.RMM) or \
                 (mode == Rounding.RUP and not sign) or (mode == Rounding.RDN and sign)
        return sign_bits | (fmt.inf_bits if to_inf else fmt.max_bits), Flags.OF | Flags.NX
    tiny = q < (1 << fmt.fraction)
    exp_bits = 0 if tiny else result_top + fmt.bias
    bits = sign_bits | (exp_bits << fmt.fraction) | (q & ((1 << fmt.fraction) - 1))
    flags = Flags.NX if r else Flags.NONE
    if tiny_after and r:
        flags |= Flags.UF
    return bits, flags


def _signed_sum(a: int, ea: int, sa: bool, b: int, eb: int, sb: bool):
    exponent = min(ea, eb)
    n = (-a if sa else a) * (1 << (ea - exponent)) + (-b if sb else b) * (1 << (eb - exponent))
    return n, exponent


def float_compute(fmt: FloatFormat, op: str, req: Request) -> Response:
    mode = Rounding(req.rounding)
    a, b = fmt.decode(req.a), fmt.decode(req.b)
    c = fmt.decode(req.c) if op == "fma" else Decoded(False, 0, 0)
    operands = (a, b, c) if op == "fma" else (a, b)
    sign = a.sign ^ b.sign
    az, bz = a.kind == "finite" and not a.significand, b.kind == "finite" and not b.significand
    invalid_product = (a.kind == "inf" and bz) or (b.kind == "inf" and az)
    invalid = any(x.signaling for x in operands)
    if op in ("mul", "fma"):
        invalid |= invalid_product
    if any(x.kind == "nan" for x in operands):
        return Response(fmt.nan_bits, Flags.NV if invalid else Flags.NONE, req.tag)
    if op == "add":
        invalid |= a.kind == b.kind == "inf" and a.sign != b.sign
    if op == "fma":
        invalid |= (a.kind == "inf" or b.kind == "inf") and c.kind == "inf" and sign != c.sign
    if op == "div":
        invalid |= (az and bz) or a.kind == b.kind == "inf"
    if invalid:
        return Response(fmt.nan_bits, Flags.NV, req.tag)
    def special(bits, flags=Flags.NONE, s=sign):
        return Response(bits | (int(s) << (fmt.width - 1)), flags, req.tag)
    if op == "add":
        if a.kind == "inf" or b.kind == "inf":
            return special(fmt.inf_bits, s=a.sign if a.kind == "inf" else b.sign)
        n, e = _signed_sum(a.significand, a.exponent, a.sign, b.significand, b.exponent, b.sign)
        sign = n < 0 if n else (a.sign if az and bz and a.sign == b.sign else mode == Rounding.RDN)
        bits, flags = round_ratio(fmt, abs(n), 1, e, sign, mode)
    elif op in ("mul", "fma"):
        if a.kind == "inf" or b.kind == "inf":
            return special(fmt.inf_bits)
        if op == "fma" and c.kind == "inf":
            return special(fmt.inf_bits, s=c.sign)
        n, e = a.significand * b.significand, a.exponent + b.exponent
        if op == "fma":
            product_zero = not n
            n, e = _signed_sum(n, e, sign, c.significand, c.exponent, c.sign)
            sign = n < 0 if n else (sign if product_zero and not c.significand and sign == c.sign else mode == Rounding.RDN)
        bits, flags = round_ratio(fmt, abs(n), 1, e, sign, mode)
    elif op == "div":
        if a.kind == "inf":
            return special(fmt.inf_bits)
        if b.kind == "inf":
            return special(0)
        if bz:
            return special(fmt.inf_bits if fmt.encoding == "ieee" else fmt.max_bits, Flags.DZ)
        bits, flags = round_ratio(fmt, a.significand, b.significand, a.exponent - b.exponent, sign, mode)
    else:
        raise ValueError(f"unknown floating operation {op}")
    return Response(bits, flags, req.tag)


def int_compute(width: int, signed: bool, op: str, req: Request) -> Response:
    mask = (1 << width) - 1
    if not 0 <= int(req.a) <= mask or not 0 <= int(req.b) <= mask:
        raise ValueError(f"operands must be raw {width}-bit integers")
    def decode(x):
        x = int(x)
        return x - (1 << width) if signed and x & (1 << (width-1)) else x
    a, b = decode(req.a), decode(req.b)
    lo, hi = (-(1 << (width-1)), (1 << (width-1))-1) if signed else (0, mask)
    remainder, flags = 0, Flags.NONE
    if op == "add":
        value = a + b
    elif op == "mul":
        value = a * b
    elif op == "div":
        if not b:
            return Response(mask, Flags.DZ, req.tag, int(req.a))
        value = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            value = -value
        remainder = a - value * b
    else:
        raise ValueError(f"unknown integer operation {op}")
    if not lo <= value <= hi:
        flags |= Flags.OF
    return Response(value & mask, flags, req.tag, remainder & mask)
