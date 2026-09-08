"""Collect unary numerical, cycle, implementation and CPU evidence by content."""
from pathlib import Path
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from zircon_asic import contract_hash, contract
from zircon_asic.evidence import implementation_hash
from zircon_asic.unary import resources_hash, implementation_config
from report_paths import portable

OPERATIONS=('exp','rcp','sqrt','rsqrt')
SEEDS=(751,11509,2026,65537,104729)


def read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def current(record):
    return (record.get('contract_hash')==contract_hash() and record.get('implementation_hash')==implementation_hash()
            and record.get('resources_sha256')==resources_hash())


def evidence(name,op,rtl_sha):
    directory=ROOT/f'build/rtl/{name}_{op}'
    numeric=read(directory/'unary-numeric.json')
    certificate=read(ROOT/f'build/sfu/certificates/{name}_{op}/certificate.json')
    campaigns=read(directory/'unary-campaign.json',[])
    modes=1 if op=='exp' else 5
    cfg_sha=hashlib.sha256(json.dumps(implementation_config(name,op),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    proof=(current(certificate) and certificate.get('discrepancies')==0 and certificate.get('configuration_sha256')==cfg_sha
           and certificate.get('checker_sha256')==sha(ROOT/'scripts/certify_sfu.py')
           and certificate.get('generator_sha256')==sha(ROOT/'scripts/generate_sfu.py')
           and certificate.get('oracle_sha256')==sha(ROOT/'scripts/sfu_reference.py'))
    if op=='exp':proof &= (certificate.get('polynomial_bound_proved') is True and certificate.get('monotonic_segment_joins') is True
                          and float(certificate.get('pre_round_error_ulp_bound',1))<=.25)
    elif name=='fp32':
        count='normalized_rcp_inputs' if op=='rcp' else 'normalized_root_inputs'
        proof &= (certificate.get(count)==(1<<23 if op=='rcp' else 1<<24)
                  and certificate.get('source_sha256')==sha(ROOT/'scripts/sfu_cores.cpp')
                  and numeric.get('normalized',{}).get('cases')==certificate.get(count)
                  and numeric.get('normalized',{}).get('discrepancies')==0
                  and numeric.get('normalized',{}).get('vectors_sha256')==certificate.get('vectors_sha256')
                  and certificate.get('vectors_sha256')==sha(ROOT/f'build/sfu/certificates/{name}_{op}/core/vectors.bin'))
    numerical=(current(numeric) and proof and numeric.get('rtl_sha256')==rtl_sha
               and numeric.get('discrepancies')==0 and numeric.get('rtl_discrepancies')==0
               and numeric.get('rounding_modes')==modes
               and numeric.get('oracle_sha256')==sha(ROOT/'scripts/sfu_reference.py')
               and numeric.get('generator_sha256')==sha(ROOT/'scripts/validate_sfu.py')
               and numeric.get('vectors_sha256')==sha(ROOT/f'build/sfu/numeric/{name}_{op}/vectors.bin'))
    numerical &= (numeric.get('input_count',0)>=1000000 if name=='fp32' else numeric.get('input_count')==65536 and numeric.get('exhaustive_inputs') is True)
    numerical &= numeric.get('cases',0)==numeric.get('input_count',0)*modes
    valid=[]
    for r in campaigns:
        if (r.get('contract_hash')!=contract_hash() or r.get('implementation_hash')!=implementation_hash()
            or r.get('rtl_sha256')!=rtl_sha or r.get('cycle_discrepancy')!=0 or r.get('numba_cycle_discrepancy')!=0):continue
        path=directory/f"campaign/{r['seed']}-{r['cycles']}"
        if not r.get('artifacts') or any(sha(path/name)!=h for name,h in r['artifacts'].items()):continue
        valid.append(r)
    cycle=(all(any(r.get('seed')==seed and r.get('cycles',0)>=20000 for r in valid) for seed in SEEDS)
           and any(r.get('cycles',0)>=100000 for r in valid))
    return bool(numerical),bool(cycle),dict(numerical=numeric,certificate=certificate,campaign=campaigns)


def gate_evidence(physical_path):
    p=read(physical_path);audit=read(physical_path.parent/'unary-checks.json')
    gate=read(physical_path.parent/'gate/equivalence.json')
    name,op=p.get('unit','.').split('.')
    cycles=gate.get('campaigns',[])
    ok=(audit.get('passed') is True and audit.get('rtl_sha256')==p.get('rtl_sha256')
        and audit.get('configuration_sha256')==p.get('configuration_sha256')
        and gate.get('rtl_sha256')==p.get('rtl_sha256') and gate.get('discrepancy_cycles')==0
        and gate.get('netlist_sha256')==sha(physical_path.parent/'results/6_final.v')
        and all(any(r.get('seed')==s and r.get('cycles',0)>=20000 and r.get('discrepancy_cycles')==0 for r in cycles) for s in SEEDS)
        and any(r.get('cycles',0)>=100000 and r.get('discrepancy_cycles')==0 for r in cycles))
    numeric=read(ROOT/f'build/rtl/{name}_{op}/unary-numeric.json')
    ok &= (gate.get('numerical',{}).get('vectors_sha256')==numeric.get('vectors_sha256')
           and gate.get('numerical',{}).get('discrepancies')==0)
    for r in cycles:
        stim=ROOT/f"build/rtl/{name}_{op}/campaign/{r['seed']}-{r['cycles']}/stimulus.txt"
        ok &= r.get('stimulus_sha256')==sha(stim)
    return bool(ok),dict(physical_checks=audit,netlist_equivalence=gate)


def integration():
    rows=[]
    for name in ('fp32','fp16','bf16'):
        for op in ('exp','rsqrt'):
            directory=ROOT/f'build/rtl/{name}-{op}-spm-network'
            r=read(directory/'alignment.json')
            ok=(r.get('contract_hash')==contract_hash() and r.get('implementation_hash')==implementation_hash()
                and r.get('rtl_sha256')==sha(directory/'Unit.sv') and r.get('numba_cycles',0)>=20000
                and r.get('discrepancy_cycles')==0 and r.get('numba_discrepancy_cycles')==0
                and r.get('stimulus_sha256')==sha(directory/'stimulus.txt'))
            rows.append(dict(format=name,operation=op,passed=ok,report=r))
    return rows


def performance():
    report=read(ROOT/'build/sfu/benchmark.json')
    expected={(f,op,mode) for f in ('fp32','fp16','bf16') for op in OPERATIONS for mode in ('batch','cycle-unit','spm-network')
              if mode!='spm-network' or op in ('exp','rsqrt')}
    valid=set()
    if (report.get('contract_hash')==contract_hash() and report.get('implementation_hash')==implementation_hash()
        and report.get('benchmark_sha256')==sha(ROOT/'scripts/benchmark_sfu.py')):
        for r in report.get('cases',[]):
            ok=(r.get('cycles',r.get('inputs',0))>=1000000 and r.get('speedup',0)>=2
                and all(len(r.get('seconds',{}).get(b,[]))>=5 for b in ('python','numba'))
                and r.get('trace_discrepancies',r.get('discrepancies',-1))==0)
            if ok:valid.add((r['format'],r['operation'],r['mode']))
    return dict(passed=valid==expected,verified_cases=len(valid),required_cases=len(expected),measurement=report)


def main():
    validation=read(ROOT/'reports/validation.json');units={}
    for name in ('fp32','fp16','bf16'):
        for op in OPERATIONS:
            key=f'{name}.{op}';rtl_sha=sha(ROOT/f'build/rtl/{name}_{op}/Unit.sv')
            numeric,cycle,data=evidence(name,op,rtl_sha)
            record=validation.get('units',{}).get(key,{})
            units[key]=dict(numerical_passed=numeric,cycle_passed=cycle,qualification=record,**data)
    candidates=[]
    index=read(ROOT/'build/sfu/candidates/index.json',[])
    # The summary needs only existing measurements; numerical proof generators
    # and their development dependencies are not imported by report collection.
    for e in index:
        directory=ROOT/e['directory'];m=read(directory/'manifest.json');c=read(directory/'contract.json')
        cfg=c.get('sfu_overrides',{}).get(e['format'],{})
        profile={key:cfg[key] for key in ('multiply_stages','range_stages','root_bits_per_stage','exactness_classification','ready_topology','root_lookahead','division_lookahead','prefix_factor') if key in cfg}
        profile.update({key:cfg.get('exp' if e['operation']=='exp' else 'refinement',{}).get(key)
                        for key in ('fraction_bits','table_bits','degree' if e['operation']=='exp' else 'iterations')})
        records=[]
        if m.get('rtl_sha256'):
            for path in (ROOT/f"build/ppa/{e['format']}_{e['operation']}_TC").glob(m['rtl_sha256'][:12]+'*/physical.json'):
                p=read(path);metrics=p.get('metrics',{})
                file,prefix=('6_report.json','finish') if '6_report.json' in metrics else ('5_1_grt.json','globalroute')
                values=metrics.get(file,{})
                records.append(dict(source=str(path.relative_to(ROOT)),stage=p.get('target'),
                    area_um2=values.get(prefix+'__design__instance__area__stdcell'),
                    setup_slack_ps=values.get(prefix+'__timing__setup__ws'),hold_slack_ps=values.get(prefix+'__timing__hold__ws')))
        candidates.append(dict(**e,rtl_sha256=m.get('rtl_sha256'),configuration=profile,measurements=records))
    networks=integration();cpu=performance()
    report=dict(contract_hash=contract_hash(),implementation_hash=implementation_hash(),resources_sha256=resources_hash(),
                units=units,networks=networks,cpu=cpu,candidates=candidates,
                selection=read(ROOT/'build/sfu/candidates/selection.json'))
    report['passed']=(all(r['numerical_passed'] and r['cycle_passed'] and r['qualification'].get('qualified') for r in units.values())
                      and all(r['passed'] for r in networks) and cpu['passed'])
    (ROOT/'reports/sfu-validation.json').write_text(json.dumps(portable(report),indent=2)+'\n')
    lines=['# 非线性算子验证记录','',
        'FP32、FP16、BF16 的 exp、rcp、sqrt、rsqrt 使用独立整数数值内核、Numba 内核与 Chisel 流水实现。',
        f"共同契约 SHA-256：`{report['contract_hash']}`。",f"表、系数与阈值 SHA-256：`{report['resources_sha256']}`。",'',
        '## 数值与周期','',
        '| 配置 | 数值输入／舍入组合 | 精度 | 三方回归拍数 | L / II |',
        '|---|---:|---|---:|---:|']
    for key,r in units.items():
        n=r['numerical'];q=r['qualification'];cycles=sum(c.get('cycles',0) for c in r['campaign'])
        lines.append(f"| {key} | {n.get('cases',0):,} | {'<1 ULP、RNE 打包' if key.endswith('.exp') else '五种模式正确舍入'} | {cycles:,} | {q.get('latency','—')} / 1 |")
    lines+=['','exp 使用 Sollya 8.0 区间误差界、逐级定点截断误差累积和分段单调性检查；类别阈值由自适应 MPFR 有向上下界生成。三个精确算子以 MPFR 为数值参考，FP32 规格化通路穷举有效数与相应指数奇偶组合。',
        '','逐周期比较请求与响应握手、结果、异常、标签、级有效位、占用和事务事件。每项完整回归包含五个固定种子各 20,000 拍及一组 100,000 拍长回归。',
        '','## ASIC 面积与时序','',
        'ASAP7 RVT，TC，0.70 V、0 °C，周期 1,000 ps，输入／输出延迟 200 ps，时钟不确定度 50 ps。完整模块包含查表、校正逻辑、弹性寄存器和握手控制。',
        '','| 配置 | 结构 | 面积 µm² | setup / hold ps | 物理阶段 |','|---|---|---:|---:|---|']
    for key,r in units.items():
        p=r['qualification'].get('physical') or {};cfg=contract()['units'][key]
        slack=' / '.join(f'{p[k]:.3f}' if p.get(k) is not None else '—' for k in ('setup_slack_ps','hold_slack_ps'))
        area=f"{p['area_um2']:.3f}" if p.get('area_um2') is not None else '—'
        lines.append(f"| {key} | {cfg['variant']} | {area} | {slack} | {p.get('evaluation_level','未测量')} |")
    lines+=['','候选先通过数值筛选，再比较综合与全局布线；每项至多两个候选进入详细布线。通过时序、约束、时钟与路由检查后，按面积×延迟选型，差异不足 5% 时优先较小面积。',
        '','布线网表使用对应 Liberty 生成的功能单元模型，回放完整数值向量和六组逐周期激励；网表、RTL、激励与工艺文件均记录 SHA-256。',
        '','## CPU 与组合网络','',
        '| 配置 | 路径 | Python 秒 | Numba 秒 | 加速 |','|---|---|---:|---:|---:|']
    for r in cpu['measurement'].get('cases',[]):
        lines.append(f"| {r['format']}.{r['operation']} | {r['mode']} | {r['python_median_seconds']:.3f} | {r['numba_median_seconds']:.3f} | {r['speedup']:.2f}× |")
    lines+=['','输入和周期激励预生成，五轮交替测量 Python 与 Numba 并取中位数。编译单独计时；测量前比较完整输出、周期轨迹与最终状态。RSS 为进程峰值，逐项编译时间、运行时间和主机配置保存在 [机器可读报告](sfu-validation.json)。',
        '','组合网络覆盖 SPM → exp → SPM、SPM → rsqrt → mul → SPM，包含背压、复位、清空、最终存储内容和周期边界后端切换。','']
    (ROOT/'reports/SFU.md').write_text('\n'.join(lines))
    print(f"Unary evidence complete: {report['passed']}")
    return report


if __name__=='__main__':main()
