"""Inspect local distributions and exercise an isolated wheel import."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def check():
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    wheel=next((ROOT/'dist').glob(f'zircon_asic-{version}-*.whl'))
    sdist=ROOT/f'dist/zircon_asic-{version}.tar.gz'
    jar=ROOT/f'hardware/target/scala-2.13/zircon-asic_2.13-{version}.jar'
    resources={name:(ROOT/'src/zircon_asic/data'/f'{name}.json').read_bytes() for name in ('contract','spm','sfu')}
    with zipfile.ZipFile(wheel) as w,tarfile.open(sdist) as s,zipfile.ZipFile(jar) as j:
        lists=[w.namelist(),s.getnames(),j.namelist()]
        for names in lists:
            assert not any('WORK_LOG' in name.upper() for name in names),'development log in distribution'
            assert any(name.endswith('/LICENSE') or name=='LICENSE' for name in names)
            assert any(name.endswith('THIRD_PARTY_NOTICES.md') for name in names)
            assert any(name.endswith('licenses/ASAP7.txt') for name in names)
        for name,data in resources.items():
            assert w.read(f'zircon_asic/data/{name}.json')==data
            assert s.extractfile(f'zircon_asic-{version}/src/zircon_asic/data/{name}.json').read()==data
            assert j.read(f'zircon-{name}.json')==data
        contract=json.loads(resources['contract']);sha=hashlib.sha256(resources['sfu']).hexdigest()
        unary=[v for v in contract['units'].values() if v.get('arity')==1]
        assert len(unary)==12 and all(v['resources_sha256']==sha for v in unary)
        for name in ('exp','rcp','rsqrt','sqrt'):
            assert f'zircon_asic-{version}/docs/hardware/{name}.md' in s.getnames()
        with tempfile.TemporaryDirectory(prefix='zircon-wheel-') as folder:
            w.extractall(folder)
            # Reject optional imports to test the complete NumPy-only path even
            # on a developer machine that already has Numba installed.
            script='''import builtins, json, sys
real_import=builtins.__import__
def guarded(name,*args,**kwargs):
    if name=='numba' or name.startswith('numba.'):raise ImportError('optional dependency deliberately unavailable')
    return real_import(name,*args,**kwargs)
builtins.__import__=guarded
import zircon_asic as z
assert z.__version__==sys.argv[1]
for name in ('fp32','fp16','bf16'):
    for op in ('exp','rcp','sqrt','rsqrt'):
        u=z.FloatingPointUnit(name,op);one=u.format.bias<<u.format.fraction
        request=z.Request(one)
        answer=u.compute(request)
        assert u.compute_batch([one],backend='auto').bits[0]==answer.bits
        assert u.step(z.Inputs(request)).accepted
        for _ in range(u.timing.latency):out=u.step()
        assert out.delivered and out.response==answer
net=z.Network().add('u',z.FP32Exp()).source('u',[z.Request(0)]).sink('u')
net.run(100,backend='python')
assert net.sinks['u'].received[0][1].bits==0x3f800000
assert 'numba' not in sys.modules
print('PASS isolated wheel: twelve scalar, batch and cycle units without Numba')
'''
            env=dict(os.environ,PYTHONPATH=folder)
            r=subprocess.run([sys.executable,'-c',script,version],cwd=folder,env=env,text=True,capture_output=True)
            if r.returncode:raise RuntimeError(r.stdout+r.stderr)
            print(r.stdout.strip())
    result=dict(version=version,artifacts={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (wheel,sdist,jar)},
                resources_sha256={name:hashlib.sha256(data).hexdigest() for name,data in resources.items()},
                wheel_sdist_jar_resources_equal=True,licenses_present=True,numpy_only_runtime=True)
    (ROOT/'build/package-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS wheel, sdist and JAR resources, licenses and source documentation')
    return result


if __name__=='__main__':check()
