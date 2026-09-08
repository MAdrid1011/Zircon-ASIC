"""Functional arithmetic and explicit elastic/iterative cycle machines."""
from dataclasses import asdict
import numpy as np
from .types import Request, Response, Inputs, Outputs, Statistics, BatchResponse, Rounding
from .formats import FloatFormat, FORMATS
from .numeric import float_compute, int_compute
from .contract import Timing, timing_for, contract_hash


class ArithmeticUnit:
    def __init__(self, format_name: str, op: str, *, timing: Timing | None = None):
        self.format_name, self.op = format_name, op
        self.timing = timing_for(format_name, op) if timing is None else timing
        if timing is not None and timing.matched:
            raise ValueError("custom timing must explicitly set matched=False")
        self.stats = Statistics()
        self.cycle = 0
        self._slots = [None] * self.timing.capacity
        self._phase = 0
        self._pending = None
        self.events: list[dict] | None = None

    def compute(self, request: Request) -> Response:
        raise NotImplementedError

    def compute_batch(self, a, b=0, c=0, *, rounding=Rounding.RNE, tags=0, backend="auto") -> BatchResponse:
        from .batch import compute_batch
        return compute_batch(self, a, b, c, rounding=rounding, tags=tags, backend=backend)

    def _observe(self, inputs: Inputs) -> Outputs:
        t = self.timing
        blocked = inputs.reset or inputs.flush
        if t.kind == "elastic":
            valid = tuple(x is not None for x in self._slots)
            ready = inputs.out_ready
            for v in reversed(valid):
                ready = not v or ready
            response = self._slots[-1]
            phase, iteration = "pipeline", 0
        else:
            valid = tuple(self._phase == i + 1 for i in range(t.latency))
            response = self._slots[0] if self._phase == t.latency else None
            ready = self._phase == 0 or (response is not None and inputs.out_ready)
            phase = "idle" if not self._phase else ("response" if response is not None else t.phases[self._phase])
            iteration = sum(p == "iterate" for p in t.phases[:self._phase])
        ready = bool(ready and not blocked)
        out_valid = response is not None and not blocked
        return Outputs(ready, out_valid, response if out_valid else None,
                       ready and inputs.request is not None, out_valid and inputs.out_ready,
                       sum(valid), valid, phase, iteration)

    def eval(self, inputs: Inputs = Inputs()) -> Outputs:
        """Observe before the edge. Repeated evaluations do not advance state."""
        if inputs.request is not None and getattr(self, "arity", 2) == 1:
            from .unary import validate_request
            validate_request(self.format, self.op, inputs.request)
        output = self._observe(inputs)
        self._pending = (inputs, output)
        return output

    def tick(self) -> Outputs:
        """Commit the last evaluated inputs exactly once."""
        if self._pending is None:
            raise RuntimeError("tick requires eval; use step(inputs) for both")
        inputs, output = self._pending
        self._pending = None
        s = self.stats
        s.cycles += 1
        s.occupancy_sum += output.occupancy
        s.input_stalls += int(inputs.request is not None and not output.in_ready and not (inputs.reset or inputs.flush))
        s.output_stalls += int(output.out_valid and not inputs.out_ready)
        if inputs.reset or inputs.flush:
            s.cancelled += output.occupancy
            self._slots = [None] * self.timing.capacity
            self._phase = 0
        else:
            new = self.compute(inputs.request) if output.accepted else None
            if self.timing.kind == "elastic":
                old = self._slots
                ready = inputs.out_ready
                nxt = old.copy()
                for i in reversed(range(len(old))):
                    ready = old[i] is None or ready
                    if ready:
                        nxt[i] = (new if i == 0 else old[i-1])
                self._slots = nxt
            elif output.accepted:
                self._slots[0] = new
                self._phase = 1
            elif output.delivered:
                self._slots[0] = None
                self._phase = 0
            elif 0 < self._phase < self.timing.latency:
                self._phase += 1
            s.accepted += int(output.accepted)
            s.delivered += int(output.delivered)
        if self.events is not None:
            self.events.append(dict(cycle=self.cycle, inputs=asdict(inputs), outputs=asdict(output)))
        self.cycle += 1
        return output

    def step(self, inputs: Inputs = Inputs()) -> Outputs:
        self.eval(inputs)
        return self.tick()

    def reset(self):
        """Immediate out-of-band reset. Use Inputs(reset=True) for a clocked reset."""
        self._slots = [None] * self.timing.capacity
        self._phase, self._pending = 0, None
        self.stats, self.cycle = Statistics(), 0

    def flush(self):
        self.stats.cancelled += sum(x is not None for x in self._slots)
        self._slots = [None] * self.timing.capacity
        self._phase, self._pending = 0, None

    def describe(self) -> dict:
        from .evidence import qualification
        evidence = qualification(self.format_name,self.op,getattr(self,"signed",True)) if self.timing.matched else {"status":"unqualified","reason":"custom exploration timing"}
        return dict(format=self.format_name, operation=self.op, timing=asdict(self.timing),
                    latency=self.timing.latency, initiation_interval=self.timing.initiation_interval,
                    capacity=self.timing.capacity, contract_hash=contract_hash(),
                    cycle_profile="hardware-contract" if self.timing.matched else "exploration",
                    qualification=evidence["status"], evidence=evidence, **self._parameters())

    def _parameters(self):
        return {}


