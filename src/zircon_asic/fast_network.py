"""Preallocated Numba cycle scheduler for built-in single-stream networks."""
import numpy as np
from numba import njit
from .fast import _float_one, _int_one
from .network import FIFO
from .types import Response, Outputs, Flags


@njit(cache=True)
def _run(cfg, downstream, upstream, operands, source, lengths, position, sink,
         valid, data, phase, heads, counts, stats, ready_pattern, resets, flushes, tracing):
    cycles, nodes = ready_pattern.shape
    trace = np.zeros((cycles if tracing else 0,nodes,10),np.uint64)
    events = np.zeros((cycles*int(np.sum(sink)),6),np.uint64)
    nevents = 0
    outvalid, inready, outready = np.zeros(nodes,np.bool_),np.zeros(nodes,np.bool_),np.zeros(nodes,np.bool_)
    outdata = np.zeros((nodes,4),np.uint64)
    req = np.zeros((nodes,5),np.uint64)
    inv = np.zeros(nodes,np.bool_)
    x,y,z = np.zeros(20,np.uint64),np.zeros(20,np.uint64),np.zeros(20,np.uint64)
    for cycle in range(cycles):
        cancel = resets[cycle] or flushes[cycle]
        for j in range(nodes-1,-1,-1):
            kind,lat,cap = cfg[j,0],cfg[j,1],cfg[j,2]
            outready[j] = inready[downstream[j]] if downstream[j] >= 0 else ready_pattern[cycle,j]
            if kind == 2:
                outvalid[j] = counts[j] > 0
                inready[j] = counts[j] < cap or outready[j]
                index = heads[j]
            elif kind == 1:
                outvalid[j] = phase[j] == lat
                inready[j] = phase[j] == 0 or (outvalid[j] and outready[j])
                index = 0
            else:
                outvalid[j] = valid[j,lat-1]
                can = outready[j]
                for i in range(lat-1,-1,-1): can = not valid[j,i] or can
                inready[j] = can
                index = lat-1
            for field in range(4): outdata[j,field] = data[j,index,field]
            outvalid[j] &= not cancel
            inready[j] &= not cancel
        for j in range(nodes):
            parent = upstream[j]
            if parent >= 0:
                inv[j] = outvalid[parent]
                req[j,0] = outdata[parent,0]; req[j,4] = outdata[parent,2]
                req[j,1] = outdata[parent,1] if cfg[j,3] == -1 else operands[j,0]
                req[j,2] = outdata[parent,3] if cfg[j,3] == -1 else operands[j,1]
                req[j,3] = operands[j,2]
            else:
                inv[j] = position[j] < lengths[j]
                if inv[j]:
                    for field in range(5): req[j,field] = source[j,position[j],field]
        for j in range(nodes):
            kind,lat,cap = cfg[j,0],cfg[j,1],cfg[j,2]
            occupied,stagebits = 0,np.uint64(0)
            for i in range(cap):
                v = (i < counts[j]) if kind == 2 else (phase[j] > 0 if kind == 1 else valid[j,i])
                occupied += int(v)
                if v: stagebits |= np.uint64(1) << np.uint64(i)
            if kind == 1: stagebits = (np.uint64(1) << np.uint64(phase[j]-1)) if phase[j] else np.uint64(0)
            accepted,delivered = inv[j] and inready[j],outvalid[j] and outready[j]
            if tracing:
                trace[cycle,j,0] = inready[j]; trace[cycle,j,1] = outvalid[j]
                if outvalid[j]:
                    for field in range(4): trace[cycle,j,field+2] = outdata[j,field]
                trace[cycle,j,6] = stagebits; trace[cycle,j,7] = occupied
                trace[cycle,j,8] = phase[j] if kind == 1 else 0
                trace[cycle,j,9] = np.uint64(accepted) | (np.uint64(delivered) << np.uint64(1))
            stats[j,0] += 1; stats[j,6] += occupied
            stats[j,4] += int(inv[j] and not inready[j] and not cancel)
            stats[j,5] += int(outvalid[j] and not outready[j])
            if cancel:
                stats[j,3] += occupied
                for i in range(cap): valid[j,i] = False
                phase[j],counts[j],heads[j] = 0,0,0
                continue
            if delivered and sink[j]:
                events[nevents,0] = cycle; events[nevents,1] = j
                for field in range(4): events[nevents,field+2] = outdata[j,field]
                nevents += 1
            stats[j,1] += int(accepted); stats[j,2] += int(delivered)
            bits,flags,rem = np.uint64(0),0,np.uint64(0)
            if accepted:
                width,eb,fb,bias,encoding,signed,op = cfg[j,3:10]
                if width == -1:
                    bits,flags,rem = req[j,0],np.int64(req[j,1]),req[j,2]
                elif eb:
                    bits,flags = _float_one(req[j,0],req[j,1],req[j,2],np.int64(req[j,3]),op,width,eb,fb,bias,encoding,x,y,z)
                else:
                    bits,flags,rem = _int_one(req[j,0],req[j,1],op,width,signed != 0)
                if upstream[j] < 0: position[j] += 1
            if kind == 0:
                can = outready[j]
                for i in range(lat-1,-1,-1):
                    can = not valid[j,i] or can
                    if can:
                        valid[j,i] = accepted if i == 0 else valid[j,i-1]
                        if i == 0 and accepted:
                            data[j,0,0],data[j,0,1],data[j,0,2],data[j,0,3] = bits,flags,req[j,4],rem
                        elif i > 0 and valid[j,i]:
                            for field in range(4): data[j,i,field] = data[j,i-1,field]
            elif kind == 1:
                if accepted:
                    phase[j] = 1
                    data[j,0,0],data[j,0,1],data[j,0,2],data[j,0,3] = bits,flags,req[j,4],rem
                elif delivered: phase[j] = 0
                elif 0 < phase[j] < lat: phase[j] += 1
            else:
                tail = (heads[j]+counts[j])%cap
                if delivered:
                    heads[j] = (heads[j]+1)%cap; counts[j] -= 1
                if accepted:
                    data[j,tail,0],data[j,tail,1],data[j,tail,2],data[j,tail,3] = bits,flags,req[j,4],rem
                    counts[j] += 1
    return trace,events[:nevents]


