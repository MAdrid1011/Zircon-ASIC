"""MPFR numerical validation and compact replay vectors for unary RTL."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
import numpy as np
import gmpy2 as g

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from zircon_asic import FloatingPointUnit, Request, Rounding, contract_hash
from zircon_asic.evidence import implementation_hash
from zircon_asic.unary import resources_hash, implementation_config
from sfu_reference import compute as reference, FORMATS, decode, pack


def directed_inputs(op,seed=7181):
    values=set()
    def neighbors(raw,radius=2):
        for v in range(max(0,int(raw)-radius),min(0x7fffffff,int(raw)+radius)+1):
            values.add(v);values.add(v|0x80000000)
    for e in range(255):neighbors(e<<23)
    for raw in (0x7f800000,0x7fc00000,0x7fffffff):neighbors(raw)
    with g.context(g.get_context(),precision=256):
        if op=='exp':
            cfg=implementation_config('fp32','exp')['exp'];k=cfg['table_bits']
            for key in ('overflow','zero','tiny','one_positive','one_negative'):neighbors(cfg[key],4)
            for n in range(-151,129):
                for j in range(1<<k):
                    t=g.mpfr(n)+g.mpfr(j)/(1<<k) if k else g.mpfr(n)+g.mpfr('.5')
                    raw=pack('fp32',t*g.log(2),0)[0] if t else 0
                    neighbors(raw&0x7fffffff)
        else:
            rng=np.random.default_rng(seed)
            lower,upper=((-74,64) if op=='sqrt' else (-64,74) if op=='rsqrt' else (-126,126))
            for _ in range(4096):
                yraw=((int(rng.integers(lower,upper+1))+127)<<23)|int(rng.integers(1<<23))
                y=decode('fp32',yraw)[3];following=decode('fp32',yraw+1)[3]
                midpoint=(y+following)/2
                x=midpoint*midpoint if op=='sqrt' else 1/(midpoint*midpoint) if op=='rsqrt' else 1/midpoint
                neighbors(pack('fp32',x,0)[0])
            for q in range(1,1025):
                x=g.mpfr(q*q)
                neighbors(pack('fp32',x if op=='sqrt' else 1/x if op=='rsqrt' else g.mpfr(q),0)[0])
            for b in (6,7,8):
                for j in range(1<<b):neighbors(0x3f800000+(j<<(23-b)))
    return np.asarray(sorted(values),np.uint64)


def validate(name,op,count=1000000,seed=7181):
    identity=implementation_hash()
    unit=FloatingPointUnit(name,op);width=unit.format.width
    dest=ROOT/f'build/sfu/numeric/{name}_{op}';dest.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(seed)
    if width==16:a=np.arange(65536,dtype=np.uint64)
    else:
        a=rng.integers(0,1 << 32,count,dtype=np.uint64)
        if op=='exp':
            # Deterministic bit patterns for active-domain values. Host floats
            # generate stimulus only and are never an arithmetic oracle.
            active=rng.uniform(-103.5,88.7,(count+1)//2).astype(np.float32)
            a[:len(active)]=active.view(np.uint32)
        directed=directed_inputs(op,seed)
        a=np.concatenate((a,directed))
    started=time.monotonic();cases=0;different_rne=0
    path=dest/'vectors.bin'
    with path.open('wb') as output:
        for rm in ([0] if op=='exp' else range(5)):
            fast=unit.compute_batch(a,rounding=Rounding(rm),backend='numba')
            rows=np.zeros((len(a),4),dtype='<u4')
            for i,raw in enumerate(a):
                raw=int(raw);answer=unit.compute(Request(raw,rounding=Rounding(rm)))
                actual=(answer.bits,int(answer.flags));expected=reference(name,op,raw,rm)
                if actual != (int(fast.bits[i]),int(fast.flags[i])):
                    failure=dict(format=name,operation=op,seed=seed,input=raw,rounding=rm,python=actual,numba=[int(fast.bits[i]),int(fast.flags[i])])
                    (dest/'failure.json').write_text(json.dumps(failure,indent=2)+'\n');raise AssertionError(failure)
                if actual != expected:
                    if op!='exp' or actual[1]!=expected[1] or actual[0] not in (reference(name,op,raw,2)[0],reference(name,op,raw,3)[0]):
                        failure=dict(format=name,operation=op,seed=seed,input=raw,rounding=rm,actual=actual,reference=expected)
                        (dest/'failure.json').write_text(json.dumps(failure,indent=2)+'\n');raise AssertionError(failure)
                    different_rne+=1
                if op=='exp':
                    fb,eb=unit.format.fraction,unit.format.exponent
                    def category(bits):
                        mag=bits&((1<<(width-1))-1)
                        return 'nan' if mag>((1<<eb)-1)<<fb else 'inf' if mag==((1<<eb)-1)<<fb else 'zero' if not mag else 'subnormal' if mag<1<<fb else 'normal'
                    if category(actual[0])!=category(expected[0]):
                        failure=dict(format=name,operation=op,seed=seed,input=raw,reason='RNE category',actual=actual,reference=expected)
                        (dest/'failure.json').write_text(json.dumps(failure,indent=2)+'\n');raise AssertionError(failure)
                rows[i]=raw,answer.bits,int(answer.flags),rm
            output.write(rows.tobytes());cases+=len(a)
            print(f'{name}.{op} rounding={rm}: {len(a)} cases',flush=True)
    if op=='exp':
        sign=1<<(width-1);active=(a&(sign-1))<=unit.format.inf_bits
        inputs=a[active];values=fast.bits[active]
        order=np.argsort(np.where(inputs&sign,((1<<width)-1)-inputs,inputs|sign))
        values=values[order];ordered_inputs=inputs[order]
        if np.any(values[1:]<values[:-1]):
            i=int(np.flatnonzero(values[1:]<values[:-1])[0])
            failure=dict(format=name,operation=op,seed=seed,reason='monotonicity',inputs=ordered_inputs[i:i+2].tolist(),outputs=values[i:i+2].tolist())
            (dest/'failure.json').write_text(json.dumps(failure,indent=2)+'\n');raise AssertionError(failure)
    assert implementation_hash()==identity,'implementation changed during numerical validation'
    result=dict(format=name,operation=op,seed=seed,cases=cases,input_count=len(a),
                directed_inputs=len(directed) if width==32 else 65536,rounding_modes=1 if op=='exp' else 5,exhaustive_inputs=width==16,
                accuracy='faithful' if op=='exp' else 'correctly-rounded',oracle='MPFR 4.2.2 directed adaptive enclosures',
                discrepancies=0,exp_different_from_correct_rne=different_rne,elapsed_seconds=time.monotonic()-started,
                monotonicity='exhaustive' if op=='exp' and width==16 else 'sampled' if op=='exp' else None,
                contract_hash=contract_hash(),implementation_hash=implementation_hash(),resources_sha256=resources_hash(),
                oracle_sha256=hashlib.sha256((ROOT/'scripts/sfu_reference.py').read_bytes()).hexdigest(),
                generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                vectors_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (dest/'numeric.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS',name,op,cases,flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--unit',action='append');p.add_argument('--count',type=int,default=1000000)
    a=p.parse_args()
    for key in a.unit or [f'{f}.{op}' for f in FORMATS for op in ('exp','rcp','sqrt','rsqrt')]:
        validate(*key.split('.'),count=a.count)
