"""Persist the five-seed and long BF16 three-way cycle campaign."""
from pathlib import Path
import json
import shutil
from validate_rtl import validate,ROOT

SEEDS=(751,11509,2026,65537,104729)

if __name__=='__main__':
    for op in ('add','mul','fma','div'):
        reports=[]
        for seed,cycles in [(s,20000) for s in SEEDS]+[(751,100000)]:
            record=validate('bf16',op,seed=seed,cycles=cycles)
            src=ROOT/f'build/rtl/bf16_{op}'
            dest=src/f'campaign/{seed}-{cycles}';dest.mkdir(parents=True,exist_ok=True)
            for name in ('alignment.json','stimulus.txt','python.trace','numba.trace','rtl.trace','events.json'):
                shutil.copyfile(src/name,dest/name)
            reports.append(record)
        (src/'bf16-campaign.json').write_text(json.dumps(reports,indent=2)+'\n')
