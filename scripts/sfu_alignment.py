"""Persist the five-seed and long unary three-way cycle campaigns."""
import argparse
import hashlib
import json
import shutil
from validate_rtl import validate, ROOT

SEEDS = (751, 11509, 2026, 65537, 104729)


def campaign(name, op):
    reports = []
    src = ROOT / f"build/rtl/{name}_{op}"
    for seed, cycles in [(s, 20000) for s in SEEDS] + [(751, 100000)]:
        record = validate(name, op, seed=seed, cycles=cycles)
        dest = src / f"campaign/{seed}-{cycles}"
        dest.mkdir(parents=True, exist_ok=True)
        for filename in ("alignment.json", "stimulus.txt", "python.trace", "numba.trace", "rtl.trace", "events.json"):
            shutil.copyfile(src / filename, dest / filename)
        record["artifacts"] = {filename: hashlib.sha256((dest / filename).read_bytes()).hexdigest()
                               for filename in ("stimulus.txt", "python.trace", "numba.trace", "rtl.trace", "events.json")}
        reports.append(record)
    (src / "unary-campaign.json").write_text(json.dumps(reports, indent=2) + "\n")
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", action="append")
    args = parser.parse_args()
    for unit in args.unit or [f"{f}.{op}" for f in ("fp32", "fp16", "bf16") for op in ("exp", "rcp", "sqrt", "rsqrt")]:
        campaign(*unit.split("."))