class FloatingPointUnit(ArithmeticUnit):
    def __init__(self, format: str | FloatFormat, op: str, **kwargs):
        self.format = FORMATS[format] if isinstance(format, str) else format
        self.arity = 1 if op in ("exp", "rcp", "sqrt", "rsqrt") else 3 if op == "fma" else 2
        if self.arity == 1 and self.format.name not in ("fp32", "fp16", "bf16"):
            raise ValueError("unary functions support fp32, fp16 and bf16")
        super().__init__(self.format.name, op, **kwargs)

    def compute(self, request: Request) -> Response:
        if self.arity == 1:
            from .unary import unary_compute
            return unary_compute(self.format, self.op, request, getattr(self,"_sfu_configuration",None))
        if self.format.name == "e2m1":
            from .lookup import fp4_compute
            return fp4_compute(self.op,request)
        return float_compute(self.format, self.op, request)

    def _parameters(self):
        result = dict(arity=self.arity)
        if self.arity == 1:
            from .unary import resources_hash
            result.update(accuracy="faithful" if self.op == "exp" else "correctly-rounded",
                          rounding_modes=["RNE"] if self.op == "exp" else [r.name for r in Rounding],
                          resources_sha256=resources_hash(), implementation=self.timing.variant)
        return result


class IntegerUnit(ArithmeticUnit):
    def __init__(self, width: int, op: str, *, signed=True, **kwargs):
        if width not in (8, 16, 32):
            raise ValueError("supported integer widths: 8, 16, 32")
        self.width, self.signed = width, bool(signed)
        super().__init__(f"int{width}", op, **kwargs)

    def compute(self, request: Request) -> Response:
        return int_compute(self.width, self.signed, self.op, request)

    def _parameters(self):
        return dict(signed=self.signed)


class FpAdd(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "add", **kwargs)
class FpMul(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "mul", **kwargs)
class FpFma(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "fma", **kwargs)
class FpDiv(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "div", **kwargs)
class FpExp(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "exp", **kwargs)
class FpRcp(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "rcp", **kwargs)
class FpSqrt(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "sqrt", **kwargs)
class FpRsqrt(FloatingPointUnit):
    def __init__(self, format="fp32", **kwargs): super().__init__(format, "rsqrt", **kwargs)
class IntAdd(IntegerUnit):
    def __init__(self, width=32, **kwargs): super().__init__(width, "add", **kwargs)
class IntMul(IntegerUnit):
    def __init__(self, width=32, **kwargs): super().__init__(width, "mul", **kwargs)
class IntDiv(IntegerUnit):
    def __init__(self, width=32, **kwargs): super().__init__(width, "div", **kwargs)


def _convenience(name, base, arg):
    def __init__(self, **kwargs): base.__init__(self, arg, **kwargs)
    return type(name, (base,), {"__init__": __init__, "__module__": __name__, "__doc__": f"{name} with the shared balanced-v1 timing contract."})


for _prefix, _fmt in [("FP32", "fp32"), ("FP16", "fp16"), ("BF16", "bf16"), ("FP8E4M3FN", "e4m3fn"), ("FP8E5M2", "e5m2"), ("FP4", "e2m1")]:
    for _suffix, _base in [("Add", FpAdd), ("Mul", FpMul), ("Fma", FpFma), ("Div", FpDiv)]:
        globals()[_prefix+_suffix] = _convenience(_prefix+_suffix, _base, _fmt)
for _width in (8, 16, 32):
    for _suffix, _base in [("Add", IntAdd), ("Mul", IntMul), ("Div", IntDiv)]:
        globals()[f"INT{_width}{_suffix}"] = _convenience(f"INT{_width}{_suffix}", _base, _width)

for _prefix, _fmt in [("FP32", "fp32"), ("FP16", "fp16"), ("BF16", "bf16")]:
    for _suffix, _base in [("Exp", FpExp), ("Rcp", FpRcp), ("Sqrt", FpSqrt), ("Rsqrt", FpRsqrt)]:
        globals()[_prefix+_suffix] = _convenience(_prefix+_suffix, _base, _fmt)

__all__ = [n for n in globals() if n.startswith(("FP", "BF16", "INT", "Fp", "Int"))] + ["ArithmeticUnit", "FloatingPointUnit"]
