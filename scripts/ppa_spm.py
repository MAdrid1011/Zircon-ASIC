"""IHP SRAM + same-process logic physical flow; no fake RAM or flop substitution."""
from pathlib import Path
import argparse, hashlib, json, re, subprocess, time
from ppa import IMAGE

ROOT=Path(__file__).resolve().parents[1]
MACRO='RM_IHPSG13_1P_1024x32_c2_bm_bist'

def run(banks=1,ports=1,target='finish'):
    name=f'spm-{4096*banks}-32-{banks}-{ports}-ihp'
    rtl=ROOT/'build/rtl'/name/'Unit.sv'
    if not rtl.exists():raise FileNotFoundError(rtl)
    sha=hashlib.sha256(rtl.read_bytes()).hexdigest()
    physical=rtl.parent/'physical';physical.mkdir(exist_ok=True)
    from validate_rtl import run as logged_run
    logged_run(['sbt',f'runMain zircon.GenerateSPM {banks*4096} 32 {banks} {ports} {physical} ihp physical'],physical/'generate.log',ROOT/'hardware')
    physical_rtl=physical/'Unit.sv'
    physical_sha=hashlib.sha256(physical_rtl.read_bytes()).hexdigest()
    pdn=(ROOT/'scripts/spm_pdn.tcl').read_text()
    out=ROOT/'build/ppa_spm'/name/sha[:12]/('prod-'+physical_sha[:12]+'-slow-hm50');out.mkdir(parents=True,exist_ok=True)
    cp='/workspace/'+str(out.relative_to(ROOT));ihp='/workspace/build/ihp'
    (out/'Unit.sv').write_bytes(physical_rtl.read_bytes())
    # The actual macro, not its behavioral model, must remain a physical block.
    columns=1 if banks==1 else 2;rows=(banks+columns-1)//columns
    width=columns*496.64+120;height=rows*416.46+120
    placement='set cells [lsort [get_cells -hierarchical -filter {ref_name == '+MACRO+'}]]\n'
    placement+=f'if {{[llength $cells] != {banks}}} {{error "SRAM macro count mismatch"}}\n'
    for b in range(banks):
        placement+=f'place_macro -macro_name [get_full_name [lindex $cells {b}]] -location {{{80+(b%columns)*496.64} {80+(b//columns)*416.46}}} -orientation R0 -exact\n'
    (out/'macros.tcl').write_text(placement)
    (out/'constraint.sdc').write_text('''create_clock -name core_clock -period 10 [get_ports clock]
set_input_delay -max 2 -clock core_clock [all_inputs -no_clocks]
set_input_delay -min 0 -clock core_clock [all_inputs -no_clocks]
set_output_delay -max 2 -clock core_clock [all_outputs]
set_output_delay -min 0 -clock core_clock [all_outputs]
set_clock_uncertainty -setup 0.5 [get_clocks core_clock]
set_clock_uncertainty -hold 0.1 [get_clocks core_clock]
set_input_transition 0.2 [all_inputs -no_clocks]
set_load 0.006 [all_outputs]
''')
    (out/'pdn.tcl').write_text(pdn)
    config=f'''export PLATFORM = ihp-sg13g2
export DESIGN_NAME = SPMPhysical
export VERILOG_FILES = {cp}/Unit.sv
export SDC_FILE = {cp}/constraint.sdc
export ADDITIONAL_LEFS = {ihp}/lef/{MACRO}.lef
export ADDITIONAL_GDS = {ihp}/gds/{MACRO}.gds
export ADDITIONAL_TYP_LIBS = {ihp}/lib/{MACRO}_typ_1p20V_25C.lib
export ADDITIONAL_SLOW_LIBS = {ihp}/lib/{MACRO}_slow_1p08V_125C.lib
export LIB_FILES = $(SLOW_LIB_FILES)
export DIE_AREA = 0 0 {width} {height}
export CORE_AREA = 20 20 {width-20} {height-20}
export MACRO_PLACEMENT_TCL = {cp}/macros.tcl
export PDN_TCL = {cp}/pdn.tcl
export PLACE_DENSITY = 0.30
export TNS_END_PERCENT = 100
export HOLD_SLACK_MARGIN = 0.05
export SETUP_SLACK_MARGIN = 0.1
export NUM_CORES = 4
export LEC_CHECK = 0
export RESULTS_DIR = {cp}/results
export REPORTS_DIR = {cp}/reports
export LOG_DIR = {cp}/logs
export OBJECTS_DIR = {cp}/objects
'''
    (out/'config.mk').write_text(config)
    cmd=['docker','run','--rm','--platform','linux/amd64','-v',f'{ROOT}:/workspace',IMAGE,'bash','-lc',
         f'source /OpenROAD-flow-scripts/env.sh && cd /OpenROAD-flow-scripts/flow && make DESIGN_CONFIG={cp}/config.mk RESULTS_DIR={cp}/results REPORTS_DIR={cp}/reports LOG_DIR={cp}/logs OBJECTS_DIR={cp}/objects {target}']
    start=time.monotonic()
    with (out/'flow.log').open('w') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
    report=dict(configuration=name,rtl_sha256=sha,physical_rtl_sha256=physical_sha,top='SPMPhysical',image=IMAGE,period_ns=10,macro=MACRO,banks=banks,ports=ports,
        macro_manifest=json.loads((ROOT/'build/ihp/manifest.json').read_text()),exit_code=r.returncode,
        elapsed_seconds=time.monotonic()-start,qualification='unqualified',target=target,
        config_sha256=hashlib.sha256(config.encode()).hexdigest())
    (out/'physical.json').write_text(json.dumps(report,indent=2))
    print(out,flush=True)
    if r.returncode:raise RuntimeError((out/'flow.log').read_text()[-4500:])
    return out

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--banks',type=int,default=1);p.add_argument('--ports',type=int,default=1);p.add_argument('--target',default='finish')
    a=p.parse_args();run(a.banks,a.ports,a.target)
