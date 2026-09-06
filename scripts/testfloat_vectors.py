"""Deterministic TestFloat prefixes: independent values, flags and RTL timing.

This is explicitly a bounded prefix of each generator stream, not a claim to
have completed TestFloat's multi-million-case level-1 campaigns.
"""
from pathlib import Path
import argparse
import hashlib
import itertools
import json
import subprocess
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zircon_asic import FloatingPointUnit, contract_hash
from build_oracles import SF_REV, TF_REV
from validate_rtl import validate


def check(name, op, count, rtl):
    generator = ROOT / "build/oracles/native-testfloat/testfloat_gen"
    if not generator.exists():
        raise RuntimeError("run scripts/build_oracles.py first")
    vectors, expected, streams = [], [], []
    modes = ["rnear_even", "rminMag", "rmin", "rmax", "rnear_maxMag"]
    for rm, mode in enumerate(modes):
        function = name.replace("fp", "f") + "_" + ("mulAdd" if op == "fma" else op)
        command = [str(generator), "-seed", "751", "-level", "1", "-" + mode, "-tininessafter", function]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = list(itertools.islice(process.stdout, count))
        process.terminate()
        _, error = process.communicate()
        if len(lines) != count:
            raise RuntimeError(f"incomplete TestFloat stream: {error}")
        streams.append(dict(rounding=rm, count=count, sha256=hashlib.sha256("".join(lines).encode()).hexdigest()))
        for line in lines:
            values = [int(v, 16) for v in line.split()]
            operands = values[:-2]
            vectors.append((*operands, *([0] if op != "fma" else []), rm))
            expected.append(values[-2:])
    unit = FloatingPointUnit(name, op)
    data = np.array(vectors, dtype=np.uint64)
    oracle = np.array(expected, dtype=np.uint64)
    for backend in ["python", "numba"]:
        output = unit.compute_batch(*data[:, :3].T, rounding=data[:, 3], backend=backend)
        np.testing.assert_array_equal(output.bits, oracle[:, 0])
        np.testing.assert_array_equal(output.flags, oracle[:, 1])
    dest = ROOT / "build/testfloat"
    dest.mkdir(exist_ok=True)
    record = dict(format=name, operation=op, cases=len(vectors), coverage="level-1 deterministic prefix per rounding mode",
                  seed=751, streams=streams, softfloat_revision=SF_REV, testfloat_revision=TF_REV,
                  contract_hash=contract_hash(), numerical_discrepancy=0, backends=["python", "numba"])
    if rtl:
        alignment = validate(name, op, vectors=vectors)
        record["rtl_sha256"] = alignment["rtl_sha256"]
        record["cycle_discrepancy"] = alignment["cycle_discrepancy"]
        record["backends"].append("verilator")
    (dest / f"{name}_{op}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS TestFloat {name}.{op}: {len(vectors)} cases", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=2048, help="prefix length per rounding mode")
    parser.add_argument("--rtl", action="store_true")
    args = parser.parse_args()
    if args.cases <= 0: parser.error("--cases must be positive")
    for name in ["fp16", "fp32"]:
        for op in ["add", "mul", "fma", "div"]:
            check(name, op, args.cases, args.rtl)
