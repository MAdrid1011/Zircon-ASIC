"""Independent BF16 encoding-search oracle and shared Python/Numba/RTL vectors."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests'), str(ROOT/'scripts')]
from zircon_asic import FloatingPointUnit, Request, Rounding, contract_hash
from zircon_asic.evidence import implementation_hash
from rational_reference import compute as reference
from validate_rtl import run, verilator_configuration

OPS = ('add', 'mul', 'fma', 'div')
SEED = 11509


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stimulus(op):
    x = np.arange(65536, dtype=np.uint32)
    # Odd affine permutations cover all encodings in every operand position.
    sweep = np.column_stack((x, (x*40503+751)&65535, (x*25173+2026)&65535))
    rng = np.random.default_rng(SEED)
    random = rng.integers(0,65536,(20000,3),dtype=np.uint32)
    edge = [0,1,2,0x7f,0x80,0x81,0x3f00,0x3f7f,0x3f80,0x3f81,0x4000,
            0x7f7e,0x7f7f,0x7f80,0x7f81,0x7fbf,0x7fc0,0x7fff]
    edge += [x|0x8000 for x in edge]
    directed = [(a,b,c) for a in edge for b in edge for c in (edge if op=='fma' else [0])]
    directed += [(0x7f7f,0x4000,0xff7f),(0x3f81,0x3fc0,1),(1,1,0x8000)]
    # Cancellation at every exponent, tiny addends, and normalized divisors.
    directed += [(i,i^0x8000,1) for i in range(0,0x7f80,127)]
    if op == 'div': directed += [(0x3f80+a,0x3f80+b,0) for a in range(128) for b in range(128)]
    return np.concatenate((sweep,random,np.asarray(directed,dtype=np.uint32))), len(directed)


def vectors(op):
    out=ROOT/'build/bf16/numerical'/op; out.mkdir(parents=True,exist_ok=True)
    path=out/'vectors.bin'; metadata=out/'vectors.json'
    identity=dict(oracle_sha256=sha(ROOT/'tests/rational_reference.py'),generator_sha256=sha(__file__),seed=SEED,operation=op)
    if path.exists() and metadata.exists():
        saved=json.loads(metadata.read_text())
        if all(saved.get(k)==v for k,v in identity.items()) and saved.get('vectors_sha256')==sha(path):
            return np.fromfile(path,dtype='<u4').reshape(-1,6),saved,path
    inputs, directed=stimulus(op)
    rows=np.empty((5*len(inputs),6),dtype='<u4')
    start=time.monotonic()
    for rm in range(5):
        block=rows[rm*len(inputs):(rm+1)*len(inputs)]
        block[:,:3]=inputs; block[:,3]=rm
        for i,(a,b,c) in enumerate(inputs): block[i,4:]=reference('bf16',op,int(a),int(b),int(c),rm)
        print(f'{op}: independent reference rounding={rm} vectors={len(block)}',flush=True)
    rows.tofile(path)
    saved=dict(**identity,count=len(rows),encodings_per_operand_per_rounding=65536,
               random_per_rounding=20000,directed_per_rounding=directed,rounding_modes=5,
               vectors_sha256=sha(path),generation_seconds=time.monotonic()-start)
    metadata.write_text(json.dumps(saved,indent=2)+'\n')
    return rows,saved,path


def validate(op, rtl=False, rtl_dir=None):
    rows,record,path=vectors(op); u=FloatingPointUnit('bf16',op)
    for rm in range(5):
        block=rows[rows[:,3]==rm]; a,b,c=block[:,:3].T
        for backend in ('python','numba'):
            got=u.compute_batch(a,b,c,rounding=Rounding(rm),backend=backend)
            good=(got.bits==block[:,4]) & (got.flags==block[:,5]) & (got.remainder==0)
            if not np.all(good):
                i=int(np.flatnonzero(~good)[0])
                raise AssertionError((op,rm,backend,i,block[i].tolist(),int(got.bits[i]),int(got.flags[i])))
    record=dict(record,contract_hash=contract_hash(),implementation_hash=implementation_hash(),
                python_discrepancies=0,numba_discrepancies=0)
    if rtl:
        dest=Path(rtl_dir) if rtl_dir else ROOT/f'build/rtl/bf16_{op}'
        sv=dest/'Unit.sv'; top=re.search(r'^module (\w+)\(',sv.read_text(),re.M)[1]
        harness=dest/'numeric.cpp'; content=(ROOT/'scripts/bf16_numeric.cpp').read_text().replace('@TOP@',f'V{top}')
        if not harness.exists() or harness.read_text()!=content: harness.write_text(content)
        version,flags=verilator_configuration()
        build=dict(rtl_sha256=sha(sv),harness_sha256=sha(harness),verilator=version,compatibility_flags=flags)
        bid=dest/'numeric-build.json'; exe=dest/'numeric-obj/numeric'
        if not exe.exists() or not bid.exists() or json.loads(bid.read_text())!=build:
            # Verilator generated makefiles contain the absolute runtime include
            # directory. Regenerate the object tree after a toolchain update.
            shutil.rmtree(dest/'numeric-obj',ignore_errors=True)
            run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','-Wno-fatal',
                 '--top-module',top,'--Mdir',str(dest/'numeric-obj'),'-CFLAGS','-std=c++17',str(sv),str(harness),'-o','numeric'],dest/'numeric-compile.log')
            bid.write_text(json.dumps(build,indent=2)+'\n')
        run([str(exe),str(path)],dest/'numeric-run.log')
        record.update(build,rtl_discrepancies=0)
        (dest/'bf16-numerical.json').write_text(json.dumps(record,indent=2)+'\n')
    (path.parent/'validation.json').write_text(json.dumps(record,indent=2)+'\n')
    print(f'PASS BF16 {op}: {len(rows)} independent vectors'+(' Python/Numba/RTL' if rtl else ' Python/Numba'),flush=True)
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--op',choices=OPS,action='append');p.add_argument('--rtl',action='store_true')
    p.add_argument('--rtl-dir',type=Path);a=p.parse_args()
    for op in a.op or OPS: validate(op,a.rtl,a.rtl_dir)
