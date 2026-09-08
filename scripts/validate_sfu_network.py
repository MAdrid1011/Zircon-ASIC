"""Three-way SPM/unary/multiply network replay, including SRAM writeback."""
import argparse
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zircon_asic import *
from zircon_asic.fast_mixed import run_network
from zircon_asic.evidence import implementation_hash
from zircon_asic.spm import spm_implementation_hash
from validate_rtl import run, verilator_configuration
from validate_spm import harness


def validate(name, op, cycles=20000, seed=4921):
    dest = ROOT / f"build/rtl/{name}-{op}-spm-network"
    dest.mkdir(parents=True, exist_ok=True)
    run(["sbt", f"runMain zircon.GenerateUnarySPMExample {name} {op} {dest}"], dest / "generate.log", ROOT / "hardware")
    cpp = harness(1, 0).replace("VSPM", "VUnarySPMExample").replace("d.io_flush=fl;", "d.io_flush=fl; in >> x; d.io_mode=x;")
    (dest / "trace.cpp").write_text(cpp)
    version, flags = verilator_configuration()
    # The generated makefile embeds the Verilator runtime include directory.
    # This network RTL is regenerated for every run, so its object tree must
    # never survive a compiler-version change.
    shutil.rmtree(dest / "obj", ignore_errors=True)
    run(["verilator", *flags, "--cc", "--exe", "--build", "-j", "4", "--assert", "-Wno-fatal",
         "--top-module", "UnarySPMExample", "--Mdir", str(dest / "obj"), str(dest / "Unit.sv"),
         str(dest / "trace.cpp"), "-o", "trace"], dest / "compile.log")
    u = FloatingPointUnit(name, op)
    width = u.format.width
    word, mask = width // 8, (1 << (width // 8)) - 1
    a, b = SPM(256, data_width=width), SPM(256, data_width=width)
    net = Network().add("a", a).add("u", u).add("b", b).connect("a", "u")
    arithmetic = [u]
    last = "u"
    if op == "rsqrt":
        m = FpMul(name)
        net.add("m", m).connect("u", "m", b=(u.format.bias + 1) << u.format.fraction)
        arithmetic.append(m)
        last = "m"
    net.connect(last, "b", mapping={"address": Field("tag"), "write": True, "data": Field("bits"), "tag": Field("tag")})
    rows, expected, python_trace = [], [], []

    def cycle(mode, request=None, ready=True, reset=False, flush=False):
        sa, sb = a.debug_state(), b.debug_state()
        occupied = sum(sum(r is not None for r in module._slots) for module in arithmetic)
        if mode == 1:
            net.sources["a"] = InputSource([request] if request else [])
            ports = net.step(ready=ready, reset=reset, flush=flush)
            python_trace.append(ports)
            inp, out = ports["a"], ports["b"]
        else:
            inp = (a if mode == 0 else b).step(Inputs(request, ready, reset, flush))
            out = inp
            (b if mode == 0 else a).step(Inputs(reset=reset, flush=flush))
            for module in arithmetic:
                module.step(Inputs(reset=reset, flush=flush))
        r, response = request or MemoryRequest(0), out.response
        rows.append(" ".join(map(str, [int(reset), int(flush), mode, int(request is not None), r.address,
                     int(r.write), r.data, mask if r.mask is None else r.mask, r.tag, int(ready)])) + "\n")
        expected.append([int(inp.in_ready), int(out.out_valid), response.bits if response else 0,
                         response.tag if response else 0, int(response.write) if response else 0,
                         int(response.status) if response else 0,
                         int(any(sa["flight"]) or any(sb["flight"]) or occupied > 0),
                         sum(sa["queue_counts"]) + sum(sb["queue_counts"]),
                         sum(sa["outstanding"]) + sum(sb["outstanding"]) + occupied])
        return inp

    cycle(0, reset=True)
    # Initialize the macro-facing RTL only through normal write requests.
    for address in range(0, 256, word):
        assert cycle(2, MemoryRequest(address, True, 0)).accepted
    for _ in range(4):
        cycle(2)
    one = u.format.bias << u.format.fraction
    for address in range(0, 256, word):
        assert cycle(0, MemoryRequest(address, True, one + address // word)).accepted
    for _ in range(4):
        cycle(0)
    compiled = copy.deepcopy(net)
    start = len(rows)
    rng, held = np.random.default_rng(seed), None
    for k in range(cycles):
        if held is None and rng.random() > .15:
            address = int(rng.integers(256 // word)) * word
            held = MemoryRequest(address, tag=address)
        reset, flush = k % 3997 == 300, k % 977 == 100
        out = cycle(1, held, bool(rng.random() > .3) and not 330 < k % 1000 < 380, reset, flush)
        if out.accepted or reset or flush:
            held = None
    for _ in range(sum(module.timing.latency for module in arithmetic) + 8):
        cycle(1)
    stop = len(rows)
    raw = np.asarray([list(map(int, row.split())) for row in rows[start:stop]], np.uint64)
    keys = compiled._order()
    stimulus = np.zeros((len(raw), len(keys), 6), np.uint64)
    stimulus[:, keys.index("a"), :] = raw[:, 3:9]
    # Compare every module port; enter compiled execution twice at a cycle boundary.
    actual = []
    split = len(raw) // 3
    for lo, hi in ((0, split), (split, len(raw))):
        actual += run_network(compiled, hi-lo, ready=raw[lo:hi, 9].astype(bool),
                              reset=raw[lo:hi, 0].astype(bool), flush=raw[lo:hi, 1].astype(bool),
                              trace=True, _stimulus=np.ascontiguousarray(stimulus[lo:hi]))
    for k, (py, fast) in enumerate(zip(python_trace, actual)):
        if py != fast:
            failure = dict(cycle=k, seed=seed, python=repr(py), numba=repr(fast), stimulus=rows[start+k])
            (dest / "numba-failure.json").write_text(json.dumps(failure, indent=2))
            raise AssertionError(failure)
    for key in net.units:
        assert net.units[key].stats == compiled.units[key].stats
    assert np.array_equal(b.memory, compiled.units["b"].memory)
    assert np.array_equal(b.initialized, compiled.units["b"].initialized)
    for address in range(0, 256, word):
        cycle(2, MemoryRequest(address))
    for _ in range(4):
        cycle(2)
    (dest / "stimulus.txt").write_text("".join(rows))
    (dest / "python.trace").write_text("\n".join(" ".join(map(str, row)) for row in expected) + "\n")
    run([str(dest / "obj/trace"), str(dest / "stimulus.txt"), str(dest / "rtl.trace")], dest / "run.log")
    rtl = (dest / "rtl.trace").read_text().splitlines()
    assert len(rtl) == len(expected)
    for k, (line, py) in enumerate(zip(rtl, expected)):
        got = list(map(int, line.split()))
        fields = [0, 1, 6, 7, 8] + ([2, 3, 4, 5] if py[1] else [])
        if any(got[i] != py[i] for i in fields):
            failure = dict(cycle=k, seed=seed, python=py, rtl=got, stimulus=rows[k])
            (dest / "failure.json").write_text(json.dumps(failure, indent=2))
            raise AssertionError(failure)
    report = dict(format=name, operation=op, cycles=len(rows), numba_cycles=stop-start, seed=seed,
                  discrepancy_cycles=0, numba_discrepancy_cycles=0, contract_hash=contract_hash(),
                  spm_contract_hash=spm_contract_hash(), implementation_hash=implementation_hash(),
                  spm_implementation_hash=spm_implementation_hash(), verilator=version,
                  rtl_sha256=hashlib.sha256((dest / "Unit.sv").read_bytes()).hexdigest(),
                  memory_sha256=hashlib.sha256(b.memory.tobytes()).hexdigest(),
                  stimulus_sha256=hashlib.sha256((dest / "stimulus.txt").read_bytes()).hexdigest())
    (dest / "alignment.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS {name}.{op} SPM network: {len(rows)} cycles", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", action="append")
    parser.add_argument("--cycles", type=int, default=20000)
    args = parser.parse_args()
    for key in args.unit or [f"{f}.{op}" for f in ("fp32", "fp16", "bf16") for op in ("exp", "rsqrt")]:
        validate(*key.split("."), cycles=args.cycles)
