"""Generate fixed-point constants, normalized tables and exp category thresholds.

Run with the development Python environment containing gmpy2. Sollya 8.0 is
provided by scripts/sollya.Dockerfile; it is never a runtime package dependency.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import subprocess
import sys
from functools import cache
import gmpy2 as g
from sfu_reference import FORMATS, compute

ROOT = Path(__file__).resolve().parents[1]


@cache
def sollya_image():
    return subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','zircon-asic-sollya:8.0'],text=True).strip()


def constant(enclose, fraction, nearest=True):
    """Quantize an MPFR-enclosed positive constant, resolving both endpoints."""
    precision=128
    while precision<=4096:
        answers=[]
        for direction in (g.RoundDown,g.RoundUp):
            with g.context(g.get_context(),precision=precision,round=direction):
                value=g.mul_2exp(enclose(),fraction)
                q=int(g.floor(value));tail=value-q
                answers.append(q+int(nearest and (tail>g.mpfr('.5') or (tail==g.mpfr('.5') and q&1))))
        if answers[0]==answers[1]:return answers[0]
        precision*=2
    raise ArithmeticError('unresolved constant quantization interval')


def polynomial(f, k, degree, output, bound_exponent=None):
    exponent=f-2 if bound_exponent is None else bound_exponent
    function='2^(x-1/2)' if k == 0 else '2^x'
    script = f'''prec=256!;
display=decimal!;
p=fpminimax({function},{degree},[|{','.join([str(f)]*(degree+1))}|],[0;1/2^{k}],fixed,absolute);
for i from 0 to {degree} do print("COEFF ",round(coeff(p,i)*2^{f},128,RN));
b=2^(-{exponent});
print("PROVED ",checkinfnorm(p-({function}),[0;1/2^{k}],b));
print("BOUND ",b);
quit;
'''
    output.mkdir(parents=True, exist_ok=True)
    (output/'polynomial.sollya').write_text(script)
    p = subprocess.run(['docker','run','--rm','--platform','linux/amd64','-i','zircon-asic-sollya:8.0'],
                       input=script,text=True,capture_output=True)
    (output/'sollya.txt').write_text(p.stdout+p.stderr)
    coefficients = [int(g.mpfr(line.split()[1])) for line in p.stdout.splitlines() if line.startswith('COEFF ')]
    if len(coefficients) != degree+1 or 'PROVED true' not in ' '.join(p.stdout.split()):
        raise ArithmeticError(f'Sollya certificate failed: {output}')
    if min(coefficients) < 0: raise ArithmeticError('positive monotonic Horner coefficients required')
    return coefficients, dict(sollya_version='8.0',sollya_image_sha256=sollya_image(),script_sha256=hashlib.sha256(script.encode()).hexdigest(),
                             polynomial_error_bound=f'2^-{exponent}', polynomial_bound_proved=True)


def first(n, predicate):
    lo, hi = 0, n
    while lo < hi:
        mid = (lo+hi)//2
        if predicate(mid): hi = mid
        else: lo = mid+1
    return lo


def tables(name, f):
    _, _, fb, _ = FORMATS[name]
    q = fb+4
    result = {}
    for op in ('rcp','sqrt','rsqrt'):
        rows = []
        for parity in range(1 if op == 'rcp' else 2):
            for frac in range(1 << fb):
                m = ((1 << fb)+frac) << parity
                if op == 'rcp':
                    n, d = 1 << (q+fb), m
                    value, tail = divmod(n,d)
                else:
                    n, d = (m << (2*q-fb), 1) if op == 'sqrt' else (1 << (2*q+fb), m)
                    value = int(g.isqrt(n//d)); tail = n-value*value*d
                rows.append((value << 1) | int(tail != 0))
        result[op] = dict(fraction_bits=q, values=rows)
    return result


def exp_parameters(name,base,f,k,degree,directory):
    fb=FORMATS[name][2]
    coeff,proof=polynomial(f,k,degree,directory,fb+5)
    with g.context(g.get_context(),precision=256,round=g.RoundUp):
        u=g.mpfr(2)**(-f);h=g.mpfr(2)**(-k)
        poly_error=g.mpfr(2)**(-(fb+5))
        evaluation=u*sum(h**i for i in range(degree))
        reduction=g.mpfr(128)*g.mpfr(2)**(-(f+9))+u
        maximum=g.sqrt(2) if k==0 else g.mpfr(2)
        err=maximum*(g.exp2(reduction)-1)
        if k:
            err+=2*(poly_error+evaluation)+(g.exp2(h)+poly_error+evaluation)*u/2+u
        else:err+=poly_error+evaluation
        bound=err*(1 << (fb+1))
        if bound>=g.mpfr('.25'):raise ArithmeticError('end-to-end error budget')
    with g.context(g.get_context(),precision=256):
        c=copy.deepcopy(base['exp']);c.update(fraction_bits=f,table_bits=k,degree=degree,constant_fraction=f+8,
            log2e=constant(lambda:g.log2(g.exp(g.mpfr(1))),f+8),coefficients=coeff,
            table=[constant(lambda:g.exp2(g.mpfr(i)/(1 << k)),f) for i in range(1 << k)],
            pre_round_error_ulp_bound=str(bound),**proof)
    def horner(r):
        p=coeff[-1]
        for v in reversed(coeff[:-1]):p=v+(p*r >> f)
        return p
    lo,hi=horner(0),horner((1 << (f-k))-1)
    joins=[(hi*c['table'][i] >> f)<=(lo*c['table'][i+1] >> f) for i in range((1 << k)-1)]
    joins.append((hi*c['table'][-1] >> f)<=2*(lo*c['table'][0] >> f))
    if not all(joins):raise ArithmeticError('monotonic segment boundary')
    c['monotonic_segment_joins']=True
    return c



def generate(initial=False):
    output = ROOT/'build/sfu/generation'
    target=ROOT/'src/zircon_asic/data/sfu.json'
    selected=json.loads(target.read_text()).get('implementations',{}) if target.exists() and not initial else {}
    data = dict(schema_version=1, generator='scripts/generate_sfu.py', formats={},
                semantics=dict(exp_range_rounding='floor',exp_horner='coefficient + floor(full_product / 2^F)',
                    exp_table_merge='floor(full_product / 2^F)',exp_pack='RNE',
                    refinement_products='full precision followed by explicit floor shift',
                    normalized_table_encoding='(floor(value * 2^Q) << 1) | exact_remainder_nonzero',
                    algebraic_correction='one adjacent Q-bit candidate, exact integer residual',
                    algebraic_pack='five rounding modes after exponent recovery and subnormal shift'))
    with g.context(g.get_context(), precision=256):
        for name, (w, eb, fb, bias) in FORMATS.items():
            f, k, degree = (32, 6, 3) if name == 'fp32' else (20, 4, 2)
            coeff, proof = polynomial(f,k,degree,output/name)
            limit = ((1 << eb)-1) << fb; sign = 1 << (w-1); one = bias << fb
            c = dict(fraction_bits=f, table_bits=k, degree=degree, constant_fraction=f+8,
                     log2e=constant(lambda:g.log2(g.exp(g.mpfr(1))),f+8),
                     coefficients=coeff, table=[constant(lambda:g.exp2(g.mpfr(i)/(1 << k)),f) for i in range(1 << k)],
                     overflow=first(limit, lambda a: compute(name,'exp',a)[0] == limit),
                     zero=first(limit, lambda a: compute(name,'exp',sign | a)[0] == 0),
                     tiny=first(limit, lambda a: compute(name,'exp',sign | a)[0] < 1 << fb),
                     one_positive=first(limit, lambda a: compute(name,'exp',a)[0] != one)-1,
                     one_negative=first(limit, lambda a: compute(name,'exp',sign | a)[0] != one)-1,
                     **proof)
            # Conservative absolute significand bound: range conversion (|x|<128),
            # polynomial certificate, table rounding and fixed-point Horner shifts.
            qerr = g.mpfr(1)/(1 << f)
            bound = 2*(g.exp2(g.mpfr(128)/(1 << (f+9))+qerr)-1) + 2*g.mpfr(2)**(-(f-2)) + 6*qerr
            c['pre_round_error_ulp_bound'] = str(g.mul_2exp(bound,fb))
            if bound*(1 << fb) >= g.mpfr('.25'): raise ArithmeticError((name,'exp error budget',bound))
            # Monotonicity within segments follows from positive coefficients and
            # monotonic floor products; enumerate every join on the fixed-point grid.
            def horner(r):
                p=coeff[-1]
                for v in reversed(coeff[:-1]):p=v+(p*r >> f)
                return p
            end=horner((1 << (f-k))-1); start=horner(0)
            joins=[(end*c['table'][i] >> f) <= (start*c['table'][i+1] >> f) for i in range((1 << k)-1)]
            joins.append((end*c['table'][-1] >> f) <= 2*(start*c['table'][0] >> f))
            c['monotonic_segment_joins'] = all(joins)
            if not all(joins): raise ArithmeticError((name,'non-monotonic table join'))
            roots=dict(fraction_bits=fb+7, table_bits=7 if name=='fp32' else 6, iterations=2 if name=='fp32' else 1)
            size=1 << roots['table_bits']; rf=roots['fraction_bits']
            roots['rcp_seed']=[int(g.floor((1 << rf)/(1+(g.mpfr(i)+.5)/size))) for i in range(size)]
            roots['rsqrt_seed']=[int(g.floor((1 << rf)/g.sqrt((1+(g.mpfr(i)+.5)/size)*(1 << parity)))) for parity in range(2) for i in range(size)]
            data['formats'][name] = dict(exp=c, refinement=roots)
            if name != 'fp32': data['formats'][name]['tables']=tables(name,fb+7)
    if selected:
        data['implementations']=copy.deepcopy(selected)
        for key,entry in data['implementations'].items():
            name,op=key.split('.');cfg=entry['configuration'];base=data['formats'][name]
            if op=='exp':
                c=cfg['exp'];cfg['exp']=exp_parameters(name,base,c['fraction_bits'],c['table_bits'],c['degree'],output/key)
            else:
                cfg['exp']=copy.deepcopy(base['exp'])
                if name!='fp32':cfg['tables']=copy.deepcopy(base['tables'])
                r=cfg['refinement'];f,b=r['fraction_bits'],r['table_bits'];size=1<<b
                with g.context(g.get_context(),precision=256):
                    r['rcp_seed']=[int(g.floor((1 << f)/(1+(g.mpfr(i)+.5)/size))) for i in range(size)]
                    r['rsqrt_seed']=[int(g.floor((1 << f)/g.sqrt((1+(g.mpfr(i)+.5)/size)*(1 << parity)))) for parity in range(2) for i in range(size)]
    data['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    target.write_text(json.dumps(data,indent=2)+'\n')
    cpath=ROOT/'src/zircon_asic/data/contract.json'; contract=json.loads(cpath.read_text())
    for name in FORMATS:
        fb=FORMATS[name][2]
        for op in ('exp','rcp','sqrt','rsqrt'):
            if op == 'exp':
                d=data['formats'][name]['exp']['degree']
                phases=['decode','constant_product','range_reduce','lookup']+[f'horner_{i}' for i in range(d)]+['table_product','normalize','grs','round']
                variant='table_polynomial'
            elif name == 'bf16':
                phases=['decode','lookup','normalize','grs','round'];variant='normalized_table'
            elif op == 'sqrt':
                count=(fb+5+1)//2
                phases=['decode','radicand']+[f'root_{i}' for i in range(count)]+['root_correct','normalize','grs','round'];variant='nonrestoring'
            else:
                it=data['formats'][name]['refinement']['iterations']
                steps=['my','update'] if op == 'rcp' else ['square','my2','update']
                phases=['decode','seed']+[f'{step}_{i}' for i in range(it) for step in steps]+['candidate','residual','correct','normalize','grs','round']
                variant='newton_residual'
            if f'{name}.{op}' in selected:
                entry=selected[f'{name}.{op}'];phases=entry['phases'];variant=entry['variant']
            contract['units'][f'{name}.{op}']=dict(latency=len(phases),kind='elastic',phases=phases,variant=variant,
                stage_capacity=[1]*len(phases),arity=1,accuracy='faithful' if op=='exp' else 'correctly-rounded',
                rounding=['RNE'] if op=='exp' else ['RNE','RTZ','RDN','RUP','RMM'],
                resources='sfu.json',resources_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    cpath.write_text(json.dumps(contract,indent=2)+'\n')
    print(target.relative_to(ROOT))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--initial',action='store_true',help='generate the numerical starting profile')
    generate(parser.parse_args().initial)
