"""Compiled atomic scheduler for arithmetic and multi-port scratchpads."""
import numpy as np
from numba import njit
from numba.typed import List
from .fast import _float_one, _int_one
from .unary import OPERATIONS
from .fast_unary import kernel_resources
from .fast_spm import access
from .spm import SPM, MemoryRequest, MemoryResponse, MemoryStatus
from .types import Response, Flags, Outputs
from .network import FIFO, Field


@njit(cache=True)
def _run(cfg, owners, ends, upstream, downstream, mapping, constants, source, lengths, position, sinks,
         valid, data, phase, heads, counts, stats, extra, rr, memories, initialized, ready, resets, flushes, tracing, stimulus, sfu):
    cycles,n=ready.shape
    trace=np.zeros((cycles if tracing else 0,n,10),np.uint64)
    events=np.zeros((cycles*int(np.sum(sinks)),6),np.uint64);nevents=0
    inv=np.zeros(n,np.bool_); ir=np.zeros(n,np.bool_); ov=np.zeros(n,np.bool_); ore=np.zeros(n,np.bool_)
    od=np.zeros((n,4),np.uint64);req=np.zeros((n,5),np.uint64)
    credit=np.zeros(n,np.bool_);target=np.full(n,-1,np.int64)
    x,y,z=np.zeros(20,np.uint64),np.zeros(20,np.uint64),np.zeros(20,np.uint64)
    for k in range(cycles):
        cancel=resets[k] or flushes[k]
        for j in range(n):
            kind,lat,cap=cfg[j,:3]
            index=lat-1 if kind==0 else 0
            if kind==2 or kind==3: index=heads[j]; ov[j]=counts[j]>0
            elif kind==1:ov[j]=phase[j]==lat
            else:ov[j]=valid[j,index]
            ov[j] &= not cancel
            od[j,:]=data[j,index,:]
        for j in range(n):
            parent=upstream[j]
            if parent<0:
                if stimulus.shape[0]>0:
                    inv[j]=stimulus[k,j,0]!=0
                    req[j,:]=stimulus[k,j,1:]
                else:
                    inv[j]=position[j]<lengths[j]
                    if inv[j]:req[j,:]=source[j,position[j],:]
            else:
                inv[j]=ov[parent]
                if inv[j]:
                    if cfg[parent,0]==3 and (od[parent,1]!=0 or od[parent,3]!=0):
                        raise ValueError("only successful memory read responses may feed a data connection")
                    for f in range(5):
                        m=mapping[j,f]
                        req[j,f]=od[parent,m] if m>=0 else constants[j,f]
                    width=cfg[j,3]
                    if cfg[j,0]==3:
                        if req[j,0]>=np.uint64(1)<<np.uint64(32) or req[j,4]>=np.uint64(1)<<np.uint64(32):raise ValueError("memory address/tag exceeds 32 bits")
                        if width<64 and req[j,2]>=np.uint64(1)<<np.uint64(width):raise ValueError("data exceeds memory word")
                        if req[j,3]>=np.uint64(1)<<np.uint64(width//8):raise ValueError("mask exceeds memory lanes")
                    elif cfg[parent,0]==3 and width>0 and width<64 and req[j,0]>=np.uint64(1)<<np.uint64(width):
                        raise ValueError("memory response exceeds arithmetic operand width")
        for g in range(len(owners)-1,-1,-1):
            start,end=owners[g],ends[g]
            for j in range(start,end):ore[j]=ir[downstream[j]] if downstream[j]>=0 else ready[k,j]
            if cfg[start,0]==3:
                for j in range(start,end):
                    credit[j]=counts[j]+int(valid[j,2])<2 or (ov[j] and ore[j])
                    ir[j]=False;target[j]=-1
                    if inv[j] and credit[j] and not cancel:
                        a=req[j,0];w=cfg[j,3]//8
                        if a+np.uint64(w)>np.uint64(cfg[j,5]) or a%np.uint64(w)!=0:ir[j]=True
                        else:target[j]=int(a//np.uint64(w)%np.uint64(cfg[j,4]))
                for b in range(cfg[start,4]):
                    for offset in range(end-start):
                        j=start+(rr[start,b]+offset)%(end-start)
                        if target[j]==b:
                            ir[j]=True;break
            else:
                j=start;kind,lat,cap=cfg[j,:3]
                if kind==2:ir[j]=counts[j]<cap or ore[j]
                elif kind==1:ir[j]=phase[j]==0 or (ov[j] and ore[j])
                else:
                    can=ore[j]
                    for i in range(lat-1,-1,-1):can=not valid[j,i] or can
                    ir[j]=can
                ir[j] &= not cancel
        for j in range(n):
            if inv[j] and cfg[j,9] >= 4:
                width = cfg[j,3]
                for field in range(3):
                    if req[j,field] >= np.uint64(1) << np.uint64(width):
                        raise ValueError("unary operand exceeds format width")
                if req[j,3] > 4 or (cfg[j,9] == 4 and req[j,3] != 0):
                    raise ValueError("unsupported unary rounding mode")
                if req[j,4] >= np.uint64(1) << np.uint64(32):
                    raise ValueError("tag must fit 32 unsigned bits")
        # Preflight reads before any memory mutation in this cycle.
        for j in range(n):
            if cfg[j,0]==3 and inv[j] and ir[j] and req[j,1]==0:
                a=int(req[j,0]);w=cfg[j,3]//8;o=cfg[j,6]
                if a+w<=cfg[j,5] and a%w==0:
                    for b in range(w):
                        if not initialized[o][a+b]:raise ValueError("SPM read of uninitialized bytes")
        for j in range(n):
            kind,lat,cap=cfg[j,:3]
            occupied=0;sv=np.uint64(0)
            if kind==3:
                occupied=counts[j]+int(valid[j,2])
                sv=np.uint64(int(valid[j,2])|int(counts[j]>0)*2|int(counts[j]>1)*4)
            elif kind==2:
                occupied=counts[j];sv=(np.uint64(1)<<np.uint64(counts[j]))-np.uint64(1)
            elif kind==1:
                occupied=int(phase[j]>0)
                if phase[j]>0:sv=np.uint64(1)<<np.uint64(phase[j]-1)
            else:
                for i in range(lat):
                    occupied+=int(valid[j,i])
                    if valid[j,i]:sv|=np.uint64(1)<<np.uint64(i)
            accept=inv[j] and ir[j];deliver=ov[j] and ore[j]
            if tracing:
                trace[k,j,0]=ir[j];trace[k,j,1]=ov[j];trace[k,j,2:6]=od[j,:]
                trace[k,j,6]=sv;trace[k,j,7]=occupied;trace[k,j,8]=phase[j]
                trace[k,j,9]=int(accept)|int(deliver)*2
            stats[j,0]+=1;stats[j,6]+=occupied
            if cancel:
                stats[j,3]+=occupied;valid[j,:]=False;phase[j]=0;heads[j]=0;counts[j]=0
                if kind==3 and cfg[j,6]==j:rr[j,:]=0
                continue
            stats[j,1]+=int(accept);stats[j,2]+=int(deliver)
            stats[j,4]+=int(inv[j] and not ir[j]);stats[j,5]+=int(ov[j] and not ore[j])
            if deliver and sinks[j]:
                events[nevents,0]=k;events[nevents,1]=j;events[nevents,2:6]=od[j,:];nevents+=1
            if accept and upstream[j]<0 and stimulus.shape[0]==0:position[j]+=1
            if kind==3:
                o=cfg[j,6];w=cfg[j,3]//8
                if inv[j] and not accept:
                    if credit[j]:extra[j,0]+=1
                    else:extra[j,1]+=1
                tail=(heads[j]+counts[j])%2
                if deliver:heads[j]=(heads[j]+1)%2;counts[j]-=1
                if valid[j,2]:data[j,tail,:]=data[j,2,:];counts[j]+=1
                valid[j,2]=accept
                if accept:
                    a,wr,d,m,t=req[j,:]
                    bits,status=access(memories[o],initialized[o],w,a,wr,d,m)
                    data[j,2,0]=bits;data[j,2,1]=status;data[j,2,2]=t;data[j,2,3]=wr
                    extra[j,2+int(wr!=0)]+=1
                    if status==0:
                        b=int(a//np.uint64(w)%np.uint64(cfg[j,4]))
                        rr[o,b]=(j-o+1)%(ends[np.searchsorted(owners,o)]-o)
                        extra[o,4+b]+=1
                continue
            bits,flags,rem=np.uint64(0),0,np.uint64(0)
            if accept:
                width,eb,fb,bias,encoding,signed,op=cfg[j,3:10]
                if width==-1:bits,flags,rem=req[j,0],np.int64(req[j,1]),req[j,2]
                elif eb:bits,flags=_float_one(req[j,0],req[j,1],req[j,2],np.int64(req[j,3]),op,width,eb,fb,bias,encoding,x,y,z,sfu)
                else:bits,flags,rem=_int_one(req[j,0],req[j,1],op,width,signed!=0)
            if kind==0:
                can=ore[j]
                for i in range(lat-1,-1,-1):
                    can=not valid[j,i] or can
                    if can:
                        valid[j,i]=accept if i==0 else valid[j,i-1]
                        if i==0 and accept:data[j,0,0],data[j,0,1],data[j,0,2],data[j,0,3]=bits,flags,req[j,4],rem
                        elif i>0 and valid[j,i]:data[j,i,:]=data[j,i-1,:]
            elif kind==1:
                if accept:
                    phase[j]=1;data[j,0,0],data[j,0,1],data[j,0,2],data[j,0,3]=bits,flags,req[j,4],rem
                elif deliver:phase[j]=0
                elif phase[j]>0 and phase[j]<lat:phase[j]+=1
            else:
                tail=(heads[j]+counts[j])%cap
                if deliver:heads[j]=(heads[j]+1)%cap;counts[j]-=1
                if accept:
                    data[j,tail,0],data[j,tail,1],data[j,tail,2],data[j,tail,3]=bits,flags,req[j,4],rem;counts[j]+=1
    return trace,events[:nevents]


def run_network(net,cycles,*,ready=True,reset=None,flush=None,trace=False,_stimulus=None,_raw_trace=False,_compile_only=False):
    names=net._order();keys=[];units=[];owners=[];ends=[]
    for name in names:
        owners.append(len(keys));u=net.units[name]
        for p in range(getattr(u,'ports',1)):keys.append(net._key(name,p));units.append(u)
        ends.append(len(keys))
    n=len(keys);lookup={k:i for i,k in enumerate(keys)}
    cfg=np.zeros((n,10),np.int64);up=np.full(n,-1,np.int64);down=up.copy()
    mapping=np.full((n,5),-1,np.int64);constants=np.zeros((n,5),np.uint64)
    maxcap=max([3]+[u.depth if isinstance(u,FIFO) else u.timing.capacity for u in units if not isinstance(u,SPM)])
    valid=np.zeros((n,maxcap),np.bool_);data=np.zeros((n,maxcap,4),np.uint64)
    phase=np.zeros(n,np.int64);heads=phase.copy();counts=phase.copy();position=phase.copy();lengths=phase.copy()
    stats=np.zeros((n,7),np.int64);extra=np.zeros((n,20),np.int64);rr=np.zeros((n,16),np.int64)
    memories=List();initialized=List()
    sf=('cycles','accepted','delivered','cancelled','input_stalls','output_stalls','occupancy_sum')
    maxlen=max([0]+[len(s.requests)-s.position for s in net.sources.values()])
    source=np.zeros((n,maxlen,5),np.uint64);origpos={}
    for g,name in enumerate(names):
        u=net.units[name];start,end=owners[g],ends[g]
        if u._pending is not None:raise RuntimeError('cannot compile with pending eval')
        if isinstance(u,SPM) and u.events is not None:raise ValueError('use run(trace=True) for compiled traces')
        for j in range(start,end):
            p=j-start;key=keys[j]
            memories.append(u.memory if isinstance(u,SPM) and p==0 else np.empty(0,np.uint8))
            initialized.append(u.initialized if isinstance(u,SPM) and p==0 else np.empty(0,np.bool_))
            if isinstance(u,SPM):
                cfg[j,:7]=3,2,2,u.data_width,u.banks,u.capacity_bytes,start
                stats[j]=[getattr(u.port_stats[p],f) for f in sf]
                extra[j,:4]=u.bank_conflicts[p],u.credit_stalls[p],0,0
                slots=list(u._queues[p]);counts[j]=len(slots)
                for i,r in enumerate(slots):data[j,i]=r.bits,int(r.status),r.tag,r.write
                r=u._flight[p]
                if r is not None:valid[j,2]=True;data[j,2]=r.bits,int(r.status),r.tag,r.write
                if p==0:
                    rr[j,:u.banks]=u._rr;extra[j,4:4+u.banks]=u.bank_accesses;extra[j,2:4]=u.reads,u.writes
            else:
                kind=2 if isinstance(u,FIFO) else int(u.timing.kind=='iterative');cap=u.depth if kind==2 else u.timing.capacity
                cfg[j,:3]=kind,u.timing.latency,cap
                if getattr(u,'transport',False):cfg[j,3]=-1
                elif hasattr(u,'format'):
                    f=u.format;cfg[j,3:9]=f.width,f.exponent,f.fraction,f.bias,{'ieee':0,'finite_nan':1,'finite':2}[f.encoding],0
                else:cfg[j,3],cfg[j,8]=u.width,u.signed
                if not getattr(u,'transport',False):cfg[j,9]=OPERATIONS[u.op]
                slots=list(u._queue) if kind==2 else u._slots
                for i,r in enumerate(slots):
                    if r is not None:valid[j,i]=True;data[j,i]=r.bits,int(r.flags),r.tag,r.remainder
                if kind==2:counts[j]=len(slots)
                phase[j]=u._phase;stats[j]=[getattr(u.stats,f) for f in sf]
            if key in net.sources:
                s=net.sources[key];origpos[key]=s.position;pending=s.requests[s.position:];lengths[j]=len(pending)
                for i,r in enumerate(pending):
                    if isinstance(u,SPM):
                        u._validate(r);source[j,i]=r.address,r.write,r.data,(1<<u.word_bytes)-1 if r.mask is None else r.mask,r.tag
                    else:
                        if getattr(u,"arity",2)==1:
                            from .unary import validate_request
                            validate_request(u.format,u.op,r)
                        source[j,i]=r.a,r.b,r.c,r.rounding,r.tag
    for e in net.connections:
        a,b=lookup[net._key(e.source,e.source_port)],lookup[net._key(e.destination,e.destination_port)]
        up[b]=a;down[a]=b
        if e.mapping is None:
            if cfg[b,0]==3:raise ValueError('memory destination requires explicit request mapping')
            mapping[b,0]=0;mapping[b,4]=2;constants[b,1:4]=e.b,e.c,e.rounding
            if cfg[b,3]==-1 and cfg[a,0]!=3:mapping[b,1]=1;mapping[b,2]=3
        else:
            fields=['address','write','data','mask','tag'] if cfg[b,0]==3 else ['a','b','c','rounding','tag']
            constants[b,3]=(1<<(cfg[b,3]//8))-1 if cfg[b,0]==3 else 0
            response={'bits':0,'status':1,'tag':2,'write':3} if cfg[a,0]==3 else {'bits':0,'flags':1,'tag':2,'remainder':3}
            if fields[0] not in e.mapping:raise ValueError('missing required request mapping field')
            for f,v in e.mapping.items():
                i=fields.index(f)
                if isinstance(v,Field):mapping[b,i]=response[v.name]
                else:constants[b,i]=v
    if isinstance(ready,dict):pattern=np.broadcast_to([ready.get(k,True) for k in keys],(cycles,n)).copy()
    else:
        pattern=np.asarray(ready,dtype=np.bool_)
        if pattern.ndim==1:pattern=pattern[:,None]
        pattern=np.broadcast_to(pattern,(cycles,n)).copy()
    resets=np.zeros(cycles,np.bool_) if reset is None else np.asarray(reset,np.bool_)
    flushes=np.zeros(cycles,np.bool_) if flush is None else np.asarray(flush,np.bool_)
    if resets.shape!=(cycles,) or flushes.shape!=(cycles,):raise ValueError('reset/flush must have one entry per cycle')
    sink=np.array([k in net.sinks for k in keys],np.bool_)
    arguments=(cfg,np.array(owners),np.array(ends),up,down,mapping,constants,source,lengths,position,sink,
        valid,data,phase,heads,counts,stats,extra,rr,memories,initialized,pattern,resets,flushes,trace,
        np.empty((0,0,0),np.uint64) if _stimulus is None else _stimulus, kernel_resources(net.units.values()))
    if _compile_only:
        from numba import typeof
        import time
        start=time.perf_counter();_run.compile(tuple(typeof(v) for v in arguments))
        return time.perf_counter()-start
    traces,events=_run(*arguments)
    def response(j,row):
        b,f,t,r=map(int,row)
        return MemoryResponse(b,t,bool(r),MemoryStatus(f)) if cfg[j,0]==3 else Response(b,Flags(f),t,r)
    for row in events:
        k,j=map(int,row[:2]);net.sinks[keys[j]].received.append((net.cycle+k,response(j,row[2:])))
    for g,name in enumerate(names):
        u=net.units[name];start,end=owners[g],ends[g]
        for j in range(start,end):
            p=j-start
            if isinstance(u,SPM):
                for f,v in zip(sf,stats[j]):setattr(u.port_stats[p],f,int(v))
                u._flight[p]=response(j,data[j,2]) if valid[j,2] else None
                u._queues[p].clear()
                for i in range(counts[j]):u._queues[p].append(response(j,data[j,(heads[j]+i)%2]))
                u.bank_conflicts[p],u.credit_stalls[p]=map(int,extra[j,:2])
            else:
                for f,v in zip(sf,stats[j]):setattr(u.stats,f,int(v))
                u._phase=int(phase[j])
                if isinstance(u,FIFO):
                    u._queue.clear()
                    for i in range(counts[j]):u._queue.append(response(j,data[j,(heads[j]+i)%u.depth]))
                else:
                    for i in range(u.timing.capacity):u._slots[i]=response(j,data[j,i]) if (phase[j]>0 if cfg[j,0]==1 else valid[j,i]) else None
            if keys[j] in net.sources:net.sources[keys[j]].position=origpos[keys[j]]+int(position[j])
        if isinstance(u,SPM):
            u._rr=list(map(int,rr[start,:u.banks]));u.bank_accesses=list(map(int,extra[start,4:4+u.banks]))
            u.reads=int(sum(extra[start:end,2]));u.writes=int(sum(extra[start:end,3]))
            for f in sf:setattr(u.stats,f,int(stats[start,0]) if f=='cycles' else sum(getattr(s,f) for s in u.port_stats))
        u.cycle+=cycles
    net.cycle+=cycles
    if not trace:return {k:s.received for k,s in net.sinks.items()}
    if _raw_trace:return keys,traces
    result=[]
    for row in traces:
        outputs={}
        for j,key in enumerate(keys):
            ir,ov,b,f,t,r,sv,occ,ph,hs=map(int,row[j]);u=units[j]
            length=3 if isinstance(u,SPM) else u.depth if isinstance(u,FIFO) else u.timing.latency
            label='spm' if isinstance(u,SPM) else 'fifo' if isinstance(u,FIFO) else 'pipeline';iteration=0
            if cfg[j,0]==1:
                label='idle' if ph==0 else 'response' if ph==u.timing.latency else u.timing.phases[ph]
                iteration=u.timing.phases[:ph].count('iterate')
            outputs[key]=Outputs(bool(ir),bool(ov),response(j,(b,f,t,r)) if ov else None,bool(hs&1),bool(hs&2),occ,
                                 tuple(bool(sv&(1<<i)) for i in range(length)),label,iteration)
        result.append(outputs)
    return result
