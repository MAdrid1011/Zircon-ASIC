"""Test-only exact rational oracle. Enumerates encodings, never calls production arithmetic."""
from fractions import Fraction
from bisect import bisect_left
from functools import lru_cache


SPECS = {"e2m1":(4,2,1,1,"finite"),"e4m3fn":(8,4,3,7,"finite_nan"),"e5m2":(8,5,2,15,"ieee")}


def decode(name,bits):
    w,eb,fb,bias,kind = SPECS[name]
    sign = bool(bits & (1 << (w-1)))
    e,m = (bits >> fb) & ((1 << eb)-1),bits & ((1 << fb)-1)
    if e == (1 << eb)-1:
        if kind == "ieee":
            if m == 0: return sign,"inf",False
            return sign,"nan",not bool(m & (1 << (fb-1)))
        if kind == "finite_nan" and m == (1 << fb)-1: return sign,"nan",False
    v = Fraction(m if e == 0 else m+(1 << fb)) * Fraction(2)**((1-bias if e == 0 else e-bias)-fb)
    return sign,v,False


@lru_cache(None)
def values(name):
    w,*_ = SPECS[name]
    return [v for i in range(1 << (w-1)) if isinstance((v := decode(name,i)[1]),Fraction)]


def choose(lo,hi,value,rm,sign,even):
    if value == lo: return False
    if rm == 1: return False
    if rm == 2: return sign
    if rm == 3: return not sign
    delta = value-lo-(hi-value)
    return delta > 0 or (delta == 0 and (rm == 4 or not even))


def unbounded_round(value,fb,rm,sign):
    if value == 0: return value
    exponent = 0
    scaled = value
    while scaled >= 2: scaled /= 2; exponent += 1
    while scaled < 1: scaled *= 2; exponent -= 1
    grid = Fraction(2)**(exponent-fb)
    q = value//grid
    return (q+choose(q*grid,(q+1)*grid,value,rm,sign,q%2 == 0))*grid


def compute(name,op,a,b,c=0,rm=0):
    w,eb,fb,bias,kind = SPECS[name]
    sa,va,na = decode(name,a); sb,vb,nb = decode(name,b)
    sc,vc,nc = decode(name,c) if op == "fma" else (False,Fraction(0),False)
    ps = sa ^ sb; nan = va == "nan" or vb == "nan" or vc == "nan"
    nanbits = 0 if kind == "finite" else (1 << (w-1))-1 if kind == "finite_nan" else (((1 << eb)-1) << fb) | (1 << (fb-1))
    invalid = na or nb or nc
    if op in ("mul","fma"): invalid |= (va == "inf" and vb == 0) or (vb == "inf" and va == 0)
    if nan: return nanbits,16 if invalid else 0
    if op == "add": invalid |= va == vb == "inf" and sa != sb
    if op == "fma": invalid |= (va == "inf" or vb == "inf") and vc == "inf" and ps != sc
    if op == "div": invalid |= (va == 0 and vb == 0) or va == vb == "inf"
    if invalid: return nanbits,16
    table = values(name); inf = ((1 << eb)-1) << fb
    maximum = len(table)-1
    def pack(bits,sign,flags=0): return bits | (int(sign) << (w-1)),flags
    if op == "div":
        if va == "inf": return pack(inf,ps)
        if vb == "inf": return pack(0,ps)
        if vb == 0: return pack(inf if kind == "ieee" else maximum,ps,8)
        v,sign = va/vb,ps
    elif op == "mul":
        if va == "inf" or vb == "inf": return pack(inf,ps)
        v,sign = va*vb,ps
    else:
        if op == "add":
            if va == "inf" or vb == "inf": return pack(inf,sa if va == "inf" else sb)
            x,sx,y,sy = va,sa,vb,sb
        else:
            if va == "inf" or vb == "inf": return pack(inf,ps)
            if vc == "inf": return pack(inf,sc)
            x,sx,y,sy = va*vb,ps,vc,sc
        signed = (-x if sx else x)+(-y if sy else y)
        v = abs(signed)
        sign = signed < 0 if signed else (sx if x == y == 0 and sx == sy else rm == 2)
    unrestricted = unbounded_round(v,fb,rm,sign)
    overflow = v > table[-1] if kind != "ieee" else unrestricted > table[-1]
    if overflow:
        to_inf = kind == "ieee" and (rm in (0,4) or (rm == 2 and sign) or (rm == 3 and not sign))
        return pack(inf if to_inf else maximum,sign,5)
    i = bisect_left(table,v)
    if i < len(table) and table[i] == v: return pack(i,sign)
    if i == len(table): i = maximum
    else: i = i-1+choose(table[i-1],table[i],v,rm,sign,(i-1)%2 == 0)
    tiny = 0 < unrestricted < Fraction(2)**(1-bias)
    return pack(i,sign,3 if tiny else 1)
