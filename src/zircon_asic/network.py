"""Synchronous compositional simulation with global evaluation and commit."""
from collections import deque
from dataclasses import dataclass, field
from .types import Request, Response, Inputs, Outputs, Statistics
from .units import ArithmeticUnit
from .contract import Timing


class DelayLine(ArithmeticUnit):
    """Elastic payload delay; b carries flags and c carries the remainder."""
    transport = True

    def __init__(self, cycles=1):
        super().__init__("transport", "delay", timing=Timing(cycles,"elastic",("transport",)*cycles,"delay",False))

    def compute(self, request):
        return Response(request.a,request.b,request.tag,request.c)


class FIFO(DelayLine):
    """Non-fall-through FIFO, one-cycle minimum latency, simultaneous pop/push."""
    def __init__(self, depth=2):
        if depth < 1: raise ValueError("FIFO depth must be positive")
        super().__init__(1)
        self.depth = depth
        self._queue = deque()

    def _observe(self, inputs):
        cancel = inputs.reset or inputs.flush
        valid = bool(self._queue) and not cancel
        ready = (len(self._queue) < self.depth or (bool(self._queue) and inputs.out_ready)) and not cancel
        return Outputs(ready,valid,self._queue[0] if valid else None,ready and inputs.request is not None,
                       valid and inputs.out_ready,len(self._queue),tuple(i < len(self._queue) for i in range(self.depth)),"fifo")

    def tick(self):
        if self._pending is None: raise RuntimeError("tick requires eval")
        inp,out = self._pending; self._pending = None
        self.stats.cycles += 1; self.stats.occupancy_sum += out.occupancy
        if inp.reset or inp.flush:
            self.stats.cancelled += len(self._queue); self._queue.clear()
        else:
            if out.delivered: self._queue.popleft(); self.stats.delivered += 1
            if out.accepted: self._queue.append(self.compute(inp.request)); self.stats.accepted += 1
            self.stats.input_stalls += int(inp.request is not None and not out.in_ready)
            self.stats.output_stalls += int(out.out_valid and not inp.out_ready)
        self.cycle += 1
        return out

    def reset(self):
        super().reset(); self._queue.clear()

    def flush(self):
        self.stats.cancelled += len(self._queue); self._queue.clear(); self._pending = None

    def describe(self):
        d = super().describe(); d.update(capacity=self.depth,minimum_latency=1,variant="non_fall_through_fifo")
        return d


@dataclass
class InputSource:
    requests: list[Request]
    position: int = 0

    def peek(self):
        return self.requests[self.position] if self.position < len(self.requests) else None


@dataclass
class OutputSink:
    received: list[tuple[int,Response]] = field(default_factory=list)


@dataclass(frozen=True)
class Connection:
    source: str
    destination: str
    b: int = 0
    c: int = 0
    rounding: int = 0


class Network:
    """Directed single-stream components; multiple independent chains are allowed.

    Connections have explicit constant second/third operands. Fanout and joins
    require dedicated future components; accidental multiple drivers are rejected.
    """
    def __init__(self):
        self.units = {}; self.connections = []; self.sources = {}; self.sinks = {}
        self.cycle = 0

    def add(self, name, unit):
        if name in self.units: raise ValueError(f"duplicate node {name}")
        self.units[name] = unit
        return self

    def connect(self, source, destination, *, b=0, c=0, rounding=0):
        if source not in self.units or destination not in self.units: raise KeyError("unknown node")
        if any(e.source == source or e.destination == destination for e in self.connections):
            raise ValueError("multiple drivers or fanout require explicit stream components")
        self.connections.append(Connection(source,destination,b,c,rounding))
        try: self._order()
        except ValueError: self.connections.pop(); raise
        return self

    def source(self, node, requests):
        if node not in self.units: raise KeyError(node)
        if any(e.destination == node for e in self.connections): raise ValueError("node already connected")
        self.sources[node] = InputSource(list(requests))
        return self

    def sink(self, node):
        if node not in self.units: raise KeyError(node)
        if any(e.source == node for e in self.connections): raise ValueError("node already connected")
        self.sinks[node] = OutputSink()
        return self

    def _order(self):
        pending, order = set(self.units), []
        while pending:
            ready = sorted(n for n in pending if not any(e.destination == n and e.source in pending for e in self.connections))
            if not ready: raise ValueError("network contains a cyclic dependency")
            order += ready; pending.difference_update(ready)
        return order

    def step(self, *, ready=True, reset=False, flush=False):
        order = self._order()
        outgoing = {e.source:e for e in self.connections}
        incoming = {e.destination:e for e in self.connections}
        observed, actual_inputs = {}, {}
        for name in reversed(order):
            sink_ready = ready.get(name,True) if isinstance(ready,dict) else ready
            out_ready = observed[outgoing[name].destination].in_ready if name in outgoing else sink_ready
            observed[name] = self.units[name].eval(Inputs(out_ready=out_ready,reset=reset,flush=flush))
        for name in order:
            req = self.sources[name].peek() if name in self.sources else None
            if name in incoming:
                e = incoming[name]; upstream = observed[e.source]
                if upstream.out_valid:
                    r = upstream.response
                    req = Request(r.bits,int(r.flags),r.remainder,tag=r.tag) if getattr(self.units[name],"transport",False) else Request(r.bits,e.b,e.c,e.rounding,r.tag)
            sink_ready = ready.get(name,True) if isinstance(ready,dict) else ready
            out_ready = observed[outgoing[name].destination].in_ready if name in outgoing else sink_ready
            actual_inputs[name] = Inputs(req,out_ready,reset,flush)
        # No state is committed until every component has observed old state.
        outputs = {name:self.units[name].eval(actual_inputs[name]) for name in order}
        for name in order:
            self.units[name].tick()
            if name in self.sources and outputs[name].accepted: self.sources[name].position += 1
            if name in self.sinks and outputs[name].delivered: self.sinks[name].received.append((self.cycle,outputs[name].response))
        self.cycle += 1
        return outputs

    def run(self, cycles, *, ready=True, reset=None, flush=None, backend="python", trace=False):
        if backend not in ("python","numba"): raise ValueError("network backend must be python or numba")
        if backend == "numba":
            from .fast_network import run_network
            return run_network(self,cycles,ready=ready,reset=reset,flush=flush,trace=trace)
        result = []
        for i in range(cycles):
            r = ready if isinstance(ready,(bool,dict)) else ready[i]
            output = self.step(ready=r,reset=bool(reset[i]) if reset is not None else False,flush=bool(flush[i]) if flush is not None else False)
            if trace: result.append(output)
        return result if trace else {n:s.received for n,s in self.sinks.items()}
