"""Byte-addressed scratchpad with atomic, multi-port cycle evaluation."""
from collections import deque
from dataclasses import asdict, dataclass
from enum import IntEnum
from importlib.resources import files
from typing import Protocol, runtime_checkable
import hashlib
import json
import operator
import numpy as np
from .types import Inputs, Outputs, Statistics


@runtime_checkable
class CycleModule(Protocol):
    """Structural protocol; implementations own their complete cycle state."""
    def compute(self, request): ...
    def compute_batch(self, *args, **kwargs): ...
    def eval(self, inputs=Inputs()): ...
    def tick(self): ...
    def step(self, inputs=Inputs()): ...
    def reset(self): ...
    def flush(self): ...
    def describe(self) -> dict: ...


class MemoryStatus(IntEnum):
    OK = 0
    OUT_OF_RANGE = 1
    MISALIGNED = 2


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    address: int
    write: bool = False
    data: int = 0
    mask: int | None = None
    tag: int = 0

    def __post_init__(self):
        for name in ("address", "data", "tag"):
            value = operator.index(getattr(self, name))
            if value < 0: raise ValueError(f"{name} must be unsigned")
        if self.address >= 1 << 32 or self.tag >= 1 << 32:
            raise ValueError("address and tag must fit 32 bits")
        if self.mask is not None and operator.index(self.mask) < 0:
            raise ValueError("mask must be unsigned")
        if self.write not in (False, True): raise ValueError("write must be boolean")


@dataclass(frozen=True, slots=True)
class MemoryResponse:
    bits: int
    tag: int
    write: bool
    status: MemoryStatus = MemoryStatus.OK


@dataclass(frozen=True, slots=True)
class MemoryBatchResponse:
    bits: np.ndarray
    tags: np.ndarray
    write: np.ndarray
    status: np.ndarray


class UninitializedReadError(RuntimeError):
    pass


def spm_contract():
    return json.loads(files("zircon_asic").joinpath("data/spm.json").read_text())


