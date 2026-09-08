"""Replay independently checked unary vectors through the generated RTL."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from validate_rtl import ROOT, run, verilator_configuration


def replay(name,op,directory=None):
    dest=Path(directory) if directory else ROOT/f'build/rtl/{name}_{op}'
    vectors=ROOT/f'build/sfu/numeric/{name}_{op}/vectors.bin'
    numeric=json.loads(vectors.with_name('numeric.json').read_text())
    if hashlib.sha256(vectors.read_bytes()).hexdigest()!=numeric['vectors_sha256']:
        raise RuntimeError('numerical vectors changed after validation')
    rtl=dest/'Unit.sv';top=re.search(r'^module (\w+)\(',rtl.read_text(),re.M)[1]
    harness=dest/'vectors.cpp';harness.write_text((ROOT/'scripts/sfu_vectors.cpp').read_text().replace('@TOP@','V'+top))
    version,flags=verilator_configuration()
    build_id=dict(rtl_sha256=hashlib.sha256(rtl.read_bytes()).hexdigest(),
                  harness_sha256=hashlib.sha256(harness.read_bytes()).hexdigest(),verilator=version)
    idpath=dest/'vectors-build.json';exe=dest/'vectors-obj/replay'
    if not exe.exists() or not idpath.exists() or json.loads(idpath.read_text())!=build_id:
        # Verilator caches absolute runtime-header paths in its generated
        # makefile, so a different Verilator version needs a fresh object tree.
        shutil.rmtree(dest/'vectors-obj',ignore_errors=True)
        run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','-Wno-fatal','--top-module',top,
             '--Mdir',str(dest/'vectors-obj'),str(rtl),str(harness),'-o','replay'],dest/'vectors-compile.log')
        idpath.write_text(json.dumps(build_id,indent=2)+'\n')
    latency=json.loads((dest/'manifest.json').read_text())['latency']
    run([str(exe),str(vectors),str(latency)],dest/'vectors-replay.log')
    normalized={}
    core=ROOT/f'build/sfu/certificates/{name}_{op}/core'
    if name=='fp32' and op!='exp':
        proof=json.loads((core/'proof.json').read_text())
        if hashlib.sha256((core/'vectors.bin').read_bytes()).hexdigest()!=proof.get('vectors_sha256'):
            raise RuntimeError('normalized numerical vectors changed')
        run([str(exe),str(core/'vectors.bin'),str(latency)],dest/'normalized-replay.log')
        normalized=dict(cases=proof['normalized_vectors'],vectors_sha256=proof['vectors_sha256'],discrepancies=0)
    report=dict(**numeric,**build_id,normalized=normalized,rtl_discrepancies=0)
    (dest/'unary-numeric.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS {name}.{op}: {numeric["cases"]} RTL vectors',flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--unit',action='append');a=p.parse_args()
    for key in a.unit or [f'{f}.{op}' for f in ('fp16','bf16') for op in ('exp','rcp','sqrt','rsqrt')]:
        replay(*key.split('.'))
