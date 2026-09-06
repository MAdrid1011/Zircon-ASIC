"""Common external stimulus and observation for a composed Chisel/Python network."""
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from zircon_asic import *
from validate_rtl import run


def main():
    out=ROOT/"build/rtl/network";out.mkdir(parents=True,exist_ok=True)
    run(["sbt",f"runMain zircon.GenerateNetwork {out}"],out/"generate.log",ROOT/"hardware")
    harness=out/"trace.cpp";harness.write_text((ROOT/"scripts/rtl_trace.cpp").read_text().replace("@TOP@","VNetworkExample"))
    run(["verilator","--cc","--exe","--build","-j","4","--assert","-Wno-fatal","--top-module","NetworkExample","--Mdir",str(out/"obj"),"-CFLAGS","-std=c++17",str(out/"Unit.sv"),str(harness),"-o","trace"],out/"compile.log")
    net=Network().add("a",INT8Add()).add("fifo",FIFO(3)).add("b",INT8Mul()).add("d",DelayLine(2))
    net.connect("a","fifo").connect("fifo","b",b=3).connect("b","d").sink("d")
    rng=np.random.default_rng(5128);rows=[];expected=[]
    for k in range(20000):
        req=Request(int(rng.integers(0,256)),int(rng.integers(0,256)),tag=k) if rng.random()>.15 else None
        ready=bool(rng.random()>.3) and not 300<k%1000<340
        rst,fl=k%3997==0,k%977==0
        net.sources["a"]=InputSource([req] if req else [])
        o=net.step(ready=ready,reset=rst,flush=fl)
        r=req or Request(0,0)
        rows.append(f"{int(rst)} {int(fl)} {int(req is not None)} {r.a} {r.b} 0 0 {r.tag} {int(ready)}\n")
        result=o["d"].response or Response(0)
        vals=o["a"].stage_valid+o["fifo"].stage_valid+o["b"].stage_valid+o["d"].stage_valid
        expected.append([int(o["a"].in_ready),int(o["d"].out_valid),result.bits,int(result.flags),result.tag,result.remainder,sum(int(v)<<i for i,v in enumerate(vals)),sum(x.occupancy for x in o.values()),0,0])
    (out/"stimulus.txt").write_text("".join(rows));(out/"python.trace").write_text("\n".join(" ".join(map(str,x)) for x in expected))
    run([str(out/"obj/trace"),str(out/"stimulus.txt"),str(out/"rtl.trace")],out/"run.log")
    for k,(line,py) in enumerate(zip((out/"rtl.trace").read_text().splitlines(),expected)):
        rtl=list(map(int,line.split()));fields=[0,1,6,7,8,9]+([2,3,4,5] if py[1] else [])
        assert all(rtl[i]==py[i] for i in fields),(k,py,rtl)
    import json
    (out/"alignment.json").write_text(json.dumps(dict(cycles=len(rows),seed=5128,contract_hash=contract_hash(),discrepancy_cycles=0,network="INT8Add -> FIFO(3) -> INT8Mul(*3) -> DelayLine(2)"),indent=2))
    print("PASS composed Chisel/Python network: 20000 cycles")


if __name__=="__main__":main()
