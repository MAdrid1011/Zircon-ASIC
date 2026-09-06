"""Load, run, and read back a Chisel SPM -> INT32Mul -> SPM network."""
from pathlib import Path
import hashlib,json,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from zircon_asic import *
from zircon_asic.spm import spm_implementation_hash
from validate_rtl import run,verilator_configuration
from validate_spm import harness

def main():
    implementation=spm_implementation_hash()
    dest=ROOT/'build/rtl/spm-network';dest.mkdir(parents=True,exist_ok=True)
    run(['sbt',f'runMain zircon.GenerateSPMExample {dest}'],dest/'generate.log',ROOT/'hardware')
    cpp=harness(1,0).replace('VSPM','VSPMExample').replace('while(in>>rst>>fl)', 'while(in>>rst>>fl)').replace('d.io_flush=fl;','d.io_flush=fl; in >> x; d.io_mode=x;')
    (dest/'trace.cpp').write_text(cpp)
    ver,flags=verilator_configuration()
    run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','-Wno-fatal','--top-module','SPMExample',
         '--Mdir',str(dest/'obj'),str(dest/'Unit.sv'),str(dest/'trace.cpp'),'-o','trace'],dest/'compile.log')
    a=SPM(256);b=SPM(256);mul=INT32Mul();rows=[];expect=[]
    net=Network().add('a',a).add('mul',mul).add('b',b).connect('a','mul',b=3)
    net.connect('mul','b',mapping={'address':Field('tag'),'write':True,'data':Field('bits'),'tag':Field('tag')})
    def cycle(mode,request=None,ready=True,reset=False,flush=False):
        statea=a.debug_state();stateb=b.debug_state();mulocc=sum(x is not None for x in mul._slots)
        if mode==1:
            net.sources['a']=InputSource([request] if request else [])
            out=net.step(ready=ready,reset=reset,flush=flush);inp=out['a'];resp=out['b']
        else:
            inp=(a if mode==0 else b).step(Inputs(request,ready,reset,flush));resp=inp
            (b if mode==0 else a).step(Inputs(reset=reset,flush=flush));mul.step(Inputs(reset=reset,flush=flush))
        r=request or MemoryRequest(0);v=resp.response
        rows.append(' '.join(map(str,[int(reset),int(flush),mode,int(request is not None),r.address,int(r.write),r.data,15 if r.mask is None else r.mask,r.tag,int(ready)]))+'\n')
        expect.append([int(inp.in_ready),int(resp.out_valid),v.bits if v else 0,v.tag if v else 0,int(v.write) if v else 0,int(v.status) if v else 0,
            int(any(statea['flight']) or any(stateb['flight']) or mulocc>0),sum(statea['queue_counts'])+sum(stateb['queue_counts']),sum(statea['outstanding'])+sum(stateb['outstanding'])+mulocc])
        return inp
    cycle(0,reset=True)
    for adr in range(0,256,4):cycle(0,MemoryRequest(adr,True,adr+5))
    for _ in range(10):cycle(0)
    rng=np.random.default_rng(4921);held=None
    for k in range(20000):
        if held is None and rng.random()>.15:
            adr=int(rng.integers(64))*4;held=MemoryRequest(adr,tag=adr)
        out=cycle(1,held,bool(rng.random()>.3) and not 330<k%1000<380,reset=k%3997==300,flush=k%977==100)
        if out.accepted or k%3997==300 or k%977==100:held=None
    for _ in range(12):cycle(1)
    for adr in range(0,256,4):cycle(2,MemoryRequest(adr))
    for _ in range(4):cycle(2)
    (dest/'stimulus.txt').write_text(''.join(rows));(dest/'python.trace').write_text('\n'.join(' '.join(map(str,r)) for r in expect))
    run([str(dest/'obj/trace'),str(dest/'stimulus.txt'),str(dest/'rtl.trace')],dest/'run.log')
    actual=(dest/'rtl.trace').read_text().splitlines();assert len(actual)==len(expect)
    for k,(line,ex) in enumerate(zip(actual,expect)):
        got=list(map(int,line.split()));indices=[0,1,6,7,8]+([2,3,4,5] if ex[1] else [])
        if any(got[i]!=ex[i] for i in indices):
            failure=dict(cycle=k,python=ex,rtl=got,stimulus=rows[k]);(dest/'failure.json').write_text(json.dumps(failure,indent=2));raise AssertionError(failure)
    assert implementation==spm_implementation_hash()
    (dest/'alignment.json').write_text(json.dumps(dict(implementation_hash=implementation,cycles=len(rows),seed=4921,discrepancy_cycles=0,rtl_sha256=hashlib.sha256((dest/'Unit.sv').read_bytes()).hexdigest(),contract_hash=spm_contract_hash(),verilator=ver),indent=2))
    print('PASS SPM -> INT32Mul -> SPM',len(rows),'cycles')

if __name__=='__main__':main()