def run_network(net,cycles,*,ready=True,reset=None,flush=None,trace=False):
    names = net._order(); n = len(names); lookup = {name:i for i,name in enumerate(names)}
    cfg = np.zeros((n,10),np.int64)
    upstream,downstream = np.full(n,-1,np.int64),np.full(n,-1,np.int64)
    operands = np.zeros((n,3),np.uint64)
    for e in net.connections:
        a,b = lookup[e.source],lookup[e.destination]
        downstream[a],upstream[b] = b,a; operands[b] = e.b,e.c,e.rounding
    maxcap = max((u.depth if isinstance(u,FIFO) else u.timing.capacity for u in net.units.values()),default=1)
    valid,data = np.zeros((n,maxcap),np.bool_),np.zeros((n,maxcap,4),np.uint64)
    phase,heads,counts = np.zeros(n,np.int64),np.zeros(n,np.int64),np.zeros(n,np.int64)
    lengths,position = np.zeros(n,np.int64),np.zeros(n,np.int64)
    source = np.zeros((n,max((len(s.requests) for s in net.sources.values()),default=0),5),np.uint64)
    statfields = ("cycles","accepted","delivered","cancelled","input_stalls","output_stalls","occupancy_sum")
    stats = np.zeros((n,7),np.int64)
    for j,name in enumerate(names):
        u = net.units[name]
        if u._pending is not None: raise RuntimeError("cannot compile a network with an uncommitted eval")
        kind = 2 if isinstance(u,FIFO) else int(u.timing.kind == "iterative")
        cap = u.depth if kind == 2 else u.timing.capacity
        cfg[j,:3] = kind,u.timing.latency,cap
        if getattr(u,"transport",False): cfg[j,3] = -1
        elif hasattr(u,"format"):
            f = u.format
            cfg[j,3:9] = f.width,f.exponent,f.fraction,f.bias,{"ieee":0,"finite_nan":1,"finite":2}[f.encoding],0
        else: cfg[j,3],cfg[j,8] = u.width,u.signed
        if not getattr(u,"transport",False): cfg[j,9] = {"add":0,"mul":1,"fma":2,"div":3}[u.op]
        slots = list(u._queue) if kind == 2 else u._slots
        for i,r in enumerate(slots):
            if r is not None: valid[j,i] = True; data[j,i] = r.bits,r.flags,r.tag,r.remainder
        counts[j] = len(slots) if kind == 2 else 0
        phase[j] = u._phase
        stats[j] = [getattr(u.stats,f) for f in statfields]
        if name in net.sources:
            s = net.sources[name]; lengths[j] = len(s.requests); position[j] = s.position
            for i,r in enumerate(s.requests): source[j,i] = r.a,r.b,r.c,r.rounding,r.tag
    if isinstance(ready,dict): pattern = np.broadcast_to([ready.get(name,True) for name in names],(cycles,n)).copy()
    else:
        pattern = np.asarray(ready,dtype=np.bool_)
        if pattern.ndim == 1: pattern = pattern[:,None]
        pattern = np.broadcast_to(pattern,(cycles,n)).copy()
    resets = np.zeros(cycles,np.bool_) if reset is None else np.asarray(reset,dtype=np.bool_)
    flushes = np.zeros(cycles,np.bool_) if flush is None else np.asarray(flush,dtype=np.bool_)
    if resets.shape != (cycles,) or flushes.shape != (cycles,): raise ValueError("reset/flush must have one entry per cycle")
    sink = np.array([name in net.sinks for name in names],np.bool_)
    traces,events = _run(cfg,downstream,upstream,operands,source,lengths,position,sink,valid,data,phase,heads,counts,stats,pattern,resets,flushes,trace)
    for row in events:
        cycle,j,bits,flags,tag,rem = map(int,row)
        net.sinks[names[j]].received.append((net.cycle+cycle,Response(bits,Flags(flags),tag,rem)))
    for j,name in enumerate(names):
        u = net.units[name]
        for field,v in zip(statfields,stats[j]): setattr(u.stats,field,int(v))
        u.cycle += cycles; u._phase = int(phase[j])
        if isinstance(u,FIFO):
            u._queue.clear()
            for i in range(int(counts[j])):
                bits,flags,tag,rem = map(int,data[j,(heads[j]+i)%u.depth]); u._queue.append(Response(bits,Flags(flags),tag,rem))
        else:
            for i in range(u.timing.capacity):
                present = phase[j] > 0 if cfg[j,0] == 1 else valid[j,i]
                bits,flags,tag,rem = map(int,data[j,i]); u._slots[i] = Response(bits,Flags(flags),tag,rem) if present else None
        if name in net.sources: net.sources[name].position = int(position[j])
    net.cycle += cycles
    if not trace: return {name:s.received for name,s in net.sinks.items()}
    result = []
    for row in traces:
        outputs = {}
        for j,name in enumerate(names):
            ir,ov,bits,flags,tag,rem,sv,occ,ph,handshakes = map(int,row[j]); u = net.units[name]
            count = u.depth if isinstance(u,FIFO) else u.timing.latency
            phase_name = "fifo" if isinstance(u,FIFO) else "pipeline"
            iteration = 0
            if u.timing.kind == "iterative":
                phase_name = "idle" if ph == 0 else "response" if ph == u.timing.latency else u.timing.phases[ph]
                iteration = u.timing.phases[:ph].count("iterate")
            outputs[name] = Outputs(bool(ir),bool(ov),Response(bits,Flags(flags),tag,rem) if ov else None,
                                    bool(handshakes&1),bool(handshakes&2),occ,tuple(bool(sv & (1 << i)) for i in range(count)),phase_name,iteration)
        result.append(outputs)
    return result
