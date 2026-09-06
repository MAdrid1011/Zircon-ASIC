"""Pre-generated million-cycle SPM benchmarks; compare final state and counters."""
import argparse, gc, hashlib, json, platform, statistics, sys, time, tracemalloc
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from zircon_asic import *


def make(case,cycles):
    if case=='single':
        s=SPM();s.load_image(bytes(4096));n=Network().add('s',s)
        n.source('s',[MemoryRequest(0,True,17),MemoryRequest(0)]*((cycles+1)//2))
    elif case=='conflict':
        s=SPM(4096,32,1,4);s.load_image(bytes(4096));n=Network().add('s',s)
        for p in range(4):n.source('s',[MemoryRequest(0,True,p),MemoryRequest(0)]*((cycles+1)//2),port=p)
    else:
        a=SPM();a.load_image(bytes(4096));b=SPM();b.load_image(bytes(4096))
        n=Network().add('a',a).add('mul',INT32Mul()).add('b',b)
        n.source('a',[MemoryRequest(0)]*cycles).connect('a','mul',b=3)
        n.connect('mul','b',mapping={'address':0,'write':True,'data':Field('bits'),'tag':0})
    return n


def snapshot(n):
    state={}
    for name,u in n.units.items():
        row=dict(stats=u.stats.report(),cycle=u.cycle)
        if isinstance(u,SPM):row.update(memory=hashlib.sha256(u.memory.tobytes()).hexdigest(),debug=u.debug_state(),bank_accesses=u.bank_accesses,conflicts=u.bank_conflicts,credit_stalls=u.credit_stalls)
        else:row.update(slots=str(u._slots),phase=u._phase)
        state[name]=row
    return state


def main(cycles=1000000,repeats=5):
    import numpy,numba,resource
    from zircon_asic.spm import spm_implementation_hash
    initial_hash=spm_implementation_hash()
    result=dict(host=platform.platform(),python=platform.python_version(),numpy=numpy.__version__,numba=numba.__version__,cycles=cycles,repeats=repeats,cases=[],implementation_hash=initial_hash)
    out=ROOT/'build/spm-benchmark.json'
    for case in ('single','conflict','mixed'):
        warm=make(case,16);t=time.perf_counter();warm.run(16,backend='numba');compile_time=time.perf_counter()-t
        del warm;timings={'python':[],'numba':[]};reference=None
        for backend in ('python','numba'):
            for repeat in range(repeats):
                n=make(case,cycles);gc.collect();t=time.perf_counter();n.run(cycles,backend=backend);elapsed=time.perf_counter()-t
                state=snapshot(n)
                if reference is None:reference=state
                assert reference==state,(case,backend,'state mismatch')
                timings[backend].append(elapsed);del n;gc.collect()
                print(case,backend,repeat,round(elapsed,3),flush=True)
        speed=statistics.median(timings['python'])/statistics.median(timings['numba'])
        result['cases'].append(dict(case=case,warmup_seconds=compile_time,seconds=timings,speedup=speed,matched_final_state=True,target_passed=speed>=2))
        result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        out.write_text(json.dumps(result,indent=2)+'\n')
    assert initial_hash==spm_implementation_hash(),'implementation changed during benchmark'
    assert all(c['target_passed'] for c in result['cases'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cycles',type=int,default=1000000);p.add_argument('--repeats',type=int,default=5)
    a=p.parse_args();main(a.cycles,a.repeats)
