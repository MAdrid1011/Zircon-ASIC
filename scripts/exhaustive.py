"""FP4/FP8 complete binary and sharded FMA independent-oracle regression."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from zircon_asic import *


def validate(name,op,shard=0,shards=1,backend="numba"):
    exe=ROOT/"build/small_oracle"; source=ROOT/"scripts/small_oracle.cpp"
    if not exe.exists() or source.stat().st_mtime>exe.stat().st_mtime:
        exe.parent.mkdir(exist_ok=True)
        subprocess.run(["c++","-std=c++17","-O3",str(source),"-o",str(exe)],check=True)
    unit=FloatingPointUnit(name,op);w=unit.format.width
    total=1 << (w*(3 if op=="fma" else 2))
    start,end=total*shard//shards,total*(shard+1)//shards
    count=0;t=time.monotonic()
    for begin in range(start,end,65536):
        stop=min(begin+65536,end);idx=np.arange(begin,stop,dtype=np.uint64);mask=(1<<w)-1
        c=idx&mask if op=="fma" else np.zeros_like(idx)
        b=(idx>>w if op=="fma" else idx)&mask
        a=(idx>>(2*w) if op=="fma" else idx>>w)&mask
        for rm in Rounding:
            raw=subprocess.check_output([str(exe),name,str(["add","mul","fma","div"].index(op)),str(begin),str(stop),str(int(rm))])
            ref=np.frombuffer(raw,dtype="<u2")
            actual=unit.compute_batch(a,b,c,rounding=rm,backend=backend)
            bad=(actual.bits!=(ref&255))|(actual.flags!=(ref>>8))
            if np.any(bad):
                i=int(np.flatnonzero(bad)[0]);raise AssertionError(dict(format=name,op=op,rounding=rm,a=int(a[i]),b=int(b[i]),c=int(c[i]),reference=int(ref[i]),bits=int(actual.bits[i]),flags=int(actual.flags[i])))
            count+=len(idx)
    report=dict(format=name,operation=op,backend=backend,shard=shard,shards=shards,begin=start,end=end,cases=count,seconds=time.monotonic()-t,oracle="independent C++ exact 128-bit rational/encoding search",discrepancies=0)
    dest=ROOT/"build/exhaustive";dest.mkdir(exist_ok=True)
    (dest/f"{name}_{op}_{backend}_{shard}-of-{shards}.json").write_text(json.dumps(report,indent=2)+"\n")
    print(report,flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--format",choices=["e2m1","e4m3fn","e5m2"]);p.add_argument("--op",choices=["add","mul","fma","div"])
    p.add_argument("--shard",type=int,default=0);p.add_argument("--shards",type=int,default=1);p.add_argument("--backend",choices=["python","numba"],default="numba")
    a=p.parse_args()
    if not 0<=a.shard<a.shards: p.error("require 0 <= shard < shards")
    for name in [a.format] if a.format else ["e2m1","e4m3fn","e5m2"]:
        for op in [a.op] if a.op else ["add","mul","fma","div"]: validate(name,op,a.shard,a.shards,a.backend)
