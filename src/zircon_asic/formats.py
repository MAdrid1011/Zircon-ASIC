"""Floating encodings. Values are represented as integer * 2**exponent."""
from dataclasses import dataclass
from .contract import contract


@dataclass(frozen=True, slots=True)
class Decoded:
    sign: bool
    significand: int
    exponent: int
    kind: str = "finite"
    signaling: bool = False


@dataclass(frozen=True, slots=True)
class FloatFormat:
    name: str
    width: int
    exponent: int
    fraction: int
    bias: int
    encoding: str

    @property
    def emin(self):
        return 1 - self.bias

    @property
    def emax(self):
        return (1 << self.exponent) - (2 if self.encoding == "ieee" else 1) - self.bias

    @property
    def max_bits(self):
        return ((1 << (self.width - 1)) - 1) - ({"ieee": 1 << self.fraction, "finite_nan": 1, "finite": 0}[self.encoding])

    @property
    def nan_bits(self):
        if self.encoding == "finite":
            return 0
        if self.encoding == "finite_nan":
            return (1 << (self.width - 1)) - 1
        return (((1 << self.exponent) - 1) << self.fraction) | (1 << (self.fraction - 1))

    @property
    def inf_bits(self):
        return ((1 << self.exponent) - 1) << self.fraction

    def decode(self, bits: int) -> Decoded:
        if not 0 <= int(bits) < (1 << self.width):
            raise ValueError(f"{self.name}: operand does not fit {self.width} bits")
        bits = int(bits)
        sign = bool(bits >> (self.width - 1))
        frac = bits & ((1 << self.fraction) - 1)
        exp = (bits >> self.fraction) & ((1 << self.exponent) - 1)
        if exp == (1 << self.exponent) - 1:
            if self.encoding == "ieee":
                if frac == 0:
                    return Decoded(sign, 0, 0, "inf")
                return Decoded(sign, 0, 0, "nan", not bool(frac & (1 << (self.fraction - 1))))
            if self.encoding == "finite_nan" and frac == (1 << self.fraction) - 1:
                return Decoded(sign, 0, 0, "nan")
        return Decoded(sign, frac if exp == 0 else (1 << self.fraction) | frac,
                       (self.emin if exp == 0 else exp - self.bias) - self.fraction)


FORMATS = {name: FloatFormat(name=name, **spec) for name, spec in contract()["formats"].items()}
FP32 = FORMATS["fp32"]
FP16 = FORMATS["fp16"]
E4M3FN = FORMATS["e4m3fn"]
E5M2 = FORMATS["e5m2"]
E2M1 = FORMATS["e2m1"]
