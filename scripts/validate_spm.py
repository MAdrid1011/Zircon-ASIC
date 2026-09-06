"""Persisted, held-valid SPM stimulus and cycle-by-cycle RTL differential check."""
from pathlib import Path
import argparse, hashlib, json, sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from zircon_asic import SPM, MemoryRequest as R, Inputs, spm_contract_hash
from zircon_asic.spm import spm_implementation_hash
from validate_rtl import run, verilator_configuration


def harness(ports,banks):
    drive=''
    observe=''
    for p in range(ports):
        for f in ('valid','bits_address','bits_write','bits_data','bits_mask','bits_tag'):
            drive+=f'in >> x; d.io_in_{p}_{f}=x;\n'
        drive+=f'in >> x; d.io_out_{p}_ready=x;\n'
        for f in (f'in_{p}_ready',f'out_{p}_valid',f'out_{p}_bits_bits',f'out_{p}_bits_tag',f'out_{p}_bits_write',f'out_{p}_bits_status',f'flight_{p}',f'queueCount_{p}',f'outstanding_{p}'):
            observe+=f'out << uint64_t(d.io_{f}) << " ";\n'
    for b in range(banks):
        for f in (f'rr_{b}',f'grantValid_{b}',f'grant_{b}',f'bankWrite_{b}',f'bankAddress_{b}',f'bankData_{b}',f'bankMask_{b}'):
            observe+=f'out << uint64_t(d.io_{f}) << " ";\n'
    return '''#include "VSPM.h"
#include "verilated.h"
#include <fstream>
#include <cstdint>
int main(int argc,char**argv) {
 Verilated::commandArgs(argc,argv); VSPM d; std::ifstream in(argv[1]); std::ofstream out(argv[2]); uint64_t rst,fl,x;
 d.clock=0; d.reset=1; d.io_flush=0; d.eval(); d.clock=1; d.eval(); d.clock=0; d.eval();
 while(in>>rst>>fl) { d.clock=0; d.reset=rst; d.io_flush=fl;
'''+drive+'''d.eval();
'''+observe+'''out << "\\n"; d.clock=1; d.eval(); d.clock=0; d.eval(); }
 d.final(); }
'''


