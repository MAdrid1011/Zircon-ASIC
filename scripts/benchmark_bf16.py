"""Million-input BF16 CPU measurements, with full result/state equivalence first."""
from pathlib import Path
import gc,hashlib,json,platform,resource,sys,time
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from zircon_asic import *
from zircon_asic.evidence import implementation_hash


def measure(fn):
    start=time.perf_counter();result=fn();return time.perf_counter()-start,result


def rss():
    n=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return n if sys.platform=='darwin' else n*1024


def snapshot(net):
    h=hashlib.sha256()
    for key,sink in net.sinks.items():
        h.update(str(key).encode())
        rows=[]
        for cycle,r in sink.received:
            rows.append((cycle,r.bits,r.tag,int(r.status) if hasattr(r,'status') else int(r.flags),int(r.write) if hasattr(r,'write') else r.remainder))
        h.update(np.asarray(rows,dtype=np.uint64).tobytes())
    states={}
    for name,u in net.units.items():
        state=dict(stats=u.stats.report(),cycle=u.cycle)
        if isinstance(u,SPM):
            state.update(memory=hashlib.sha256(u.memory.tobytes()).hexdigest(),debug=u.debug_state())
        else:state.update(slots=repr(u._slots),phase=u._phase)
        states[name]=state
    return dict(outputs=h.hexdigest(),states=states,positions={str(k):v.position for k,v in net.sources.items()})


def network(memory,requests):
    n=Network()
    if memory:
        a=SPM(128,data_width=16);b=SPM(128,data_width=16)
        a.load_image(np.arange(0x3f00,0x3f40,dtype='<u2').tobytes());b.load_image(bytes(128))
        n.add('a',a).add('f',BF16Fma()).add('b',b).connect('a','f',b=0x4000,c=0x3f80)
        n.connect('f','b',mapping={'address':Field('tag'),'write':True,'data':Field('bits'),'tag':Field('tag')})
        n.source('a',requests).sink('b')
    else:
        n.add('a',BF16Add()).add('m',BF16Mul()).add('f',BF16Fma())
        n.connect('a','m',b=0x3f80).connect('m','f',b=0x4000,c=0x3f80)
        n.source('a',requests).sink('f')
    return n


def main():
    count=1_000_000;rng=np.random.default_rng(751)
    a,b,c=rng.integers(0,65536,(3,count),dtype=np.uint32)
    arithmetic=[]
    for op in ('add','mul','fma','div'):
        u=FloatingPointUnit('bf16',op)
        py,expected=measure(lambda:u.compute_batch(a,b,c,backend='python'))
        first,got=measure(lambda:u.compute_batch(a,b,c,backend='numba'))
        for field in ('bits','flags','tags','remainder'):assert np.array_equal(getattr(expected,field),getattr(got,field))
        warm=[]
        for _ in range(5):
            elapsed,got=measure(lambda:u.compute_batch(a,b,c,backend='numba'));warm.append(elapsed)
        row=dict(operation=op,count=count,python_seconds=py,numba_first_call_seconds=first,
                 numba_warm_seconds=warm,numba_warm_median_seconds=float(np.median(warm)),max_rss_bytes=rss(),discrepancies=0)
        arithmetic.append(row);print(json.dumps(row),flush=True)
    networks=[]
    del expected,got;gc.collect()
    for memory in (False,True):
        requests=([MemoryRequest((i%64)*2,tag=(i%64)*2) for i in range(count)] if memory else
                  [Request(0x3f00+(i%64),0x3f80,tag=i) for i in range(count)])
        # Patterns are generated once, before either backend is timed.
        ready=rng.random(count)>.1;ready[500:550]=False
        n=network(memory,requests);py,_=measure(lambda:n.run(count,ready=ready,backend='python'));expected=snapshot(n)
        del n;gc.collect()
        n=network(memory,requests);first,_=measure(lambda:n.run(count,ready=ready,backend='numba'));assert snapshot(n)==expected
        del n;gc.collect();warm=[]
        for _ in range(5):
            n=network(memory,requests);elapsed,_=measure(lambda:n.run(count,ready=ready,backend='numba'))
            assert snapshot(n)==expected;warm.append(elapsed);del n;gc.collect()
        row=dict(network='spm-fma-spm' if memory else 'add-mul-fma',cycles=count,python_seconds=py,
                 numba_first_call_seconds=first,numba_warm_seconds=warm,numba_warm_median_seconds=float(np.median(warm)),
                 speedup=py/float(np.median(warm)),max_rss_bytes=rss(),discrepancies=0)
        networks.append(row);print(json.dumps(row),flush=True)
        del requests;gc.collect()
    report=dict(host=platform.platform(),machine=platform.machine(),python=platform.python_version(),numpy=np.__version__,
                contract_hash=contract_hash(),implementation_hash=implementation_hash(),seed=751,
                arithmetic=arithmetic,networks=networks,
                method='First call includes compilation or cache loading; warm results are five independent runs. Network construction and input generation excluded; marshalling/state restoration included. RSS is process high-water mark.')
    (ROOT/'build/bf16/benchmark.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
