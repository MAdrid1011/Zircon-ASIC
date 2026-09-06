"""Generate native Chisel RTL, run Verilator, compare every externally visible cycle.

All stimulus is persisted before execution. A failure retains both traces and
the first divergence, including the seed; successful runs emit a JSON report.
"""
from pathlib import Path
import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from zircon_asic import *


def run(cmd, log, cwd=ROOT):
    with open(log, "w") as f:
        process = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=cwd)
    if process.returncode:
        raise RuntimeError(f"command failed; {log}\n{Path(log).read_text()[-6000:]}")


def validate(name, op, signed=True, cycles=12000, seed=751, regenerate=False, exhaustive=False):
    unit = IntegerUnit(int(name[3:]),op,signed=signed) if name.startswith("int") else FloatingPointUnit(name,op)
    w = unit.width if name.startswith("int") else unit.format.width
    dest = ROOT / f"build/rtl/{name}_{op}{'_unsigned' if not signed else ''}"
    dest.mkdir(parents=True, exist_ok=True)
    sources = list((ROOT/"hardware/src").rglob("*.scala")) + [ROOT/"src/zircon_asic/data/contract.json"]
    sv = dest/"Unit.sv"
    regenerate |= not sv.exists() or max(p.stat().st_mtime for p in sources) > sv.stat().st_mtime
    if regenerate:
        run(["sbt", f"runMain zircon.Generate {name} {op} {dest} {'signed' if signed else 'unsigned'}"],dest/"generate.log",ROOT/"hardware")
    manifest = json.loads((dest/"manifest.json").read_text())
    assert manifest["contract"] == contract()
    assert manifest["latency"] == unit.timing.latency
    assert manifest["phases"] == list(unit.timing.phases)
    top = re.search(r"^module (\w+)\(",sv.read_text(),re.M)[1]
    harness = dest/"trace.cpp"
    text = (ROOT/"scripts/rtl_trace.cpp").read_text().replace("@TOP@",f"V{top}")
    if not harness.exists() or harness.read_text() != text: harness.write_text(text)
    exe = dest/"obj/trace"
    if regenerate or not exe.exists() or harness.stat().st_mtime > exe.stat().st_mtime:
        run(["verilator","--cc","--exe","--build","-j","4","--assert","-Wno-fatal","--top-module",top,
             "--Mdir",str(dest/"obj"),"-CFLAGS","-std=c++17",str(sv),str(harness),"-o","trace"],dest/"compile.log")
    rng = np.random.default_rng(seed)
    reqs = []
    if exhaustive:
        assert w <= 8
        if op == "fma": assert w == 4, "FP8 FMA exhaustive uses the sharded release driver"
        reqs = [(a,b,c,rm) for rm in (range(5) if not name.startswith("int") else [0])
                for a in range(1 << w) for b in range(1 << w)
                for c in (range(1 << w) if op == "fma" else [0])]
    rows, expected, events, accepted, previous_visible = [], [], [], {}, set()
    held, idx, k = None, 0, 0
    # Request payload is held by this driver until accepted, even across long stalls.
    while k < cycles or (exhaustive and (idx < len(reqs) or unit._phase or any(x is not None for x in unit._slots))):
        rst = k in (93,444) if k < cycles else False
        flush = k in (177,901,4097) if k < cycles else False
        ready = bool(rng.random() > .25) and not (500 <= k%1500 < 545) if k < cycles else True
        if held is None:
            if exhaustive and idx < len(reqs):
                a,b,c,rm = reqs[idx]; held = Request(a,b,c,Rounding(rm),idx); idx += 1
            elif not exhaustive and k < cycles-2*unit.timing.latency and rng.random() > .15:
                a,b,c = [int(x) for x in rng.integers(0,1 << w,3,dtype=np.uint64)]
                held = Request(a,b,c,Rounding(int(rng.integers(0,5))),k)
        inp = Inputs(held,ready,rst,flush)
        o = unit.step(inp)
        if o.accepted: accepted[held.tag] = k
        if o.out_valid and o.response.tag not in previous_visible:
            events.append(dict(tag=o.response.tag,accepted=accepted.get(o.response.tag),ready=k))
            previous_visible.add(o.response.tag)
        if o.delivered:
            events.append(dict(tag=o.response.tag,delivered=k))
        r = held or Request(0,0)
        rows.append(f"{int(rst)} {int(flush)} {int(held is not None)} {r.a} {r.b} {r.c} {int(r.rounding)} {r.tag} {int(ready)}\n")
        response = o.response or Response(0)
        expected.append([int(o.in_ready),int(o.out_valid),response.bits,int(response.flags),response.tag,response.remainder,
                         sum(int(v) << i for i,v in enumerate(o.stage_valid)),o.occupancy,
                         (o.stage_valid.index(True)+1 if any(o.stage_valid) else 0) if unit.timing.kind == "iterative" else 0,o.iteration])
        if o.accepted or rst or flush: held = None
        k += 1
    (dest/"stimulus.txt").write_text("".join(rows))
    run([str(exe),str(dest/"stimulus.txt"),str(dest/"rtl.trace")],dest/"run.log")
    actual = [[int(v) for v in line.split()] for line in (dest/"rtl.trace").read_text().splitlines()]
    assert len(actual) == len(expected)
    for i,(py,rtl) in enumerate(zip(expected,actual)):
        fields = [0,1,6,7,8,9] + ([2,3,4,5] if py[1] else [])
        if any(py[j] != rtl[j] for j in fields):
            (dest/"python.trace").write_text("\n".join(" ".join(map(str,x)) for x in expected))
            failure = dict(seed=seed,cycle=i,fields=[j for j in fields if py[j] != rtl[j]],python=py,rtl=rtl,stimulus=rows[i].strip())
            (dest/"failure.json").write_text(json.dumps(failure,indent=2))
            raise AssertionError(f"{name}.{op} signed={signed}: {failure}; traces in {dest}")
    result = dict(format=name,operation=op,signed=signed,seed=seed,cycles=k,contract_hash=contract_hash(),
                  statistics=unit.stats.report(),cycle_discrepancy=0,numerical_discrepancy=0,
                  test="exhaustive" if exhaustive else "random-backpressure-reset-flush",physical_qualification=False)
    (dest/"alignment.json").write_text(json.dumps(result,indent=2)+"\n")
    (dest/"events.json").write_text(json.dumps(events))
    print(f"PASS {name}.{op} {'signed' if signed else 'unsigned'}: {k} cycles, {unit.stats.delivered} delivered",flush=True)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--unit", action="append", help="e.g. fp32.fma; default all")
    p.add_argument("--unsigned", action="store_true")
    p.add_argument("--cycles",type=int,default=12000)
    p.add_argument("--seed",type=int,default=751)
    p.add_argument("--exhaustive",action="store_true")
    args = p.parse_args()
    names = args.unit or list(contract()["units"])
    results = [validate(*name.split("."),signed=not args.unsigned,cycles=args.cycles,seed=args.seed,exhaustive=args.exhaustive) for name in names]
    (ROOT/"build/alignment-summary.json").write_text(json.dumps(results,indent=2))


if __name__ == "__main__": main()
