"""Reproduce the bounded BF16 topology search and two-finalist physical selection."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import copy
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from zircon_asic import contract
from validate_rtl import validate
from validate_bf16 import validate as numerical
from ppa import run as physical


def configurations():
    for op,variants,depths in [('add',('single_path','near_far'),(3,4,6)),
                             ('mul',('direct_dadda','booth_dadda','native'),(3,4,7)),
                             ('fma',('fused_compressor','unrounded_product'),(4,5,8))]:
        for variant in variants:
            for depth in depths:
                phases={
                    ('add',3):['align','add_normalize','grs_round'],
                    ('add',4):['align','add_normalize','grs','round'],
                    ('add',6):['decode','align','add','round_normalize','grs','round'],
                    ('mul',3):['compress','product_normalize','grs_round'],
                    ('mul',4):['compress','product_normalize','grs','round'],
                    ('mul',7):['decode','compress_0','compress_1','product','round_normalize','grs','round'],
                    ('fma',4):['product_compress','align_compress','add_normalize','grs_round'],
                    ('fma',5):['product_compress','align_compress','add_normalize','grs','round'],
                    ('fma',8):['decode','compress','product_compress','align_compress','add','round_normalize','grs','round'],
                }[op,depth]
                if variant=='unrounded_product':
                    phases=['product' if p=='product_compress' else 'align' if p=='align_compress' else p for p in phases]
                yield op,variant,phases,0,0
    for variant,iterations,radix in [('radix2_nonrestoring',10,2),('radix4_srt',6,4),('radix16_srt',3,16)]:
        for overhead in (4,6):
            phases=['decode','normalize']+['iterate']*iterations+['correct']
            phases+=['round'] if overhead==4 else ['round_normalize','grs','round']
            yield 'div',variant,phases,iterations,radix


def prepare():
    result=[]
    for op,variant,phases,iterations,radix in configurations():
        c=copy.deepcopy(contract());item=c['units'][f'bf16.{op}'];depth=len(phases)
        item.update(latency=depth,phases=phases,variant=variant,stage_capacity=[1]*(1 if op=='div' else depth))
        if op=='div':item.update(iterations=iterations,radix=radix)
        dest=ROOT/f'build/bf16/candidates/{op}-{variant}-{depth}'
        dest.mkdir(parents=True,exist_ok=True);path=dest/'contract.json'
        content=json.dumps(c,indent=2)+'\n'
        if not path.exists() or path.read_text()!=content:path.write_text(content)
        result.append((op,dest,path))
    return result


def measurement(dest,stage):
    manifest=json.loads((dest/'manifest.json').read_text());sha=manifest['rtl_sha256']
    candidates=[]
    for p in (ROOT/'build/ppa').glob(f'bf16_{manifest["operation"]}_TC/{sha[:12]}*/physical.json'):
        d=json.loads(p.read_text());name,prefix=('6_report.json','finish') if stage=='finish' else ('5_1_grt.json','globalroute')
        m=d.get('metrics',{}).get(name,{})
        if not m:continue
        area=m.get(prefix+'__design__instance__area__stdcell')
        setup=m.get(prefix+'__timing__setup__ws',-float('inf'));hold=m.get(prefix+'__timing__hold__ws',-float('inf'))
        candidates.append(dict(directory=str(dest.relative_to(ROOT)),physical=str(p.relative_to(ROOT)),
          rtl_sha256=sha,variant=manifest['variant'],latency=manifest['latency'],area_um2=area,
          setup_slack_ps=setup,hold_slack_ps=hold,area_latency=area*manifest['latency'] if area else float('inf'),
          passed=d['exit_code']==0 and setup>=0 and hold>=0))
    return sorted(candidates,key=lambda r:(r['passed'],r['setup_slack_ps']),reverse=True)[0] if candidates else None


def best(rows):
    valid=[r for r in rows if r and r['passed']]
    if not valid:raise RuntimeError('no passing candidate; inspect physical critical paths')
    minimum=min(r['area_latency'] for r in valid)
    return min((r for r in valid if r['area_latency']<minimum*1.05),key=lambda r:(r['area_um2'],r['latency'],r['directory']))


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=['prepare','validate','screen','finalists','select'],default='screen')
    p.add_argument('--op',choices=['add','mul','fma','div']);p.add_argument('--jobs',type=int,default=2);args=p.parse_args()
    candidates=[r for r in prepare() if not args.op or r[0]==args.op]
    if args.stage=='prepare':return
    if args.stage in ('validate','screen'):
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures=[]
            for op,dest,path in candidates:
                validate('bf16',op,cycles=20000,contract_path=path,destination=dest)
                numerical(op,True,dest)
                if args.stage=='screen':futures.append(pool.submit(physical,f'bf16.{op}','TC','global_route',0,0,dest))
            failures=[]
            for f in futures:
                try:f.result()
                except Exception as e:failures.append(str(e))
            if failures:raise RuntimeError('\n'.join(failures))
    if args.stage=='finalists':
        selected=[]
        for op in sorted({r[0] for r in candidates}):
            rows=[measurement(dest,'global_route') for name,dest,_ in candidates if name==op]
            first=best(rows)
            selected.append(first)
            # A second finalist is useful for cross-checking a close PPA tradeoff,
            # but never promote a timing-failing implementation merely to fill a slot.
            remaining=[r for r in rows if r and r!=first and r['passed']]
            if remaining: selected.append(best(remaining))
        (ROOT/'build/bf16/finalists.json').write_text(json.dumps(selected,indent=2)+'\n')
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures=[pool.submit(physical,'bf16.'+Path(r['directory']).name.split('-')[0],'TC','finish',0,0,ROOT/r['directory']) for r in selected]
            for f in futures:f.result()
    if args.stage=='select':
        finalists=json.loads((ROOT/'build/bf16/finalists.json').read_text());chosen={}
        for op in sorted({r[0] for r in candidates}):
            rows=[measurement(ROOT/r['directory'],'finish') for r in finalists if Path(r['directory']).name.startswith(op+'-')]
            chosen[op]=best(rows)
        report=dict(rule='minimum area*latency; within 5% prefer smaller area then latency',selected=chosen,
                    screening=[measurement(d,'global_route') for _,d,_ in candidates],
                    finalists=[measurement(ROOT/r['directory'],'finish') for r in finalists])
        (ROOT/'build/bf16/selection.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(chosen,indent=2))


if __name__=='__main__':main()
