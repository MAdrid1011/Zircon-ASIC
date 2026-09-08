"""Million-input unary CPU measurements with complete trace/state verification."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('NUMBA_CACHE_DIR',str(ROOT/'build/sfu/benchmark-cache'))
sys.path.insert(0,str(ROOT/'src'))
from zircon_asic import *
from zircon_asic.evidence import implementation_hash
from benchmark_bf16 import snapshot,rss


def elapsed(fn):
    start=time.perf_counter();value=fn();return time.perf_counter()-start,value


def network(name,op,requests,memory=False):
    unit=FloatingPointUnit(name,op);f=unit.format
    net=Network()
    if memory:
        a,b=SPM(4096,data_width=f.width),SPM(4096,data_width=f.width)
        word=f.width//8;values=(np.arange(4096//word,dtype=np.uint32)%(1<<f.fraction))+(f.bias<<f.fraction)
        a.load_image(values.astype(f'<u{word}').tobytes());b.load_image(bytes(4096))
        net.add('input',a).add('u',unit).add('output',b).connect('input','u')
        last='u'
        if op=='rsqrt':
            net.add('mul',FpMul(name)).connect('u','mul',b=(f.bias+1)<<f.fraction);last='mul'
        net.connect(last,'output',mapping={'address':Field('tag'),'write':True,'data':Field('bits'),'tag':Field('tag')})
        return net.source('input',requests).sink('output')
    return net.add('u',unit).source('u',requests).sink('u')


def cycle_benchmark(name,op,count,ready,resets,flushes,memory=False):
    from zircon_asic.fast_network import run_network
    u=FloatingPointUnit(name,op);word=u.format.width//8
    requests=([MemoryRequest((i%(4096//word))*word,tag=(i%(4096//word))*word) for i in range(count)] if memory else
              [Request((u.format.bias<<u.format.fraction)+(i%(1<<u.format.fraction)),tag=i) for i in range(count)])
    make=lambda:network(name,op,requests,memory)
    net=make();keys=net._order();expected=np.zeros((count,len(keys),10),np.uint64)
    # Trace collection is separate from both timing paths.
    for k in range(count):
        ports=net.step(ready=bool(ready[k]),reset=bool(resets[k]),flush=bool(flushes[k]))
        for j,key in enumerate(keys):
            o=ports[key];r=o.response
            expected[k,j,:2]=o.in_ready,o.out_valid
            if r:expected[k,j,2:6]=r.bits,int(r.status) if isinstance(r,MemoryResponse) else int(r.flags),r.tag,int(r.write) if isinstance(r,MemoryResponse) else r.remainder
            expected[k,j,6:]=sum(int(v)<<i for i,v in enumerate(o.stage_valid)),o.occupancy,0,int(o.accepted)+2*int(o.delivered)
    final=snapshot(net);del net;gc.collect()
    net=make();compile_seconds=run_network(net,1,_compile_only=True)
    got_keys,actual=run_network(net,count,ready=ready,reset=resets,flush=flushes,trace=True,_raw_trace=True)
    assert keys==got_keys
    actual[:,:,2:6][actual[:,:,1]==0]=0
    if not np.array_equal(expected,actual):
        mismatch=np.argwhere(expected!=actual)[0]
        raise AssertionError((name,op,memory,mismatch.tolist()))
    assert snapshot(net)==final
    trace_hash=hashlib.sha256(expected.tobytes()).hexdigest()
    del net,expected,actual;gc.collect()
    times={'python':[],'numba':[]}
    for _ in range(5):
        for backend in ('python','numba'):
            net=make()
            seconds,_=elapsed(lambda:net.run(count,backend=backend,ready=ready,reset=resets,flush=flushes))
            assert snapshot(net)==final
            times[backend].append(seconds);del net;gc.collect()
    return dict(format=name,operation=op,mode='spm-network' if memory else 'cycle-unit',cycles=count,
                compile_seconds=compile_seconds,seconds=times,
                python_median_seconds=float(np.median(times['python'])),numba_median_seconds=float(np.median(times['numba'])),
                speedup=float(np.median(times['python'])/np.median(times['numba'])),trace_sha256=trace_hash,
                trace_discrepancies=0,final_state=final,max_rss_bytes=rss())


def batch_benchmark(name,op,count,rng):
    from numba import typeof
    from zircon_asic.fast import _batch
    from zircon_asic.fast_unary import kernel_resources
    u=FloatingPointUnit(name,op);f=u.format
    a=rng.integers(0,1<<f.width,count,dtype=np.uint64)
    if op=='exp':
        values=rng.uniform(-8,8,count//2)
        raw=values.astype(np.float16).view(np.uint16) if name=='fp16' else values.astype(np.float32).view(np.uint32)
        a[:len(raw)]=raw>>16 if name=='bf16' else raw
    rounding=np.zeros(count,np.uint64) if op=='exp' else rng.integers(0,5,count,dtype=np.uint64)
    dummy=np.zeros(1,np.uint64)
    args=(dummy,dummy,dummy,dummy,4,f.width,f.exponent,f.fraction,f.bias,0,False,kernel_resources((u,)))
    compile_seconds,_=elapsed(lambda:_batch.compile(tuple(typeof(v) for v in args)))
    expected=u.compute_batch(a,rounding=rounding,backend='python')
    actual=u.compute_batch(a,rounding=rounding,backend='numba')
    for field in ('bits','flags','tags','remainder'):assert np.array_equal(getattr(expected,field),getattr(actual,field))
    result_hash=hashlib.sha256(expected.bits.tobytes()+expected.flags.tobytes()).hexdigest()
    times={'python':[],'numba':[]}
    for _ in range(5):
        for backend in ('python','numba'):
            seconds,actual=elapsed(lambda:u.compute_batch(a,rounding=rounding,backend=backend))
            assert np.array_equal(expected.bits,actual.bits) and np.array_equal(expected.flags,actual.flags)
            times[backend].append(seconds)
    return dict(format=name,operation=op,mode='batch',inputs=count,compile_seconds=compile_seconds,
                seconds=times,python_median_seconds=float(np.median(times['python'])),
                numba_median_seconds=float(np.median(times['numba'])),
                speedup=float(np.median(times['python'])/np.median(times['numba'])),result_sha256=result_hash,
                discrepancies=0,max_rss_bytes=rss())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--count',type=int,default=1_000_000)
    parser.add_argument('--unit',action='append');parser.add_argument('--mode',choices=['all','batch','cycle-unit','spm-network'],default='all')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();count=args.count;rng=np.random.default_rng(751)
    target=ROOT/'build/sfu/benchmark.json';target.parent.mkdir(exist_ok=True)
    import numba
    result=dict(host=platform.platform(),machine=platform.machine(),python=platform.python_version(),numpy=np.__version__,numba=numba.__version__,
                implementation_hash=implementation_hash(),contract_hash=contract_hash(),seed=751,count=count,cases=[],
                benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                method='Explicit dispatcher compilation; complete numerical/trace and final-state equality before timing. Five interleaved Python/Numba runs; input generation and network construction excluded, marshalling and result/state restoration included. RSS is process high-water mark.')
    if args.resume and target.exists():
        previous=json.loads(target.read_text())
        for key in ('host','machine','python','numpy','numba','implementation_hash','contract_hash','count','seed','benchmark_sha256'):
            if previous.get(key)!=result[key]:raise RuntimeError(f'cannot resume changed benchmark {key}')
        result=previous
    ready=rng.random(count)>.1;ready[500:550]=False
    resets=np.zeros(count,bool);flushes=np.zeros(count,bool)
    resets[997::99991]=True;flushes[499::19001]=True
    for key in args.unit or [f'{f}.{op}' for f in ('fp32','fp16','bf16') for op in ('exp','rcp','sqrt','rsqrt')]:
        name,op=key.split('.')
        for mode in ('batch','cycle-unit','spm-network'):
            if args.mode not in ('all',mode) or (mode=='spm-network' and op not in ('exp','rsqrt')):continue
            if any((r['format'],r['operation'],r['mode'])==(name,op,mode) for r in result['cases']):continue
            case_seed=int.from_bytes(hashlib.sha256((key+':751').encode()).digest()[:8],'little')
            row=batch_benchmark(name,op,count,np.random.default_rng(case_seed)) if mode=='batch' else cycle_benchmark(name,op,count,ready,resets,flushes,mode=='spm-network')
            result['cases'].append(row);target.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({k:v for k,v in row.items() if k!='final_state'}),flush=True)
    assert implementation_hash()==result['implementation_hash']
    if any(r['speedup']<2 for r in result['cases']):raise RuntimeError('Numba speedup target below 2x')


if __name__=='__main__':main()
