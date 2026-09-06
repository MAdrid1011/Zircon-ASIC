"""Common bit-level arithmetic and cycle interfaces."""
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Any


class Rounding(IntEnum):
    RNE = 0
    RTZ = 1
    RDN = 2
    RUP = 3
    RMM = 4


class Flags(IntFlag):
    NONE = 0
    NX = 1
    UF = 2
    OF = 4
    DZ = 8
    NV = 16


@dataclass(frozen=True, slots=True)
class Request:
    a: int
    b: int
    c: int = 0
    rounding: Rounding = Rounding.RNE
    tag: int = 0

    def __post_init__(self):
        import operator
        for value in (self.a,self.b,self.c,self.tag,self.rounding):
            operator.index(value)
        if not 0 <= self.tag < (1 << 32):
            raise ValueError("tag must fit 32 unsigned bits")
        Rounding(self.rounding)


@dataclass(frozen=True, slots=True)
class Response:
    bits: int
    flags: Flags = Flags.NONE
    tag: int = 0
    remainder: int = 0


@dataclass(frozen=True, slots=True)
class BatchResponse:
    bits: Any
    flags: Any
    tags: Any
    remainder: Any


@dataclass(frozen=True, slots=True)
class Inputs:
    request: Request | None = None
    out_ready: bool = True
    reset: bool = False
    flush: bool = False


@dataclass(frozen=True, slots=True)
class Outputs:
    in_ready: bool
    out_valid: bool
    response: Response | None
    accepted: bool
    delivered: bool
    occupancy: int
    stage_valid: tuple[bool, ...]
    phase: str = "idle"
    iteration: int = 0


@dataclass(slots=True)
class Statistics:
    cycles: int = 0
    accepted: int = 0
    delivered: int = 0
    cancelled: int = 0
    input_stalls: int = 0
    output_stalls: int = 0
    occupancy_sum: int = 0

    def report(self) -> dict[str, int | float]:
        from dataclasses import asdict
        result = asdict(self)
        result["throughput"] = self.delivered / self.cycles if self.cycles else 0.0
        result["mean_occupancy"] = self.occupancy_sum / self.cycles if self.cycles else 0.0
        return result