def spm_contract_hash():
    return hashlib.sha256(json.dumps(spm_contract(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spm_implementation_hash():
    root=files("zircon_asic");digest=hashlib.sha256()
    for name in ("spm.py","types.py","network.py","fast_mixed.py","fast_spm.py","fast.py","units.py","numeric.py","formats.py","lookup.py","contract.py"):
        digest.update(name.encode());digest.update(root.joinpath(name).read_bytes())
    digest.update(spm_contract_hash().encode())
    return digest.hexdigest()


class SPM:
    """Single-RW banked SRAM. eval observes old state; tick commits all ports."""
    def __init__(self, capacity_bytes=4096, data_width=32, banks=1, ports=1):
        for value in (capacity_bytes, data_width, banks, ports): operator.index(value)
        c = spm_contract()
        if data_width not in c["data_widths"] or banks not in c["banks"] or not 1 <= ports <= c["max_ports"]:
            raise ValueError("unsupported width, bank count or port count")
        if not 0 < capacity_bytes <= 1 << 32 or capacity_bytes % (banks * (data_width // 8)):
            raise ValueError("capacity must contain equal whole-word banks and fit the address space")
        self.capacity_bytes, self.data_width = int(capacity_bytes), int(data_width)
        self.banks, self.ports = int(banks), int(ports)
        self.word_bytes = data_width // 8
        self.memory = np.zeros(capacity_bytes, np.uint8)
        self.initialized = np.zeros(capacity_bytes, np.bool_)
        self.events = None
        self.reset()

    def _clear(self):
        self._flight = [None] * self.ports
        self._queues = [deque() for _ in range(self.ports)]
        self._rr = [0] * self.banks
        self._pending = None

    def reset(self):
        self._clear()
        self.cycle = 0
        self.stats = Statistics()
        self.port_stats = [Statistics() for _ in range(self.ports)]
        self.bank_accesses = [0] * self.banks
        self.bank_conflicts = [0] * self.ports
        self.credit_stalls = [0] * self.ports
        self.reads = self.writes = 0

    def flush(self):
        for p in range(self.ports):
            n = len(self._queues[p]) + int(self._flight[p] is not None)
            self.port_stats[p].cancelled += n
            self.stats.cancelled += n
        self._clear()

    def _idle(self):
        if self._pending is not None or any(self._flight) or any(self._queues):
            raise RuntimeError("functional/image access requires an idle SPM without pending eval")

    def load_image(self, data, address=0):
        self._idle()
        address = operator.index(address)
        raw = np.frombuffer(bytes(data), dtype=np.uint8)
        if address < 0 or address + len(raw) > self.capacity_bytes: raise ValueError("image out of range")
        self.memory[address:address+len(raw)] = raw
        self.initialized[address:address+len(raw)] = True

    def dump_image(self):
        """Return backing bytes; consult initialized for unspecified byte locations."""
        self._idle()
        return self.memory.tobytes()

    def _validate(self, r):
        if not isinstance(r, MemoryRequest): raise TypeError("SPM requires MemoryRequest")
        if r.data >= 1 << self.data_width: raise ValueError("data exceeds word width")
        if r.mask is not None and r.mask >= 1 << self.word_bytes: raise ValueError("mask exceeds byte lanes")

    def _status(self, r):
        if r.address + self.word_bytes > self.capacity_bytes: return MemoryStatus.OUT_OF_RANGE
        if r.address % self.word_bytes: return MemoryStatus.MISALIGNED
        return MemoryStatus.OK

    def _check_read(self, r):
        if not r.write and self._status(r) == MemoryStatus.OK and not self.initialized[r.address:r.address+self.word_bytes].all():
            raise UninitializedReadError(f"read of uninitialized bytes at 0x{r.address:x}")

    def _execute(self, r):
        status = self._status(r)
        bits = 0
        if status == MemoryStatus.OK:
            if r.write:
                mask = (1 << self.word_bytes) - 1 if r.mask is None else r.mask
                for i in range(self.word_bytes):
                    if mask >> i & 1:
                        self.memory[r.address+i] = r.data >> (8*i) & 255
                        self.initialized[r.address+i] = True
            else:
                self._check_read(r)
                bits = int.from_bytes(self.memory[r.address:r.address+self.word_bytes].tobytes(), "little")
        return MemoryResponse(bits, r.tag, bool(r.write), status)

    def compute(self, request):
        self._idle(); self._validate(request)
        return self._execute(request)

    def compute_batch(self, addresses, *, write=False, data=0, masks=None, tags=0, backend="auto"):
        self._idle()
        if backend not in ("auto", "python", "numba"): raise ValueError("invalid backend")
        arrays = np.broadcast_arrays(addresses, write, data, (1 << self.word_bytes)-1 if masks is None else masks, tags)
        shape = arrays[0].shape
        reqs = [MemoryRequest(operator.index(a), operator.index(w), operator.index(d), operator.index(m), operator.index(t))
                for a,w,d,m,t in zip(*(x.flat for x in arrays))]
        for r in reqs: self._validate(r)
        if backend != "python":
            try:
                from .fast_spm import batch
            except ImportError:
                if backend == "numba": raise
            else:
                return batch(self, reqs, shape)
        responses = [self._execute(r) for r in reqs]
        return MemoryBatchResponse(*(np.array([getattr(r,k) for r in responses],dtype=d).reshape(shape)
            for k,d in (("bits",np.uint64),("tag",np.uint32),("write",np.bool_),("status",np.uint8))))

    def _inputs(self, inputs):
        if isinstance(inputs, Inputs):
            if self.ports != 1: raise ValueError("provide one Inputs per SPM port")
            inputs = (inputs,)
        else: inputs = tuple(inputs)
        if len(inputs) != self.ports or not all(isinstance(i,Inputs) for i in inputs): raise ValueError("one Inputs per port required")
        if len({(i.reset,i.flush) for i in inputs}) != 1: raise ValueError("reset/flush must match on all ports")
        for i in inputs:
            if i.request is not None: self._validate(i.request)
        return inputs

    def eval(self, inputs=Inputs()):
        ins = self._inputs(inputs)
        cancel = ins[0].reset or ins[0].flush
        credit, ready, targets = [], [False]*self.ports, [-1]*self.ports
        for p,i in enumerate(ins):
            occupied = len(self._queues[p]) + int(self._flight[p] is not None)
            credit.append(occupied < 2 or (bool(self._queues[p]) and i.out_ready))
            if i.request is not None and not cancel and credit[p]:
                if self._status(i.request) != MemoryStatus.OK: ready[p] = True
                else: targets[p] = (i.request.address // self.word_bytes) % self.banks
        grants = [-1]*self.banks
        for b in range(self.banks):
            for n in range(self.ports):
                p = (self._rr[b]+n) % self.ports
                if targets[p] == b:
                    grants[b] = p; ready[p] = True; break
        outputs = []
        for p,i in enumerate(ins):
            q = self._queues[p]
            r = q[0] if q and not cancel else None
            valid = (self._flight[p] is not None, len(q)>0, len(q)>1)
            outputs.append(Outputs(ready[p],r is not None,r,ready[p] and i.request is not None,
                r is not None and i.out_ready,sum(valid),valid,"spm"))
        self._pending = (ins,tuple(outputs),tuple(grants),tuple(credit))
        return outputs[0] if self.ports == 1 else tuple(outputs)

    def tick(self):
        if self._pending is None: raise RuntimeError("tick requires eval")
        ins, outs, grants, credit = self._pending
        cancel = ins[0].reset or ins[0].flush
        # Diagnose before any mutation, so an invalid read cannot partially commit a cycle.
        if not cancel:
            for i,o in zip(ins,outs):
                if o.accepted: self._check_read(i.request)
        self._pending = None
        debug = self.debug_state()
        for p,(i,o) in enumerate(zip(ins,outs)):
            s = self.port_stats[p]
            s.cycles += 1; s.occupancy_sum += o.occupancy
            if cancel: s.cancelled += o.occupancy
            else:
                s.accepted += int(o.accepted); s.delivered += int(o.delivered)
                s.input_stalls += int(i.request is not None and not o.in_ready)
                s.output_stalls += int(o.out_valid and not i.out_ready)
                if i.request is not None and not o.accepted:
                    if not credit[p]: self.credit_stalls[p] += 1
                    else: self.bank_conflicts[p] += 1
        if cancel: self._clear()
        else:
            for p,(i,o) in enumerate(zip(ins,outs)):
                if o.delivered: self._queues[p].popleft()
                if self._flight[p] is not None: self._queues[p].append(self._flight[p])
                self._flight[p] = self._execute(i.request) if o.accepted else None
                if o.accepted:
                    self.writes += int(i.request.write); self.reads += int(not i.request.write)
                assert len(self._queues[p])+int(self._flight[p] is not None) <= 2
            for b,p in enumerate(grants):
                if p >= 0:
                    self._rr[b] = (p+1) % self.ports; self.bank_accesses[b] += 1
        self.stats.cycles += 1
        for f in ("accepted","delivered","cancelled","input_stalls","output_stalls","occupancy_sum"):
            setattr(self.stats,f,sum(getattr(s,f) for s in self.port_stats))
        if self.events is not None:
            self.events.append(dict(cycle=self.cycle,inputs=[asdict(i) for i in ins],outputs=[asdict(o) for o in outs],grants=grants,state=debug))
        self.cycle += 1
        return outs[0] if self.ports == 1 else outs

    def step(self, inputs=Inputs()):
        self.eval(inputs)
        return self.tick()

    def run(self, schedule, *, backend="python", trace=False):
        """Replay explicit per-cycle Inputs, including bubbles and held requests."""
        if backend not in ("python","numba"): raise ValueError("invalid backend")
        if backend == "python":
            result=[]
            for inputs in schedule:
                cycle=self.cycle; out=self.step(inputs)
                if trace: result.append(out)
                else:
                    for p,o in enumerate((out,) if self.ports==1 else out):
                        if o.delivered: result.append((cycle,p,o.response))
            return result
        from .network import Network
        from .fast_mixed import run_network
        ins=[self._inputs(i) for i in schedule];n=len(ins)
        stimulus=np.zeros((n,self.ports,6),np.uint64);ready=np.zeros((n,self.ports),bool)
        for k,inputs in enumerate(ins):
            for p,i in enumerate(inputs):
                ready[k,p]=i.out_ready
                if i.request is not None:
                    r=i.request
                    stimulus[k,p]=1,r.address,r.write,r.data,(1<<self.word_bytes)-1 if r.mask is None else r.mask,r.tag
        net=Network().add("spm",self);net.cycle=self.cycle
        for p in range(self.ports):net.sink("spm",port=p)
        result=run_network(net,n,ready=ready,reset=np.array([i[0].reset for i in ins]),
            flush=np.array([i[0].flush for i in ins]),trace=trace,_stimulus=stimulus)
        if trace:
            return [row['spm'] if self.ports==1 else tuple(row[net._key('spm',p)] for p in range(self.ports)) for row in result]
        return sorted((cycle,p,r) for p in range(self.ports) for cycle,r in result[net._key('spm',p)])

    def debug_state(self):
        return dict(rr=tuple(self._rr),flight=tuple(r is not None for r in self._flight),
                    queue_counts=tuple(map(len,self._queues)),
                    outstanding=tuple(len(q)+int(r is not None) for q,r in zip(self._queues,self._flight)))

    def describe(self):
        evidence={"status":"unqualified","reason":"no matching configuration evidence"}
        path=files("zircon_asic").joinpath("data/spm_qualification.json")
        if path.is_file():
            report=json.loads(path.read_text())
            if report.get("implementation_hash")==spm_implementation_hash() and report.get("contract_hash")==spm_contract_hash():
                key=f"{self.capacity_bytes}:{self.data_width}:{self.banks}:{self.ports}"
                evidence=report.get("configurations",{}).get(key,evidence)
        return dict(capacity_bytes=self.capacity_bytes,data_width=self.data_width,banks=self.banks,ports=self.ports,
                    latency=2,initiation_interval=1,capacity=2*self.ports,credits_per_port=2,
                    response_depth=2,contract_hash=spm_contract_hash(),cycle_profile="spm-v1",
                    storage_backend="byte_array",qualification=evidence["status"],evidence=evidence)
