"""Publish only configuration-specific, hash-bound SPM qualification evidence."""
from pathlib import Path
import hashlib,json,sys,statistics
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from zircon_asic.spm import spm_contract_hash,spm_implementation_hash
from zircon_asic.evidence import implementation_hash
from report_paths import portable

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    implementation=spm_implementation_hash();contract=spm_contract_hash()
    python=read(ROOT/'build/python-validation.json');benchmark=read(ROOT/'build/spm-benchmark.json')
    network=read(ROOT/'build/rtl/spm-network/alignment.json')
    cold=read(ROOT/'build/spm-compile-benchmark.json');macro=read(ROOT/'build/spm-macro/validation.json')
    platform=read(ROOT/'build/ihp/platform/manifest.json')
    macrok=macro.get('passed') and all((ROOT/p).exists() and sha(ROOT/p)==h for p,h in macro['sources'].items())
    pyok=python['exit_code']==0 and python['unchanged_during_tests'] and not python['skipped'] and python['implementation_hash']==implementation_hash()
    perfok=benchmark.get('implementation_hash')==implementation and benchmark['cycles']>=1000000 and benchmark['repeats']>=5 and {c['case'] for c in benchmark['cases']}=={'single','conflict','mixed'} and all(c['target_passed'] for c in benchmark['cases']) and cold['implementation_hash']==implementation
    netok=network.get('implementation_hash')==implementation and network['discrepancy_cycles']==0 and network['rtl_sha256']==sha(ROOT/'build/rtl/spm-network/Unit.sv')
    records={};physical_records=[]
    for path in (ROOT/'build/ppa_spm').rglob('physical.json'):
        p=read(path);checks=path.parent/'checks.json';gate=path.parent/'gate/alignment.json'
        if checks.exists():p['checks']=read(checks)
        if gate.exists():p['gate']=read(gate)
        p['source']=str(path.relative_to(ROOT));physical_records.append(p)
    for path in sorted((ROOT/'build/rtl').glob('spm-*/alignment.json')):
        a=read(path)
        if 'configuration' not in a:continue
        c=a['configuration'];key=':'.join(str(c[x]) for x in ('capacity_bytes','data_width','banks','ports'))
        rtlsha=sha(path.parent/'Unit.sv')
        cycleok=a.get('implementation_hash')==implementation and a.get('contract_hash')==contract and a['build']['rtl']==rtlsha and len(a['runs'])>=5 and all(r['random_cycles']>=20000 and r['discrepancy_cycles']==0 for r in a['runs'])
        physical=None
        for p in physical_records:
            ch=p.get('checks',{});g=p.get('gate',{})
            if p['rtl_sha256']==rtlsha and p['exit_code']==0 and ch.get('passed') and g.get('discrepancy_cycles')==0 and g.get('netlist_sha256')==ch.get('netlist_sha256'):
                pp=ROOT/p['source'];net=pp.parent/'results/6_final.v'
                production=pp.parent/'Unit.sv'
                production_ok=production.exists() and sha(production)==p.get('physical_rtl_sha256',p['rtl_sha256'])
                if net.exists() and sha(net)==g['netlist_sha256'] and production_ok:physical=p
        status='dual-verified' if cycleok and pyok and perfok and netok and macrok and physical else 'cycle-verified' if cycleok and pyok else 'unqualified'
        record=dict(status=status,configuration=c,cycle_alignment=cycleok,functional_tests=pyok,performance=perfok,network_alignment=netok,
            physical=physical,rtl_sha256=rtlsha,alignment_source=str(path.relative_to(ROOT)),runs=a['runs'])
        if key not in records or status=='dual-verified':records[key]=record
    report=dict(implementation_hash=implementation,contract_hash=contract,configurations=records,python=python,benchmark=benchmark,cold_compilation=cold,macro_adapter=macro,platform_views=platform,network=network,physical_history=physical_records)
    (ROOT/'reports/spm-validation.json').write_text(json.dumps(portable(report),indent=2)+'\n')
    (ROOT/'src/zircon_asic/data/spm_qualification.json').write_text(json.dumps(portable({k:report[k] for k in ('implementation_hash','contract_hash','configurations')}),indent=2)+'\n')
    lines=['# SPM 验证记录','',f'共同契约 SHA-256：`{contract}`。',f'实现 SHA-256：`{implementation}`。','',
        'IHP SG13G2，100 MHz，TT/1.20 V/25°C 和 SS/1.08 V/125°C。测量流程为真实 SRAM 宏的详细布线、寄生参数提取和静态时序分析。','',
        '| 容量 / 位宽 / banks / ports | 状态 | Python/Numba/RTL | TT setup / hold ns | SS setup / hold ns |','|---|---|---|---:|---:|']
    for key,r in records.items():
        corners=(r['physical'] or {}).get('checks',{}).get('sta',[])
        sl=[' / '.join(f'{c[k]:.6f}' for k in ('setup_ns','hold_ns')) for c in corners]
        lines.append(f"| {key} | {r['status']} | {'通过' if r['cycle_alignment'] else '证据非当前'} | {sl[0] if sl else '—'} | {sl[1] if sl else '—'} |")
    lines += ['', '## 面积与频率','', '| 配置 | SRAM µm² | 控制逻辑 µm² | 核心 µm² | 两角均通过的最高扫描频率 MHz |','|---|---:|---:|---:|---:|']
    for key,r in records.items():
        if not r['physical']:continue
        checks=r['physical']['checks'];a=checks['area'];f=min(c['highest_scanned_mhz'] for c in checks['sta'])
        lines.append(f"| {key} | {a['finish__design__instance__area__macros']:.1f} | {a['finish__design__instance__area__stdcell']:.1f} | {a['finish__design__core__area']:.1f} | {f:.3f} |")
    lines += ['', '频率扫描固定布局与布线结果，周期边界分辨率为 0.001 ns。参考运行点为 100 MHz，访问延迟 2 拍；无冲突时每 bank 每拍服务一笔访问，两个参考配置分别为 1 亿和 4 亿笔/秒。各角关键路径与完整扫描点保存在物理证据包。',
        '', '## CPU 执行时间','', '同一主机、预生成百万拍请求、五次运行取中位数，包含调用时的网络数组准备与状态导回。各场景验证最终存储内容和统计相同；逐拍等价由独立回归验证。','',
        f"独立进程、空 Numba 缓存下，编译事件耗时 {cold['compile_seconds']:.3f} 秒，首次调用总耗时 {cold['cold_call_seconds']:.3f} 秒。性能进程峰值 RSS 为 {benchmark['peak_rss_bytes']/1048576:.1f} MiB。",'',
        '| 场景 | Python 中位秒 | Numba 中位秒 | 加速倍数 |','|---|---:|---:|---:|']
    for row in benchmark['cases']:lines.append(f"| {row['case']} | {statistics.median(row['seconds']['python']):.3f} | {statistics.median(row['seconds']['numba']):.3f} | {row['speedup']:.2f}× |")
    lines += ['', '宏和工具版本、详细布线面积、频率扫描、候选测量及原始证据路径见 `spm-validation.json`。',
        '综合网表通过官方 SRAM 模型执行功能回放；时序由独立 STA 测量。Verilator 标准单元功能模型显式连接 specify 延迟参考线，并为 Q 输出添加 10 ps 延迟以处理零延迟时钟树的事件顺序。原文件与适配文件分别记录摘要。','']
    (ROOT/'reports/SPM.md').write_text('\n'.join(lines))
    print({k:v['status'] for k,v in records.items()})
    return report

if __name__=='__main__':main()
