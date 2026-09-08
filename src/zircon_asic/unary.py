"""Integer unary arithmetic and reproducible fixed-point exp evaluation."""
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from math import isqrt
import json

from .numeric import round_ratio
from .types import Flags, Request, Response, Rounding

OPERATIONS = {"add": 0, "mul": 1, "fma": 2, "div": 3, "exp": 4, "rcp": 5, "sqrt": 6, "rsqrt": 7}


@lru_cache(maxsize=1)
def resources():
    return json.loads(files("zircon_asic").joinpath("data/sfu.json").read_text())


def resources_hash():
    return sha256(files("zircon_asic").joinpath("data/sfu.json").read_bytes()).hexdigest()


def implementation_config(format_name, operation):
    data = resources()
    selected = data.get("implementations", {}).get(f"{format_name}.{operation}")
    return selected["configuration"] if selected else data["formats"][format_name]


def validate_request(fmt, op, request):
    for value in (request.a, request.b, request.c):
        if not 0 <= value < (1 << fmt.width):
            raise ValueError(f"{fmt.name}: operand does not fit {fmt.width} bits")
    if op == "exp" and request.rounding != Rounding.RNE:
        raise ValueError("exp supports RNE only")


def root_ratio(fmt, n, d, exponent, mode):
    """Correctly round sqrt(n/d * 2**exponent) with exact midpoint tests."""
    top = n.bit_length()-d.bit_length()+exponent
    shift = exponent-top
    if ((n << shift) < d) if shift >= 0 else (n < (d << -shift)):
        top -= 1
    quantum = top//2-fmt.fraction
    shift = exponent-2*quantum
    nn, dd = (n << shift, d) if shift >= 0 else (n, d << -shift)
    q = isqrt(nn//dd)
    exact = q*q*dd == nn
    midpoint = 4*nn-dd*(2*q+1)**2
    inc = ((mode == Rounding.RNE and (midpoint > 0 or (midpoint == 0 and q & 1))) or
           (mode == Rounding.RMM and midpoint >= 0) or (mode == Rounding.RUP and not exact))
    bits, _ = round_ratio(fmt, q+int(inc), 1, quantum, False, Rounding.RTZ)
    return bits, Flags.NONE if exact else Flags.NX


def exp_finite(fmt, a, raw, configuration=None):
    cfg = (configuration or implementation_config(fmt.name, "exp"))["exp"]
    mag = raw & ((1 << (fmt.width-1))-1)
    if not a.sign and mag >= cfg["overflow"]: return fmt.inf_bits, Flags.OF | Flags.NX
    if a.sign and mag >= cfg["zero"]: return 0, Flags.UF | Flags.NX
    if mag <= cfg["one_negative" if a.sign else "one_positive"]:
        return fmt.bias << fmt.fraction, Flags.NX
    f, k = cfg["fraction_bits"], cfg["table_bits"]
    product = a.significand*cfg["log2e"]
    if a.sign: product = -product
    shift = a.exponent+f-cfg["constant_fraction"]
    t = product << shift if shift >= 0 else product >> -shift
    if k == 0:
        t += 1 << (f-1)
    n = t >> f
    j = (t >> (f-k)) & ((1 << k)-1)
    r = t & ((1 << (f-k))-1)
    coeff = cfg["coefficients"]
    polynomial = coeff[-1]
    for c in reversed(coeff[:-1]): polynomial = c+((polynomial*r) >> f)
    value = (polynomial*cfg["table"][j]) >> f
    bits, flags = round_ratio(fmt, value, 1, n-f, False, Rounding.RNE)
    # Category boundaries are generated independently from MPFR RNE results.
    if bits >= fmt.inf_bits: bits = fmt.max_bits
    if bits == 0: bits = 1
    minimum_normal = 1 << fmt.fraction
    tiny = a.sign and mag >= cfg["tiny"]
    bits = min(bits, minimum_normal-1) if tiny else max(bits, minimum_normal)
    return bits, Flags.NX | (Flags.UF if tiny else Flags.NONE)


def unary_compute(fmt, op, request: Request, configuration=None):
    validate_request(fmt, op, request)
    a = fmt.decode(request.a)
    sign = int(a.sign) << (fmt.width-1)
    def response(bits, flags=Flags.NONE): return Response(bits, flags, request.tag)
    if a.kind == "nan": return response(fmt.nan_bits, Flags.NV if a.signaling else Flags.NONE)
    if op == "exp":
        if a.kind == "inf": return response(0 if a.sign else fmt.inf_bits)
        if not a.significand: return response(fmt.bias << fmt.fraction)
        return response(*exp_finite(fmt, a, request.a, configuration))
    if op == "rcp":
        if a.kind == "inf": return response(sign)
        if not a.significand: return response(sign | fmt.inf_bits, Flags.DZ)
        return response(*round_ratio(fmt, 1, a.significand, -a.exponent, a.sign, request.rounding))
    if not a.significand and a.kind == "finite":
        return response(sign if op == "sqrt" else sign | fmt.inf_bits, Flags.NONE if op == "sqrt" else Flags.DZ)
    if a.sign: return response(fmt.nan_bits, Flags.NV)
    if a.kind == "inf": return response(fmt.inf_bits if op == "sqrt" else 0)
    if op == "sqrt": return response(*root_ratio(fmt, a.significand, 1, a.exponent, request.rounding))
    if op == "rsqrt": return response(*root_ratio(fmt, 1, a.significand, -a.exponent, request.rounding))
    raise ValueError(f"unknown unary operation {op}")
