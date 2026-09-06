"""Record measured scalar/batch/network CPU costs with cold and warm runs."""
from pathlib import Path
import platform,time,json,sys,resource
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from zircon_asic import *


def timed(fn,repeats=1):
    start=time.perf_counter()
    for _ in range(repeats):fn()
    return (time.perf_counter()-start)/repeats


def main():
    rng=np.random.default_rng(737);n=100000
    a,b,c=rng.integers(0,2**32,(3,n),dtype=np.uint32)
    rows=[]
    for fmt,op in [("fp32","fma"),("fp16","div"),("e4m3fn","mul"),("e2m1","fma")]:
        u=FloatingPointUnit(fmt,op);mask=(1<<u.format.width)-1;aa,bb,cc=a&mask,b&mask,c&mask
        scalar=timed(lambda:[u.compute(Request(int(x),int(y),int(z))) for x,y,z in zip(aa,bb,cc)])
        py=timed(lambda:u.compute_batch(aa,bb,cc,backend="python"))
        cold=timed(lambda:u.compute_batch(aa,bb,cc,backend="numba"))
        warm=timed(lambda:u.compute_batch(aa,bb,cc,backend="numba"),5)
        rows.append(dict(format=fmt,op=op,count=n,scalar_seconds=scalar,python_batch_seconds=py,numba_first_call_seconds=cold,numba_warm_seconds=warm,speedup_over_python_batch=py/warm))
    def network():
        net=Network()
        for i in range(16):
            net.add(str(i),INT8Mul()).source(str(i),[Request(j&255,3,tag=j) for j in range(4000)]).sink(str(i))
        return net
    net=network();py=timed(lambda:net.run(4100))
    net=network();cold=timed(lambda:net.run(4100,backend="numba"))
    net=network();warm=timed(lambda:net.run(4100,backend="numba"))
    report=dict(host=platform.platform(),processor=platform.machine(),python=platform.python_version(),numpy=np.__version__,arithmetic=rows,
                network=dict(modules=16,cycles=4100,python_seconds=py,numba_first_call_seconds=cold,numba_warm_seconds=warm,speedup=py/warm),
                max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                note="First call includes cache loading or compilation. Warm timing includes Python API marshalling and result materialization; no traces.")
    p=ROOT/"build/benchmark.json";p.write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))


if __name__=="__main__":main()
