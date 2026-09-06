"""Independent TT/SS extracted-RC STA, frequency scan and macro/netlist checks."""
from pathlib import Path
import argparse,json,re,subprocess,hashlib
from ppa import IMAGE
from ppa_spm import ROOT,MACRO

def check(directory):
    out=Path(directory).resolve();cp='/workspace/'+str(out.relative_to(ROOT))
    for f in ('6_final.odb','6_final.spef','6_final.sdc','6_final.v'):
        if not (out/'results'/f).exists():raise FileNotFoundError(f)
    results=[]
    for corner,lib in [('typ','typ_1p20V_25C'),('slow','slow_1p08V_125C')]:
        std='typ_1p20V_25C' if corner=='typ' else 'slow_1p08V_125C'
        text=f'''read_liberty /OpenROAD-flow-scripts/flow/platforms/ihp-sg13g2/lib/sg13g2_stdcell_{std}.lib
read_liberty /workspace/build/ihp/lib/{MACRO}_{lib}.lib
read_db {cp}/results/6_final.odb
read_sdc {cp}/results/6_final.sdc
read_spef {cp}/results/6_final.spef
set_propagated_clock [all_clocks]
puts "CHECK_SETUP_BEGIN"
check_setup -verbose
puts "CHECK_SETUP_END"
report_checks -path_delay max -format full_clock_expanded -group_path_count 10 > {cp}/{corner}-setup.rpt
report_checks -path_delay min -format full_clock_expanded -group_path_count 10 > {cp}/{corner}-hold.rpt
report_check_types -min_pulse_width -min_period -violators > {cp}/{corner}-clock.rpt
report_clock_min_period > {cp}/{corner}-min-period.rpt
puts "BASELINE_BEGIN"
report_worst_slack -max -digits 6
report_worst_slack -min -digits 6
report_tns
puts "BASELINE_END"
foreach period {{4 5 6 7 8 9 10 11 12 14 16 20}} {{
  create_clock -name core_clock -period $period [get_ports clock]
  set_propagated_clock [all_clocks]
  puts "SCAN $period"
  report_worst_slack -max -digits 6
  report_worst_slack -min -digits 6
  report_check_types -min_pulse_width -min_period -violators
}}
proc period_pass {{period}} {{
  create_clock -name core_clock -period $period [get_ports clock]
  set_propagated_clock [all_clocks]
  report_check_types -min_pulse_width -min_period -violators > {cp}/{corner}-scan-clock.rpt
  set f [open {cp}/{corner}-scan-clock.rpt r]; set checks [read $f]; close $f
  return [expr {{[sta::worst_slack_cmd max] >= 0 && [sta::worst_slack_cmd min] >= 0 && [string first "VIOLATED" $checks] < 0}}]
}}
set low 4.0; set high 20.0
if {{[period_pass $high]}} {{
  while {{$high-$low > 0.001}} {{
    set middle [expr {{($high+$low)/2.0}}]
    if {{[period_pass $middle]}} {{set high $middle}} else {{set low $middle}}
  }}
  period_pass $high
  puts "REFINED_PERIOD $low $high"
  report_worst_slack -max -digits 6
  report_worst_slack -min -digits 6
}}
'''
        script=out/f'{corner}-sta.tcl';script.write_text(text)
        cmd=['docker','run','--rm','--platform','linux/amd64','-v',f'{ROOT}:/workspace',IMAGE,'bash','-lc',
             f'source /OpenROAD-flow-scripts/env.sh && openroad -exit {cp}/{corner}-sta.tcl']
        r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        (out/f'{corner}-sta.log').write_text(r.stdout)
        if r.returncode or re.search(r'(?m)^(Error:|\[ERROR)',r.stdout):raise RuntimeError(r.stdout[-4000:])
        baseline=r.stdout.split('BASELINE_BEGIN')[1].split('BASELINE_END')[0]
        slacks=[float(x) for x in re.findall(r'worst slack (?:min|max)\s+([-\d.eE]+)',baseline)]
        if len(slacks)!=2:raise ValueError(baseline)
        unconstrained=r.stdout.split('CHECK_SETUP_BEGIN')[1].split('CHECK_SETUP_END')[0]
        clock_report=(out/f'{corner}-clock.rpt').read_text()
        scans=[]
        for p,body in re.findall(r'SCAN (\d+)\n(.*?)(?=SCAN |\Z)',r.stdout,re.S):
            values=[float(x) for x in re.findall(r'worst slack (?:min|max)\s+([-\d.eE]+)',body)]
            passed=len(values)==2 and min(values)>=0 and 'VIOLATED' not in body
            scans.append(dict(period_ns=int(p),slacks_ns=values,passed=passed))
        refined=re.search(r'REFINED_PERIOD ([\d.]+) ([\d.]+)',r.stdout)
        bracket=list(map(float,refined.groups())) if refined else None
        results.append(dict(corner=corner,setup_ns=slacks[0],hold_ns=slacks[1],check_setup=unconstrained.strip(),clock_checks=clock_report.strip(),
            passed=min(slacks)>=0 and 'VIOLATED' not in clock_report and 'Warning:' not in unconstrained,
            scan=scans,period_boundary_ns=bracket,period_resolution_ns=0.001,
            highest_scanned_mhz=1000/bracket[1] if bracket else max([1000/x['period_ns'] for x in scans if x['passed']],default=0)))
    metrics=json.loads((out/'logs/6_report.json').read_text())
    physical=json.loads((out/'physical.json').read_text())
    count=len(re.findall(r'\b'+MACRO+r'\s+\\?\S+\s*\(', (out/'results/6_final.v').read_text()))
    violations=metrics.get('detailedroute__route__drc_errors',None)
    route=json.loads((out/'logs/5_2_route.json').read_text())
    drc={k:v for k,v in route.items() if 'drc' in k.lower()}
    report=dict(sta=results,macro_count=count,expected_macro_count=physical['banks'],route_drc=drc,
        area={k:v for k,v in metrics.items() if 'area' in k},rtl_sha256=physical['rtl_sha256'],
        netlist_sha256=hashlib.sha256((out/'results/6_final.v').read_bytes()).hexdigest(),
        passed=all(r['passed'] for r in results) and count==physical['banks'] and drc.get('detailedroute__route__drc_errors')==0
               and physical['exit_code']==0 and physical['target']=='finish')
    (out/'checks.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise RuntimeError('physical checks did not all pass')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();check(a.directory)
