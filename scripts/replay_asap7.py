"""Replay verified cycle and numerical vectors on the routed ASAP7 netlist."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from ppa import ROOT, IMAGE
from validate_rtl import run, verilator_configuration


def models():
    directory=ROOT/'build/asap7-functional'
    directory.mkdir(exist_ok=True)
    model=directory/'cells.v';record=directory/'models.json'
    if record.exists() and model.exists():
        d=json.loads(record.read_text())
        if (d['image']==IMAGE and d['model_sha256']==hashlib.sha256(model.read_bytes()).hexdigest()
            and d.get('license_sha256')==hashlib.sha256((ROOT/'licenses/ASAP7.txt').read_bytes()).hexdigest()):return model,d
    script="""source /OpenROAD-flow-scripts/env.sh
: > /workspace/build/asap7-functional/models.ys
: > /workspace/build/asap7-functional/libraries.sha256
for lib in /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/*_RVT_TT_*.lib*; do
  printf 'read_liberty -ignore_miss_func %s\\n' "$lib" >> /workspace/build/asap7-functional/models.ys
  sha256sum "$lib" >> /workspace/build/asap7-functional/libraries.sha256
done
printf 'write_verilog -noattr /workspace/build/asap7-functional/cells.v\\n' >> /workspace/build/asap7-functional/models.ys
yosys -Q -T -s /workspace/build/asap7-functional/models.ys
"""
    run(['docker','run','--rm','--platform','linux/amd64','-v',f'{ROOT}:/workspace',IMAGE,'bash','-lc',script],directory/'generate.log')
    libraries={Path(line.split()[1]).name:line.split()[0] for line in (directory/'libraries.sha256').read_text().splitlines()}
    notice='\n'.join('// '+line for line in (ROOT/'licenses/ASAP7.txt').read_text().splitlines())+'\n'
    model.write_text(notice+model.read_text())
    d=dict(image=IMAGE,model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),libraries=libraries,
           license_sha256=hashlib.sha256((ROOT/'licenses/ASAP7.txt').read_bytes()).hexdigest())
    record.write_text(json.dumps(d,indent=2)+'\n')
    return model,d


def replay(directory,numeric=True):
    physical=Path(directory).resolve();p=json.loads((physical/'physical.json').read_text())
    name,op=p['unit'].split('.');rtl=ROOT/f'build/rtl/{name}_{op}'
    if hashlib.sha256((rtl/'Unit.sv').read_bytes()).hexdigest()!=p['rtl_sha256']:
        raise RuntimeError('physical and verified RTL differ')
    netlist=physical/'results/6_final.v';dest=physical/'gate';dest.mkdir(exist_ok=True)
    cellmodels,library=models();version,flags=verilator_configuration()
    top=re.search(r'^module (\w+)\s*\(',netlist.read_text(),re.M)[1]
    netsha=hashlib.sha256(netlist.read_bytes()).hexdigest()
    common=['verilator',*flags,'--cc','--exe','--build','-j','4','-Wno-fatal','--top-module',top,
            '-CFLAGS','-std=c++17',str(netlist),str(cellmodels)]
    source=dest/'trace.cpp';source.write_text((ROOT/'scripts/rtl_trace.cpp').read_text().replace('@TOP@','V'+top))
    shutil.rmtree(dest/'obj',ignore_errors=True)
    run([*common,'--Mdir',str(dest/'obj'),str(source),'-o','trace'],dest/'compile.log')
    reports=[]
    for campaign in sorted((rtl/'campaign').iterdir()):
        if not (campaign/'alignment.json').exists():continue
        evidence=json.loads((campaign/'alignment.json').read_text())
        if evidence['rtl_sha256']!=p['rtl_sha256']:raise RuntimeError('cycle campaign RTL mismatch')
        output=dest/(campaign.name+'.trace')
        run([str(dest/'obj/trace'),str(campaign/'stimulus.txt'),str(output)],dest/(campaign.name+'.log'))
        expected=(campaign/'rtl.trace').read_text().splitlines();actual=output.read_text().splitlines()
        assert len(actual)==len(expected)
        for cycle,(a,b) in enumerate(zip(expected,actual)):
            gold,gate=list(map(int,a.split())),list(map(int,b.split()))
            fields=[0,1,6,7,8,9]+([2,3,4,5] if gold[1] else [])
            if any(gold[i]!=gate[i] for i in fields):
                failure=dict(cycle=cycle,seed=evidence['seed'],rtl=gold,gate=gate,netlist_sha256=netsha)
                (dest/'failure.json').write_text(json.dumps(failure,indent=2));raise AssertionError(failure)
        reports.append(dict(seed=evidence['seed'],cycles=len(actual),discrepancy_cycles=0,
                            stimulus_sha256=hashlib.sha256((campaign/'stimulus.txt').read_bytes()).hexdigest(),
                            gate_trace_sha256=hashlib.sha256(output.read_bytes()).hexdigest()))
    if len(reports)<6:raise RuntimeError('five fixed seeds and the long cycle replay are required')
    numeric_evidence={}
    if numeric:
        source=dest/'vectors.cpp';source.write_text((ROOT/'scripts/sfu_vectors.cpp').read_text().replace('@TOP@','V'+top))
        shutil.rmtree(dest/'vectors-obj',ignore_errors=True)
        run([*common,'--Mdir',str(dest/'vectors-obj'),str(source),'-o','replay'],dest/'vectors-compile.log')
        vectors=ROOT/f'build/sfu/numeric/{name}_{op}/vectors.bin'
        numeric_evidence=json.loads(vectors.with_name('numeric.json').read_text())
        if hashlib.sha256(vectors.read_bytes()).hexdigest()!=numeric_evidence['vectors_sha256']:raise RuntimeError('numeric vectors changed')
        run([str(dest/'vectors-obj/replay'),str(vectors),str(p['latency'])],dest/'vectors.log')
        numeric_evidence=dict(cases=numeric_evidence['cases'],vectors_sha256=numeric_evidence['vectors_sha256'],discrepancies=0)
    result=dict(method='RTL/routed-netlist trace equivalence with functional Liberty models',
                rtl_sha256=p['rtl_sha256'],netlist_sha256=netsha,verilator=version,library=library,
                campaigns=reports,numerical=numeric_evidence,discrepancy_cycles=0)
    (dest/'equivalence.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS {name}.{op} routed-netlist equivalence: {sum(r["cycles"] for r in reports)} cycles',flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--cycles-only',action='store_true')
    args=parser.parse_args();replay(args.directory,not args.cycles_only)
