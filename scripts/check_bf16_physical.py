"""Check constraints, clocks and DRC on the extracted ASAP7 BF16 design."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess
from ppa import ROOT,IMAGE


def check(directory,report_name='bf16-checks.json'):
    out=Path(directory).resolve();cp='/workspace/'+str(out.relative_to(ROOT))
    for filename in ('6_final.odb','6_final.spef','6_final.sdc','6_final.v'):
        if not (out/'results'/filename).exists():raise FileNotFoundError(out/'results'/filename)
    script=f'''foreach lib [glob /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/*_RVT_TT_*.lib*] {{
  read_liberty $lib
}}
read_db {cp}/results/6_final.odb
read_sdc {cp}/results/6_final.sdc
read_spef {cp}/results/6_final.spef
set_propagated_clock [all_clocks]
puts CHECK_SETUP_BEGIN
check_setup -verbose
puts CHECK_SETUP_END
report_checks -path_delay max -format full_clock_expanded -group_path_count 10 > {cp}/setup.rpt
report_checks -path_delay min -format full_clock_expanded -group_path_count 10 > {cp}/hold.rpt
report_check_types -min_pulse_width -min_period -violators > {cp}/clock.rpt
puts BASELINE_BEGIN
report_worst_slack -max -digits 6
report_worst_slack -min -digits 6
puts BASELINE_END
'''
    (out/'audit.tcl').write_text(script)
    command=['docker','run','--rm','--platform','linux/amd64','-v',f'{ROOT}:/workspace',IMAGE,'bash','-lc',
             f'source /OpenROAD-flow-scripts/env.sh && openroad -exit {cp}/audit.tcl']
    r=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/'audit.log').write_text(r.stdout)
    if r.returncode or re.search(r'(?m)^(Error:|\[ERROR)',r.stdout):raise RuntimeError(r.stdout[-5000:])
    unconstrained=r.stdout.split('CHECK_SETUP_BEGIN')[1].split('CHECK_SETUP_END')[0].strip()
    baseline=r.stdout.split('BASELINE_BEGIN')[1].split('BASELINE_END')[0]
    slacks=[float(x) for x in re.findall(r'worst slack (?:min|max)\s+([-\d.eE]+)',baseline)]
    clocks=(out/'clock.rpt').read_text();physical=json.loads((out/'physical.json').read_text())
    route=json.loads((out/'logs/5_2_route.json').read_text())
    drc=route.get('detailedroute__route__drc_errors')
    report=dict(rtl_sha256=physical['rtl_sha256'],configuration_sha256=physical['configuration_sha256'],
                clock_period_ps=1000,corner='TC',voltage=0.70,temperature_c=0,
                slacks_ps=slacks,check_setup=unconstrained,clock_checks=clocks,route_drc=drc,
                artifact_sha256={n:hashlib.sha256((out/'results'/n).read_bytes()).hexdigest()
                                 for n in ('6_final.odb','6_final.spef','6_final.sdc','6_final.v')},
                passed=len(slacks)==2 and min(slacks)>=0 and not unconstrained and 'VIOLATED' not in clocks and drc==0
                       and physical['exit_code']==0 and physical['target']=='finish')
    (out/report_name).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not report['passed']:raise RuntimeError('ASAP7 extracted physical checks failed')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();check(a.directory)
