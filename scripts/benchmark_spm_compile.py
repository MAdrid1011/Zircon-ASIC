"""Measure a cold Numba compilation separately from steady-state benchmarks."""
import json,os,subprocess,sys,tempfile,time
from pathlib import Path

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='zircon-spm-numba-') as cache:
        env=dict(os.environ,NUMBA_CACHE_DIR=cache,PYTHONPATH=str(root/'src'))
        code='''import json,time,sys
sys.path.insert(0,"scripts")
from benchmark_spm import make
from zircon_asic.spm import spm_implementation_hash
from numba.core.event import install_timer
durations=[];n=make("mixed",128);start=time.perf_counter()
with install_timer("numba:compile",durations.append): n.run(128,backend="numba")
print(json.dumps(dict(implementation_hash=spm_implementation_hash(),fresh_cache=True,compile_seconds=sum(durations),cold_call_seconds=time.perf_counter()-start)))
'''
        r=subprocess.run([sys.executable,'-c',code],cwd=root,env=env,text=True,stdout=subprocess.PIPE,check=True)
        report=json.loads(r.stdout);assert report['compile_seconds']>0
        (root/'build/spm-compile-benchmark.json').write_text(json.dumps(report,indent=2)+'\n')
        print(report)