def validate(capacity=4096,width=32,banks=1,ports=1,ihp=False,cycles=20000,seeds=(751,),regenerate=True):
    implementation=spm_implementation_hash()
    name=f'spm-{capacity}-{width}-{banks}-{ports}'+('-ihp' if ihp else '')
    dest=ROOT/'build/rtl'/name;dest.mkdir(parents=True,exist_ok=True)
    if regenerate or not (dest/'Unit.sv').exists():
        run(['sbt',f'runMain zircon.GenerateSPM {capacity} {width} {banks} {ports} {dest}'+(' ihp' if ihp else '')],dest/'generate.log',ROOT/'hardware')
    (dest/'trace.cpp').write_text(harness(ports,banks))
    ver,flags=verilator_configuration()
    sources=[str(dest/'Unit.sv')]
    if ihp:
        sources += [str(p) for p in sorted((ROOT/'build/ihp/verilog').glob('*.v'))]
    build_id=dict(rtl=hashlib.sha256((dest/'Unit.sv').read_bytes()).hexdigest(),harness=hashlib.sha256((dest/'trace.cpp').read_bytes()).hexdigest(),verilator=ver,
                  sources={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources})
    bid=dest/'build-id.json';exe=dest/'obj/trace'
    if not exe.exists() or not bid.exists() or json.loads(bid.read_text())!=build_id:
        run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','--timing','-DFUNCTIONAL','-Wno-fatal','--top-module','SPM',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17',*sources,str(dest/'trace.cpp'),'-o','trace'],dest/'compile.log')
        bid.write_text(json.dumps(build_id,indent=2))
    reports=[]
    for seed in seeds:
        s=SPM(capacity,width,banks,ports);rng=np.random.default_rng(seed)
        rows=[];expected=[];masks=[];held=[None]*ports; schedule=[]; pyoutputs=[]
        def cycle(ins):
            obs=s.eval(ins[0] if ports==1 else tuple(ins)); obs=(obs,) if ports==1 else obs
            schedule.append(ins[0] if ports==1 else tuple(ins));pyoutputs.append(obs[0] if ports==1 else obs)
            debug=s.debug_state(); grants=s._pending[2]
            vals=[];compare=[]
            row=[int(ins[0].reset),int(ins[0].flush)]
            for p,(i,o) in enumerate(zip(ins,obs)):
                r=i.request or R(0)
                row += [int(i.request is not None),r.address,int(r.write),r.data,(1<<(width//8))-1 if r.mask is None else r.mask,r.tag,int(i.out_ready)]
                response=o.response
                vals += [int(o.in_ready),int(o.out_valid),response.bits if response else 0,response.tag if response else 0,
                         int(response.write) if response else 0,int(response.status) if response else 0,int(debug['flight'][p]),debug['queue_counts'][p],debug['outstanding'][p]]
                compare += [True,True]+[o.out_valid]*4+[True]*3
            for b,p in enumerate(grants):
                r=ins[p].request if p>=0 else R(0)
                vals += [debug['rr'][b],int(p>=0),max(p,0),int(p>=0 and r.write),r.address,r.data,(1<<(width//8))-1 if r.mask is None else r.mask]
                compare += [True,True,p>=0,True,p>=0,p>=0,p>=0]
            rows.append(' '.join(map(str,row))+'\n');expected.append(vals);masks.append(compare)
            s.tick();return obs
        # Initialization goes through the exact same SRAM port as normal traffic.
        cycle([Inputs(reset=True) for _ in range(ports)])
        for a in range(0,capacity,width//8):
            ins=[Inputs() for _ in range(ports)];ins[0]=Inputs(R(a,True,0))
            assert cycle(ins)[0].accepted
        for _ in range(3):cycle([Inputs() for _ in range(ports)])
        for k in range(cycles):
            rst=k%3997==100;fl=k%977==700
            for p in range(ports):
                if held[p] is None and rng.random()>.15:
                    a=int(rng.integers(0,capacity//(width//8)))*(width//8)
                    if k%5==0:a=0
                    if k%97==0:a=capacity
                    elif width>8 and k%73==0:a=1
                    held[p]=R(a,bool(rng.integers(2)),int(rng.bit_generator.random_raw())&((1<<width)-1),int(rng.integers(1<<(width//8))),k*ports+p)
            ins=[Inputs(held[p],bool(rng.random()>.25) and not (p==0 and 300<k%1000<350),rst,fl) for p in range(ports)]
            obs=cycle(ins)
            for p,o in enumerate(obs):
                if o.accepted or rst or fl:held[p]=None
        for _ in range(4):cycle([Inputs() for _ in range(ports)])
        # Read back every word to compare final memory, including masked updates.
        for a in range(0,capacity,width//8):
            ins=[Inputs() for _ in range(ports)];ins[0]=Inputs(R(a))
            assert cycle(ins)[0].accepted
        for _ in range(3):cycle([Inputs() for _ in range(ports)])
        prefix=dest/f'seed-{seed}'
        fast=SPM(capacity,width,banks,ports)
        fastoutputs=fast.run(schedule,backend='numba',trace=True)
        if fastoutputs!=pyoutputs:
            first=next(k for k,(a,b) in enumerate(zip(fastoutputs,pyoutputs)) if a!=b)
            (dest/'numba-failure.json').write_text(json.dumps(dict(seed=seed,cycle=first,stimulus=rows[first],python=str(pyoutputs[first]),numba=str(fastoutputs[first])),indent=2))
            raise AssertionError(f'Numba differs at {first}')
        assert fast.debug_state()==s.debug_state() and fast.dump_image()==s.dump_image()
        assert fast.stats==s.stats and fast.bank_accesses==s.bank_accesses
        stim=prefix.with_suffix('.stim');trace=prefix.with_suffix('.rtl');pytrace=prefix.with_suffix('.python')
        stim.write_text(''.join(rows));pytrace.write_text('\n'.join(' '.join(map(str,r)) for r in expected))
        run([str(exe),str(stim),str(trace)],prefix.with_suffix('.log'))
        actual=trace.read_text().splitlines();assert len(actual)==len(expected)
        for k,(row,exp,mask) in enumerate(zip(actual,expected,masks)):
            got=list(map(int,row.split()))
            if any(a!=b for a,b,m in zip(got,exp,mask) if m):
                failure=dict(cycle=k,seed=seed,expected=exp,actual=got,mask=mask,stimulus=rows[k])
                (dest/'failure.json').write_text(json.dumps(failure,indent=2));raise AssertionError(failure)
        reports.append(dict(seed=seed,cycles=len(rows),random_cycles=cycles,discrepancy_cycles=0))
        print(f'PASS {name} seed={seed} cycles={len(rows)}',flush=True)
    assert implementation==spm_implementation_hash(),'implementation changed during validation'
    report=dict(implementation_hash=implementation,configuration=dict(capacity_bytes=capacity,data_width=width,banks=banks,ports=ports,ihp=ihp),runs=reports,
                contract_hash=spm_contract_hash(),build=build_id,qualification='cycle-verified')
    (dest/'alignment.json').write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capacity',type=int,default=4096);p.add_argument('--width',type=int,default=32)
    p.add_argument('--banks',type=int,default=1);p.add_argument('--ports',type=int,default=1);p.add_argument('--ihp',action='store_true')
    p.add_argument('--cycles',type=int,default=20000);p.add_argument('--seeds',type=int,nargs='+',default=[751,752,753,754,755]);p.add_argument('--no-regenerate',action='store_true')
    a=p.parse_args();validate(a.capacity,a.width,a.banks,a.ports,a.ihp,a.cycles,a.seeds,not a.no_regenerate)
