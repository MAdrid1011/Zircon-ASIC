"""Numerically gated, finite ASIC search for unary implementations.

Every retained table/algorithm keeps its first two certified internal widths.
Candidate contracts and results are isolated from the default public profile.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import gmpy2 as g
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from generate_sfu import polynomial, exp_parameters
from check_sfu_cores import check
from sfu_reference import FORMATS
from validate_rtl import validate, run
from ppa import run as physical

BASE=ROOT/'build/sfu/candidates'


def phases(op,variant,cfg):
    out=['decode'];ms=cfg.get('multiply_stages',1)
    def mult(name):out.extend([f'{name}_compress_{i}' for i in range(ms-1)]+[name])
    fb=cfg['tables']['rcp']['fraction_bits']-4 if 'tables' in cfg else 23
    q=fb+4
    if op=='exp':
        c=cfg['exp'];mult('constant_product')
        if cfg.get('range_stages',1)==2:out.append('range_shift')
        out.append('range_reduce')
        if c['table_bits']:out.append('lookup')
        for i in range(c['degree']):mult(f'horner_{i}')
        if c['table_bits']:mult('table_product')
    elif variant=='normalized_table':out.append('lookup')
    elif variant in ('nonrestoring','reciprocal_digits'):
        step=cfg.get('root_bits_per_stage',2)
        if variant=='nonrestoring':out.append('radicand')
        if variant=='reciprocal_digits' and cfg.get('division_lookahead'):out.append('divisor')
        out.extend(f'root_{i}' for i in range((q+step)//step))
        out.append('root_correct')
    else:
        out.append('seed');gold=variant=='goldschmidt'
        if gold:mult('gold_seed')
        for i in range(cfg['refinement']['iterations']):
            if op!='rcp' and not gold:mult(f'square_{i}')
            if gold and op=='rcp':out.append(f'factor_{i}')
            else:mult(f'my_{i}')
            mult(f'update_{i}')
        if op=='sqrt' and not gold:mult('root_product')
        out.append('candidate')
        if op=='rsqrt' and ms>1:mult('candidate_square')
        mult('residual')
        if op=='rsqrt' and ms>1:mult('delta')
        out.append('correct')
    return out+['normalize','grs','round']



def prepare(units=None):
    BASE.mkdir(parents=True,exist_ok=True)
    original=json.loads((ROOT/'src/zircon_asic/data/contract.json').read_text())
    data=json.loads((ROOT/'src/zircon_asic/data/sfu.json').read_text())['formats']
    entries=[];rejected=[]
    def keep(name,op,variant,cfg,label):
        path=BASE/f'{name}_{op}_{label}';path.mkdir(parents=True,exist_ok=True)
        c=copy.deepcopy(original);ps=phases(op,variant,cfg)
        c['sfu_overrides']={name:cfg}
        c['units'][f'{name}.{op}'].update(latency=len(ps),kind='elastic',phases=ps,variant=variant,stage_capacity=[1]*len(ps))
        (path/'contract.json').write_text(json.dumps(c,indent=2)+'\n')
        entries.append(dict(format=name,operation=op,variant=variant,latency=len(ps),directory=str(path.relative_to(ROOT))))
    for name,(_,_,fb,_) in FORMATS.items():
        for op in ('exp','rcp','sqrt','rsqrt'):
            if units and f'{name}.{op}' not in units:continue
            if op=='exp':
                for k in ((5,6,7,0) if name=='fp32' else (4,5,6,0)):
                    degrees=(5,6) if k==0 and name=='fp32' else (3,4) if k==0 else (2,3,4) if name=='fp32' else (2,3)
                    for degree in degrees:
                        kept=0
                        for f in (fb+7,fb+9,fb+11,fb+13):
                            label=f'k{k}_d{degree}_f{f}'
                            try:exp=exp_parameters(name,data[name],f,k,degree,BASE/'proofs'/name/label)
                            except ArithmeticError as e:
                                rejected.append(dict(format=name,operation=op,candidate=label,reason=str(e)));continue
                            cfg=copy.deepcopy(data[name]);cfg['exp']=exp;cfg['multiply_stages']=2 if name=='fp32' else 1
                            keep(name,op,'polynomial' if k==0 else 'table_polynomial',cfg,label)
                            kept+=1
                            if kept==2:break
                continue
            if name!='fp32':keep(name,op,'normalized_table',copy.deepcopy(data[name]),'table')
            if op=='sqrt' or op=='rcp':
                variant='nonrestoring' if op=='sqrt' else 'reciprocal_digits'
                for step in (1,2,3):
                    cfg=copy.deepcopy(data[name]);cfg['root_bits_per_stage']=step
                    keep(name,op,variant,cfg,f'digits{step}')
            for algorithm in (('newton_residual','goldschmidt') if name=='fp32' else ('newton_residual',)):
                for b in ((6,7,8) if name=='fp32' else (4,5,6)):
                    kept=0
                    for f in (fb+5,fb+7,fb+9,fb+11):
                        cfg=copy.deepcopy(data[name]);cfg['multiply_stages']=2 if name=='fp32' else 1
                        r=cfg['refinement'];r.update(fraction_bits=f,table_bits=b)
                        size=1 << b
                        with g.context(g.get_context(),precision=256):
                            r['rcp_seed']=[int(g.floor((1 << f)/(1+(g.mpfr(i)+.5)/size))) for i in range(size)]
                            r['rsqrt_seed']=[int(g.floor((1 << f)/g.sqrt((1+(g.mpfr(i)+.5)/size)*(1 << parity)))) for parity in range(2) for i in range(size)]
                        label=f'{algorithm}_b{b}_f{f}'
                        try:check(name,cfg,BASE/'proofs'/name/f'{op}_{label}',algorithm,op)
                        except RuntimeError as e:
                            rejected.append(dict(format=name,operation=op,candidate=label,reason=str(e)));continue
                        keep(name,op,algorithm,cfg,label);kept+=1
                        if kept==2:break
    (BASE/'index.json').write_text(json.dumps(entries,indent=2)+'\n')
    (BASE/'rejected.json').write_text(json.dumps(rejected,indent=2)+'\n')
    print(f'{len(entries)} retained, {len(rejected)} numerically rejected',flush=True)
    return entries


def measure(entry,stage):
    dest=ROOT/entry['directory']
    if not (dest/'manifest.json').exists():return None
    manifest=json.loads((dest/'manifest.json').read_text())
    sha=manifest['rtl_sha256'];folder=ROOT/f'build/ppa/{entry["format"]}_{entry["operation"]}_TC'
    filename,prefix={'synth':('1_1_yosys_stat.json','synth'),'global_route':('5_1_grt.json','globalroute'),'finish':('6_report.json','finish')}[stage]
    measurements=[]
    for path in sorted(folder.glob(sha[:12]+'*/physical.json')):
        d=json.loads(path.read_text());m=d.get('metrics',{}).get(filename,{})
        if stage=='synth':
            # ORFS records mapped synthesis metrics in its step metadata.
            merged={k:v for record in d.get('metrics',{}).values() for k,v in record.items()}
            area=merged.get('synth__design__instance__area__stdcell')
        else:area=m.get(prefix+'__design__instance__area__stdcell')
        if area is None:continue
        setup=m.get(prefix+'__timing__setup__ws');hold=m.get(prefix+'__timing__hold__ws')
        measurements.append(dict(entry,rtl_sha256=sha,physical=str(path.relative_to(ROOT)),area_um2=area,
                    area_latency=area*entry['latency'],setup_slack_ps=setup,hold_slack_ps=hold,
                    setup_margin=d.get('setup_repair_margin_ps',0),hold_margin=d.get('hold_repair_margin_ps',0),
                    passed=d['exit_code']==0 and (stage=='synth' or (setup is not None and hold is not None and min(setup,hold)>=0))))
    return min(measurements,key=lambda r:(not r['passed'],r['area_latency'])) if measurements else None


def retime(entries,multiply_stages=None,range_stages=None,exactness_classification=False,ready_topology=None,root_lookahead=False,prefix_factor=False,division_lookahead=False):
    all_entries=json.loads((BASE/'index.json').read_text())
    known=set()
    def identity(e,cfg):return (e['format'],e['operation'],e['variant'],json.dumps(cfg,sort_keys=True))
    for e in all_entries:
        c=json.loads((ROOT/e['directory']/'contract.json').read_text());known.add(identity(e,c['sfu_overrides'][e['format']]))
    added=[]
    for e in entries:
        if range_stages is not None and e['operation']!='exp':continue
        if exactness_classification and e['operation'] not in ('rcp','rsqrt'):continue
        if root_lookahead and e['variant']!='nonrestoring':continue
        if division_lookahead and e['variant']!='reciprocal_digits':continue
        if prefix_factor and e['variant'] not in ('newton_residual','goldschmidt'):continue
        if e['variant'] in ('normalized_table','nonrestoring','reciprocal_digits') and ready_topology is None and not root_lookahead and not division_lookahead:continue
        c=json.loads((ROOT/e['directory']/'contract.json').read_text());cfg=c['sfu_overrides'][e['format']]
        if multiply_stages is not None:cfg['multiply_stages']=multiply_stages
        if range_stages is not None:cfg['range_stages']=range_stages
        if exactness_classification:cfg['exactness_classification']=True
        if ready_topology is not None:cfg['ready_topology']=ready_topology
        if root_lookahead:
            cfg['root_lookahead']=True;cfg['root_bits_per_stage']=2
        if prefix_factor:cfg['prefix_factor']=True
        if division_lookahead:cfg['division_lookahead']=True;cfg['root_bits_per_stage']=2
        if identity(e,cfg) in known:continue
        known.add(identity(e,cfg))
        suffix=f'_m{cfg.get("multiply_stages",1)}_r{cfg.get("range_stages",1)}'
        if cfg.get('exactness_classification'):suffix+='_exact'
        if cfg.get('ready_topology')=='suffix_tree':suffix+='_tree'
        if cfg.get('root_lookahead'):suffix+='_lookahead'
        if cfg.get('prefix_factor'):suffix+='_prefix'
        if cfg.get('division_lookahead'):suffix+='_divlookahead'
        dest=ROOT/(e['directory']+suffix);dest.mkdir(exist_ok=True)
        ps=phases(e['operation'],e['variant'],cfg)
        c['units'][f'{e["format"]}.{e["operation"]}'].update(phases=ps,latency=len(ps),stage_capacity=[1]*len(ps))
        (dest/'contract.json').write_text(json.dumps(c,indent=2)+'\n')
        added.append(dict(e,directory=str(dest.relative_to(ROOT)),latency=len(ps)))
    (BASE/'index.json').write_text(json.dumps(all_entries+added,indent=2)+'\n')
    print(f'{len(added)} retimed candidates added',flush=True)


def best(rows):
    valid=[r for r in rows if r and r['passed']]
    if not valid:raise RuntimeError('no passing unary candidate')
    minimum=min(r['area_latency'] for r in valid)
    return min((r for r in valid if r['area_latency']<minimum*1.05),key=lambda r:(r['area_um2'],r['latency'],r['directory']))


def promote():
    from generate_sfu import generate
    chosen=json.loads((BASE/'selection.json').read_text())['selected']
    expected={f'{name}.{op}' for name in FORMATS for op in ('exp','rcp','sqrt','rsqrt')}
    if set(chosen)!=expected:raise RuntimeError('promotion requires all twelve selected physical configurations')
    data_path=ROOT/'src/zircon_asic/data/sfu.json';data=json.loads(data_path.read_text());selected={}
    for key,e in chosen.items():
        measured=measure(e,'finish')
        if not measured or not measured['passed'] or measured['rtl_sha256']!=e['rtl_sha256']:
            raise RuntimeError(f'{key}: detailed-route measurement changed')
        physical_path=ROOT/measured['physical'];physical_data=json.loads(physical_path.read_text())
        audit_path=physical_path.parent/'unary-checks.json'
        audit=json.loads(audit_path.read_text()) if audit_path.exists() else {}
        if (not audit.get('passed') or audit.get('rtl_sha256')!=e['rtl_sha256']
            or audit.get('configuration_sha256')!=physical_data['configuration_sha256']):
            raise RuntimeError(f'{key}: extracted setup/hold, clock, constraints and routing audit required')
        directory=ROOT/e['directory'];c=json.loads((directory/'contract.json').read_text())
        a=json.loads((directory/'alignment.json').read_text())
        if a.get('rtl_sha256')!=e['rtl_sha256'] or a.get('cycle_discrepancy')!=0 or a.get('numba_cycle_discrepancy')!=0:
            raise RuntimeError(f'{key}: candidate three-way alignment required')
        selected[key]=dict(variant=e['variant'],phases=c['units'][key]['phases'],configuration=c['sfu_overrides'][e['format']])
    data['implementations']=selected
    data_path.write_text(json.dumps(data,indent=2)+'\n')
    generate()
    print('Selected physical profiles written to the common contract; qualification is collected from final regressions.')


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','generate','validate','synth','screen','retime','repair','finalists','select','promote'])
    p.add_argument('--unit',action='append');p.add_argument('--directory',action='append');p.add_argument('--jobs',type=int,default=2)
    p.add_argument('--missing',action='store_true');p.add_argument('--multiply-stages',type=int,choices=[1,2,3,4])
    p.add_argument('--range-stages',type=int,choices=[1,2]);p.add_argument('--setup-margin',type=int,default=30)
    p.add_argument('--finalists-per-unit',type=int,choices=[1,2],default=2)
    p.add_argument('--exactness-classification',action='store_true')
    p.add_argument('--ready-topology',choices=['suffix_tree'])
    p.add_argument('--root-lookahead',action='store_true')
    p.add_argument('--prefix-factor',action='store_true')
    p.add_argument('--division-lookahead',action='store_true')
    a=p.parse_args()
    if a.stage=='promote':promote();return
    if a.stage=='prepare':prepare(a.unit);return
    entries=json.loads((BASE/'index.json').read_text())
    if a.unit:entries=[v for v in entries if f'{v["format"]}.{v["operation"]}' in a.unit]
    if a.directory:entries=[v for v in entries if v['directory'] in set(a.directory)]
    if a.stage=='retime':retime(entries,a.multiply_stages,a.range_stages,a.exactness_classification,a.ready_topology,a.root_lookahead,a.prefix_factor,a.division_lookahead);return
    if a.stage=='generate':
        if a.missing:entries=[e for e in entries if not (ROOT/e['directory']/'manifest.json').exists()]
        index=BASE/'generate-index.json';index.write_text(json.dumps([dict(e,directory=str(ROOT/e['directory'])) for e in entries]))
        run(['sbt',f'runMain zircon.GenerateUnaryCandidates {index}'],BASE/'generate.log',ROOT/'hardware')
        return
    if a.stage=='validate':
        for e in entries:
            dest=ROOT/e['directory']
            if a.missing and (dest/'alignment.json').exists() and (dest/'manifest.json').exists():
                if json.loads((dest/'alignment.json').read_text()).get('rtl_sha256')==json.loads((dest/'manifest.json').read_text())['rtl_sha256']:continue
            validate(e['format'],e['operation'],cycles=3000,contract_path=dest/'contract.json',destination=dest,use_existing=True)
        return
    if a.stage in ('synth','screen','repair'):
        target='synth' if a.stage=='synth' else 'global_route'
        if a.stage=='repair':
            entries=[e for e in entries if (r:=measure(e,'global_route')) and not r['passed'] and r['setup_slack_ps'] is not None and -50<r['setup_slack_ps']<0]
        if a.missing:entries=[e for e in entries if measure(e,target) is None]
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            futures={pool.submit(physical,f'{e["format"]}.{e["operation"]}','TC',target,a.setup_margin if a.stage=='repair' else 0,0,ROOT/e['directory']):e for e in entries}
            failed=[]
            for future in as_completed(futures):
                try:future.result()
                except Exception as error:failed.append(dict(candidate=futures[future],error=str(error)))
        (BASE/f'{a.stage}-failures.json').write_text(json.dumps(failed,indent=2)+'\n')
        if failed:raise RuntimeError(f'{len(failed)} flow failures')
        return
    grouped={}
    for e in entries:grouped.setdefault(f'{e["format"]}.{e["operation"]}',[]).append(e)
    if a.stage=='finalists':
        selected=[]
        for key,group in grouped.items():
            rows=[measure(e,'global_route') for e in group];first=best(rows);selected.append(first)
            remaining=[r for r in rows if r and r['passed'] and r['directory']!=first['directory']]
            if a.finalists_per_unit == 2 and remaining:selected.append(best(remaining))
        path=BASE/'finalists.json';previous=json.loads(path.read_text()) if path.exists() else []
        previous=[e for e in previous if f'{e["format"]}.{e["operation"]}' not in grouped]
        path.write_text(json.dumps(previous+selected,indent=2)+'\n')
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            futures=[pool.submit(physical,f'{e["format"]}.{e["operation"]}','TC','finish',e.get('setup_margin',0),e.get('hold_margin',0),ROOT/e['directory']) for e in selected]
            for future in futures:future.result()
        return
    finalists=json.loads((BASE/'finalists.json').read_text());path=BASE/'selection.json'
    chosen=json.loads(path.read_text())['selected'] if path.exists() else {}
    for key in grouped:
        chosen[key]=best([measure(e,'finish') for e in finalists if f'{e["format"]}.{e["operation"]}'==key])
        from check_bf16_physical import check as audit
        audit((ROOT/chosen[key]['physical']).parent,'unary-checks.json')
    (BASE/'selection.json').write_text(json.dumps(dict(rule='minimum area*latency; within 5% smaller area then latency',selected=chosen),indent=2)+'\n')
    print(json.dumps(chosen,indent=2))


if __name__=='__main__':main()
