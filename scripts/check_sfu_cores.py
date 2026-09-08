"""Exhaust normalized input domains and certify the fixed correction distance."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def check(name='fp32',resource=None,destination=None,algorithm='newton',operation='all',vectors=False):
    data=resource or json.loads((ROOT/'src/zircon_asic/data/sfu.json').read_text())['formats'][name]
    cfg=data['refinement'];fb={'fp32':23,'fp16':10,'bf16':7}[name]
    dest=Path(destination) if destination else ROOT/f'build/sfu/cores/{name}'
    dest.mkdir(parents=True,exist_ok=True)
    code={'newton':0,'newton_residual':0,'goldschmidt':1,'nonrestoring':2,'reciprocal_digits':3}[algorithm]
    fields=[cfg['fraction_bits'],cfg['table_bits'],cfg['iterations'],fb,code,
            {'rcp':1,'rsqrt':2,'sqrt':4,'all':7}[operation]]+cfg['rcp_seed']+cfg['rsqrt_seed']
    (dest/'parameters.bin').write_bytes(struct.pack('<'+'Q'*len(fields),*fields))
    source=ROOT/'scripts/sfu_cores.cpp';exe=dest/'verify'
    subprocess.run(['c++','-O3','-std=c++17',str(source),'-o',str(exe)],check=True)
    if vectors and (name!='fp32' or operation=='all'):raise ValueError('normalized replay vectors need one FP32 operation')
    p=subprocess.run([str(exe),str(dest/'parameters.bin')]+([str(dest/'vectors.bin')] if vectors else []),text=True,capture_output=True)
    (dest/'run.log').write_text(p.stdout+p.stderr)
    if p.returncode:raise RuntimeError(p.stderr)
    result=json.loads(p.stdout)
    result.update(format=name,algorithm=algorithm,operation=operation,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  parameters_sha256=hashlib.sha256((dest/'parameters.bin').read_bytes()).hexdigest())
    if vectors:result['vectors_sha256']=hashlib.sha256((dest/'vectors.bin').read_bytes()).hexdigest()
    (dest/'proof.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    return result


if __name__=='__main__':
    for name in ('fp32','fp16','bf16'):check(name)
