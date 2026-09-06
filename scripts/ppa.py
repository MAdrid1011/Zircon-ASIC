"""Pinned OpenROAD/ASAP7 physical flow. No inferred or fabricated timing passes."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "openroad/orfs@sha256:696763e68f34723118155f28f86851077847948e139d1495c67860066028b386"


def run(unit,corner="WC",target="finish"):
    name,op = unit.split(".")
    rtl = ROOT/f"build/rtl/{name}_{op}/Unit.sv"
    if not rtl.exists(): raise FileNotFoundError(f"generate and validate {unit} before physical implementation")
    top = re.search(r"^module (\w+)\(",rtl.read_text(),re.M)[1]
    rtl_bytes = rtl.read_bytes()
    rtl_hash = hashlib.sha256(rtl_bytes).hexdigest()
    out = ROOT/f"build/ppa/{name}_{op}_{corner}"/rtl_hash[:12]
    out.mkdir(parents=True,exist_ok=True)
    snapshot = out/"Unit.sv"
    if not snapshot.exists(): snapshot.write_bytes(rtl_bytes)
    if snapshot.read_bytes() != rtl_bytes: raise RuntimeError("RTL snapshot collision")
    container = "/workspace/"+str(out.relative_to(ROOT))
    config = f"""export PLATFORM = asap7
export DESIGN_NAME = {top}
export VERILOG_FILES = /workspace/{snapshot.relative_to(ROOT)}
export SDC_FILE = {container}/constraint.sdc
export CORNER = {corner}
export ASAP7_USE_VT = RVT
export CORE_UTILIZATION = 40
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 2
export PLACE_DENSITY_LB_ADDON = 0.20
export TNS_END_PERCENT = 20
export NUM_CORES = 2
# Bundled Kepler binary uses unsupported instructions under macOS ARM emulation.
# This flow performs timing/physical checks; post-resize formal LEC is separate.
export LEC_CHECK = 0
export RESULTS_DIR = {container}/results
export REPORTS_DIR = {container}/reports
export LOG_DIR = {container}/logs
export OBJECTS_DIR = {container}/objects
"""
    (out/"config.mk").write_text(config)
    (out/"constraint.sdc").write_text("""# ASAP7 timing units are picoseconds: 1000 ps is 1 GHz.
create_clock -name core_clock -period 1000 [get_ports clock]
set_input_delay 200 -clock core_clock [all_inputs -no_clocks]
set_output_delay 200 -clock core_clock [all_outputs]
set_clock_uncertainty 50 [get_clocks core_clock]
""")
    command = ["docker","run","--rm","--platform","linux/amd64","-v",f"{ROOT}:/workspace",IMAGE,
               "bash","-lc",f"source /OpenROAD-flow-scripts/env.sh && cd /OpenROAD-flow-scripts/flow && make DESIGN_CONFIG={container}/config.mk RESULTS_DIR={container}/results REPORTS_DIR={container}/reports LOG_DIR={container}/logs OBJECTS_DIR={container}/objects {target}"]
    start = time.monotonic()
    with (out/"flow.log").open("w") as log: result = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    metrics = {}
    for p in sorted((out/"logs").glob("*.json")):
        try: metrics[p.name] = json.loads(p.read_text())
        except json.JSONDecodeError: pass
    report = dict(unit=unit,corner=corner,platform="ASAP7",voltage=0.63 if corner=="WC" else None,
                  temperature_c=100 if corner=="WC" else None,clock_period_ps=1000,io_delay_ps=200,uncertainty_ps=50,
                  image=IMAGE,rtl_sha256=rtl_hash,source_snapshot=True,
                  target=target,post_resize_formal_lec=False,exit_code=result.returncode,elapsed_seconds=time.monotonic()-start,metrics=metrics)
    (out/"physical.json").write_text(json.dumps(report,indent=2)+"\n")
    if result.returncode: raise RuntimeError(f"{unit}: physical flow failed; inspect {out/'flow.log'}")
    print(f"Completed {unit} {corner} {target}: {out/'physical.json'}",flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("units",nargs="+")
    p.add_argument("--corner",default="WC",choices=["BC","TC","WC"])
    p.add_argument("--target",default="finish",choices=["synth","floorplan","place","cts","global_route","route","finish"])
    p.add_argument("--jobs",type=int,default=1)
    a = p.parse_args()
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futures={unit:pool.submit(run,unit,a.corner,a.target) for unit in a.units}
        errors=[]
        for unit,future in futures.items():
            try: future.result()
            except Exception as error: print(error,file=sys.stderr);errors.append(unit)
        if errors:sys.exit(1)
