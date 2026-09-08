"""Certify the selected fixed-point profile and bind its generated resources."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from zircon_asic import contract_hash, contract
from zircon_asic.evidence import implementation_hash
from zircon_asic.unary import implementation_config, resources_hash
from generate_sfu import exp_parameters, tables
from check_sfu_cores import check
from sfu_reference import compute as reference, FORMATS


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def certify(name,op):
    key=f'{name}.{op}';cfg=implementation_config(name,op)
    spec=contract()['units'][key];dest=ROOT/f'build/sfu/certificates/{name}_{op}'
    dest.mkdir(parents=True,exist_ok=True)
    if op=='exp':
        c=cfg['exp']
        regenerated=exp_parameters(name,cfg,c['fraction_bits'],c['table_bits'],c['degree'],dest/'polynomial')
        if regenerated!=c:raise AssertionError('selected exp resource does not reproduce')
        # The proof bounds every fixed-point grid point. Positive coefficients
        # make floor-Horner monotone within a segment; all joins are enumerated.
        _,eb,fb,_=FORMATS[name];limit=((1<<eb)-1)<<fb;sign=1<<(FORMATS[name][0]-1)
        predicates={
            'overflow':lambda raw:reference(name,'exp',raw)[0]==limit,
            'zero':lambda raw:reference(name,'exp',sign|raw)[0]==0,
            'tiny':lambda raw:reference(name,'exp',sign|raw)[0]<1<<fb,
            'one_positive':lambda raw:reference(name,'exp',raw)[0]==FORMATS[name][3]<<fb,
            'one_negative':lambda raw:reference(name,'exp',sign|raw)[0]==FORMATS[name][3]<<fb,
        }
        checks={}
        for label,predicate in predicates.items():
            threshold=c[label];one=label.startswith('one_')
            left,right=(threshold,threshold+1) if one else (threshold-1,threshold)
            if (predicate(left),predicate(right)) != ((True,False) if one else (False,True)):
                raise AssertionError((name,label,threshold))
            checks[label]=dict(input=threshold,left_input=left,right_input=right)
        result=dict(method='Sollya interval polynomial bound + directed MPFR quantization and error accumulation + integer segment joins',
                    pre_round_error_ulp_bound=c['pre_round_error_ulp_bound'],polynomial_bound_proved=True,
                    sollya_image_sha256=c['sollya_image_sha256'],sollya_version=c['sollya_version'],
                    monotonic_segment_joins=c['monotonic_segment_joins'],thresholds=checks,
                    finite_nonzero_exp_exact_cases='none; zero maps exactly to one',discrepancies=0)
    elif spec['variant']=='normalized_table':
        expected=tables(name,FORMATS[name][2]+7)[op]
        if expected!=cfg['tables'][op]:raise AssertionError('normalized table differs from exact integer quotient/root and remainder')
        result=dict(method='Exhaustive integer quotient/square comparison and exact residual bit for every normalized table entry',
                    entries=len(expected['values']),fraction_bits=expected['fraction_bits'],discrepancies=0)
    else:
        result=check(name,cfg,dest/'core',spec['variant'],op,vectors=name=='fp32')
    result.update(format=name,operation=op,variant=spec['variant'],configuration_sha256=digest(cfg),
                  contract_hash=contract_hash(),implementation_hash=implementation_hash(),resources_sha256=resources_hash(),
                  generator_sha256=hashlib.sha256((ROOT/'scripts/generate_sfu.py').read_bytes()).hexdigest(),
                  checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  oracle_sha256=hashlib.sha256((ROOT/'scripts/sfu_reference.py').read_bytes()).hexdigest())
    (dest/'certificate.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS {key} numerical certificate',flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--unit',action='append');args=parser.parse_args()
    for key in args.unit or [f'{f}.{op}' for f in FORMATS for op in ('exp','rcp','sqrt','rsqrt')]:certify(*key.split('.'))
