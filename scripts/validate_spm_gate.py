"""Replay registered RTL stimulus against the routed netlist and real macro model."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys,re
from ppa import IMAGE
from validate_spm import ROOT,harness
from validate_rtl import run,verilator_configuration

def validate(directory,seed=751):
    physical=Path(directory).resolve();meta=json.loads((physical/'physical.json').read_text())
    ports,banks=meta['ports'],meta['banks']
    rtl=ROOT/'build/rtl'/meta['configuration']
    dest=physical/'gate';dest.mkdir(exist_ok=True)
    cp='/workspace/'+str(dest.relative_to(ROOT))
    subprocess.run(['docker','run','--rm','--platform','linux/amd64','-v',f'{ROOT}:/workspace',IMAGE,
        'cp','/OpenROAD-flow-scripts/flow/platforms/ihp-sg13g2/verilog/sg13g2_stdcell.v',cp+'/sg13g2_stdcell.v'],check=True)
    # Verilator ignores $setuphold delayed-reference arguments. For zero-delay
    # functional replay only, explicitly connect those helper wires to ports.
    # Keep the original model and both hashes; SRAM models remain unmodified.
    original=(dest/'sg13g2_stdcell.v').read_text()
    def helpers(match):
        names=re.findall(r'delayed_\w+',match.group(0))
        return match.group(0)+'\n'+''.join(f'assign {name} = {name[8:]};\n' for name in names)
    functional=re.sub(r'wire\s+delayed_\w+[^;]*;',helpers,original)
    # A 10 ps sequential output delay prevents zero-delay clock-tree delta races.
    # This is a functional simulation accommodation, never a timing model/SDF.
    functional=re.sub(r'\b(buf|not)\s+\((Q(?:_N)?),',r'\1 #0.01 (\2,',functional)
    functional='// Modified by Zircon-ASIC: connect specify helper signals and add 10 ps sequential output delay for functional replay.\n'+functional
    (dest/'sg13g2_stdcell_functional.v').write_text(functional)
    cpp=harness(ports,banks).replace('d.eval();','settle();')
    production=meta.get('top')=='SPMPhysical'
    if production:
        cpp=cpp.replace('VSPM','VSPMPhysical')
        # Keep field offsets in the replay trace; observation pins are absent at
        # the production boundary, and their slots are not compared here.
        cpp=re.sub(r'out << uint64_t\(d\.io_(?:flight|queueCount|outstanding|rr|grantValid|grant|bankWrite|bankAddress|bankData|bankMask)_\d+\) << " ";', 'out << 0 << " ";',cpp)
    cpp=cpp.replace('uint64_t rst,fl,x;', 'uint64_t rst,fl,x; auto settle=[&]() { d.eval(); while(d.eventsPending()) { Verilated::timeInc(d.nextTimeSlot()-Verilated::time()); d.eval(); } };')
    (dest/'trace.cpp').write_text(cpp)
    ver,flags=verilator_configuration()
    sources=[physical/'results/6_final.v',dest/'sg13g2_stdcell_functional.v',*sorted((ROOT/'build/ihp/verilog').glob('*.v'))]
    shutil.rmtree(dest/'obj',ignore_errors=True)
    run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','--timing','-DFUNCTIONAL','-Wno-fatal','--top-module',meta.get('top','SPM'),
         '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17',*map(str,sources),str(dest/'trace.cpp'),'-o','trace'],dest/'compile.log')
    stimulus=rtl/f'seed-{seed}.stim';expected=(rtl/f'seed-{seed}.python').read_text().splitlines()
    run([str(dest/'obj/trace'),str(stimulus),str(dest/'gate.trace')],dest/'run.log')
    actual=(dest/'gate.trace').read_text().splitlines();assert len(actual)==len(expected)
    for k,(py,gate) in enumerate(zip(expected,actual)):
        a=list(map(int,py.split()));b=list(map(int,gate.split()));indices=[]
        for p in range(ports):
            q=9*p;indices += [q,q+1]+([] if production else [q+6,q+7,q+8])+(list(range(q+2,q+6)) if a[q+1] else [])
        for bank in range(0 if production else banks):
            q=9*ports+7*bank;indices += [q,q+1,q+3]+([q+2,q+4,q+5,q+6] if a[q+1] else [])
        if any(a[i]!=b[i] for i in indices):
            failure=dict(cycle=k,seed=seed,python=a,gate=b);(dest/'failure.json').write_text(json.dumps(failure,indent=2));raise AssertionError(failure)
    report=dict(cycles=len(actual),seed=seed,discrepancy_cycles=0,verilator=ver,netlist_sha256=hashlib.sha256(sources[0].read_bytes()).hexdigest(),
        sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},timing_simulation=False,
        stdcell_original_sha256=hashlib.sha256(original.encode()).hexdigest(),compatibility='connect specify helper wires and add 10 ps Q output delay to prevent zero-delay CTS delta races; no SDF timing validation')
    (dest/'alignment.json').write_text(json.dumps(report,indent=2));print('PASS gate replay',len(actual))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('--seed',type=int,default=751);a=p.parse_args();validate(a.directory,a.seed)
