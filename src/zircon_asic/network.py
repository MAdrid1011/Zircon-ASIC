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
    source_port: int = 0
    destination_port: int = 0
    mapping: dict | None = None


@dataclass(frozen=True)
class Field:
    """Serializable response-field reference in a connection mapping."""
    name: str


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
        if any(u is unit for u in self.units.values()): raise ValueError("a state owner may only be added once; use its ports")
        self.units[name] = unit
        return self

    def connect(self, source, destination, *, b=0, c=0, rounding=0, source_port=0, destination_port=0, mapping=None):
        if source not in self.units or destination not in self.units: raise KeyError("unknown node")
        self._port(source,source_port); self._port(destination,destination_port)
        if any((e.source,e.source_port) == (source,source_port) or (e.destination,e.destination_port) == (destination,destination_port) for e in self.connections):
            raise ValueError("multiple drivers or fanout require explicit stream components")
        if self._key(source,source_port) in self.sinks or self._key(destination,destination_port) in self.sources:
            raise ValueError("port already attached to a source or sink")
        if mapping is not None:
            from .spm import SPM
            fields = {"address","write","data","mask","tag"} if isinstance(self.units[destination],SPM) else {"a","b","c","rounding","tag"}
            if not isinstance(mapping,dict) or set(mapping)-fields: raise ValueError("unknown request mapping field")
            for v in mapping.values():
                if isinstance(v,Field):
                    from .spm import MemoryResponse
                    schema = MemoryResponse if isinstance(self.units[source],SPM) else Response
                    if v.name not in schema.__dataclass_fields__: raise ValueError("unknown response field")
                elif not isinstance(v,(int,bool)): raise TypeError("mapping supports integer constants and Field references")
        self.connections.append(Connection(source,destination,b,c,rounding,source_port,destination_port,mapping))
        try: self._order()
        except ValueError: self.connections.pop(); raise
        return self

    @staticmethod
    def _key(node,port=0):
        return node if port == 0 else (node,port)

    def _port(self,node,port):
        if node not in self.units: raise KeyError(node)
        if not isinstance(port,int) or not 0 <= port < getattr(self.units[node],"ports",1): raise ValueError("invalid port")

    def source(self, node, requests, *, port=0):
        if node not in self.units: raise KeyError(node)
        self._port(node,port)
        if any((e.destination,e.destination_port) == (node,port) for e in self.connections): raise ValueError("port already connected")
        self.sources[self._key(node,port)] = InputSource(list(requests))
        return self

    def sink(self, node, *, port=0):
        if node not in self.units: raise KeyError(node)
        self._port(node,port)
        if any((e.source,e.source_port) == (node,port) for e in self.connections): raise ValueError("port already connected")
        self.sinks[self._key(node,port)] = OutputSink()
        return self

    def _order(self):
        pending, order = set(self.units), []
        while pending:
            ready = sorted(n for n in pending if not any(e.destination == n and e.source in pending for e in self.connections))
            if not ready: raise ValueError("network contains a cyclic dependency")
            order += ready; pending.difference_update(ready)
        return order

    def step(self, *, ready=True, reset=False, flush=False):
        pending={name:u._pending for name,u in self.units.items()}
        try:
            order,outputs=self._evaluate(ready=ready,reset=reset,flush=flush)
        except Exception:
            for name,value in pending.items():self.units[name]._pending=value
            raise
        for name in order:
            self.units[name].tick()
        for key,o in outputs.items():
            if key in self.sources and o.accepted:self.sources[key].position+=1
            if key in self.sinks and o.delivered:self.sinks[key].received.append((self.cycle,o.response))
        self.cycle+=1
        return outputs

    def _evaluate(self, *, ready=True, reset=False, flush=False):
        from .spm import SPM, MemoryRequest, MemoryResponse, MemoryStatus
        order = self._order()
        outgoing = {self._key(e.source,e.source_port):e for e in self.connections}
        incoming = {self._key(e.destination,e.destination_port):e for e in self.connections}
        snapshots, requests, outputs = {}, {}, {}
        # Payloads of supported modules depend only on old registered state.
        for name in order:
            u=self.units[name]; ports=getattr(u,"ports",1)
            empty=tuple(Inputs(reset=reset,flush=flush) for _ in range(ports))
            obs=u.eval(empty if ports>1 else empty[0])
            for p,o in enumerate(obs if ports>1 else (obs,)): snapshots[self._key(name,p)]=o
        for name in order:
            u=self.units[name]
            for p in range(getattr(u,"ports",1)):
                key=self._key(name,p)
                req=self.sources[key].peek() if key in self.sources else None
                if key in incoming:
                    e=incoming[key]; o=snapshots[self._key(e.source,e.source_port)]
                    if o.out_valid:
                        r=o.response
                        if isinstance(r,MemoryResponse) and (r.write or r.status != MemoryStatus.OK):
                            raise ValueError("only successful memory read responses may feed a data connection")
                        if e.mapping is not None:
                            args={k:getattr(r,v.name) if isinstance(v,Field) else v for k,v in e.mapping.items()}
                            req=MemoryRequest(**args) if isinstance(u,SPM) else Request(**args)
                        elif isinstance(u,SPM): raise ValueError("memory destination requires explicit request mapping")
                        elif getattr(u,"transport",False): req=Request(r.bits,int(getattr(r,"flags",0)),getattr(r,"remainder",0),tag=r.tag)
                        else:
                            if isinstance(r,MemoryResponse):
                                width=u.format.width if hasattr(u,"format") else u.width
                                if r.bits >= 1<<width: raise ValueError("memory response exceeds arithmetic operand width")
                            req=Request(r.bits,e.b,e.c,e.rounding,r.tag)
                requests[key]=req
        for name in reversed(order):
            u=self.units[name]; ins=[]
            for p in range(getattr(u,"ports",1)):
                key=self._key(name,p)
                rdy=ready.get(key,True) if isinstance(ready,dict) else ready
                if key in outgoing:
                    e=outgoing[key]; rdy=outputs[self._key(e.destination,e.destination_port)].in_ready
                ins.append(Inputs(requests[key],rdy,reset,flush))
            obs=u.eval(tuple(ins) if len(ins)>1 else ins[0])
            for p,o in enumerate(obs if len(ins)>1 else (obs,)): outputs[self._key(name,p)]=o
        # Validate memory diagnostics before any owner commits.
        for name in order:
            u=self.units[name]
            if isinstance(u,SPM):
                for p in range(u.ports):
                    key=self._key(name,p)
                    if outputs[key].accepted: u._check_read(requests[key])
        return order,outputs

    def run(self, cycles, *, ready=True, reset=None, flush=None, backend="python", trace=False):
        if backend not in ("python","numba"): raise ValueError("network backend must be python or numba")
        if backend == "numba":
            from .fast_network import run_network
            return run_network(self,cycles,ready=ready,reset=reset,flush=flush,trace=trace)
        result = []
        for i in range(cycles):
            r = ready if isinstance(ready,(bool,dict)) else ready[i]
            if hasattr(r,"ndim") and r.ndim == 1:
                keys=[self._key(name,p) for name in self._order() for p in range(getattr(self.units[name],"ports",1))]
                if len(r)!=len(keys): raise ValueError("one ready value per endpoint required")
                r=dict(zip(keys,map(bool,r)))
            output = self.step(ready=r,reset=bool(reset[i]) if reset is not None else False,flush=bool(flush[i]) if flush is not None else False)
            if trace: result.append(output)
        return result if trace else {n:s.received for n,s in self.sinks.items()}
