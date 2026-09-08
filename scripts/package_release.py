"""Bundle qualified RTL and its immutable validation/physical evidence."""
from pathlib import Path
import hashlib
import json
import zipfile
import tomllib
from report_paths import export_bytes

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    report = json.loads((ROOT / "reports/validation.json").read_text())
    units = report["units"]
    shared=json.loads((ROOT/'src/zircon_asic/data/contract.json').read_text())
    expected={k+suffix for k in shared['units'] for suffix in (('', '.unsigned') if k.startswith('int') else ('',))}
    if set(units) != expected or not all(r["qualified"] for r in units.values()):
        raise RuntimeError(f"evidence package requires all {len(expected)} current configurations to qualify")
    if any(key.split('.')[-1] in ('exp','rcp','sqrt','rsqrt') for key in units):
        unary=json.loads((ROOT/'reports/sfu-validation.json').read_text())
        if not unary.get('passed') or unary.get('implementation_hash')!=report['implementation_hash']:
            raise RuntimeError('unary evidence requires current numerical, cycle, ASIC, network and CPU acceptance')
    paths = set((ROOT / "reports").glob("*.md"))
    paths.update(ROOT / p for p in ("LICENSE", "THIRD_PARTY_NOTICES.md"))
    paths.update((ROOT / "licenses").glob("*.txt"))
    paths.add(ROOT / "reports/validation.json")
    paths.add(ROOT / "src/zircon_asic/data/contract.json")
    paths.add(ROOT / "src/zircon_asic/data/qualification.json")
    paths.add(ROOT / 'src/zircon_asic/data/sfu.json')
    paths.add(ROOT / 'reports/sfu-validation.json')
    for key, record in units.items():
        directory = ROOT / "build/rtl" / key.replace(".", "_")
        physical = ROOT / record["physical"]["source"]
        for sv in [directory / "Unit.sv", physical.parent / "Unit.sv"]:
            if hashlib.sha256(sv.read_bytes()).hexdigest() != record["rtl_sha256"]:
                raise RuntimeError(f"evidence source mismatch: {sv}")
        for name in ["Unit.sv", "manifest.json", "alignment.json", "build-id.json",
                     "stimulus.txt", "python.trace", "rtl.trace"]:
            paths.add(directory / name)
        paths.update(directory.glob("exhaustive-*.json"))
        paths.update(directory.glob("icarus-replay.json"))
        paths.update(directory.glob('bf16-*.json'))
        paths.update(directory.glob('unary-*.json'))
        paths.update(directory.glob('vectors-build.json'))
        paths.update(directory.glob('vectors-replay.log'))
        paths.update(directory.glob('normalized-replay.log'))
        paths.update(directory.glob('campaign/*/*'))
        if (directory/'numba.trace').exists():paths.add(directory/'numba.trace')
        paths.update(physical.parent / name for name in ["physical.json", "Unit.sv", "config.mk", "constraint.sdc"])
        paths.update((physical.parent / "reports").glob("*.rpt"))
        paths.update((physical.parent / "logs").glob("*.json"))
        paths.update(physical.parent.glob('bf16-checks.json'))
        paths.update(physical.parent.glob('unary-checks.json'))
        if key.split('.')[-1] in ('exp','rcp','sqrt','rsqrt'):
            paths.update(physical.parent/'results'/name for name in ('6_final.v','6_final.sdc','6_final.spef'))
            paths.update((physical.parent/'gate').glob('*.json'))
            paths.update((physical.parent/'gate').glob('*.trace'))
            paths.update((physical.parent/'gate').glob('[0-9]*.log'))
            paths.add(physical.parent/'gate/vectors.log')
        paths.update(physical.parent.glob('audit.*'))
        paths.update(physical.parent.glob('*.rpt'))
    for pattern in ["build/testfloat/*.json", "build/exhaustive/*.json", "build/python-validation.json", "build/benchmark.json"]:
        paths.update(ROOT.glob(pattern))
    for pattern in ('build/bf16/*.json','build/bf16/numerical/*/*.json','build/bf16/numerical/*/*.bin',
                    'build/bf16/cores/*.json','build/bf16/cores/*.sv','build/bf16/candidates/*/*.json'):
        paths.update(ROOT.glob(pattern))
    for pattern in ('build/sfu/benchmark.json','build/sfu/numeric/*/*.json','build/sfu/numeric/*/*.bin',
                    'build/sfu/certificates/**/*.json','build/sfu/certificates/**/*.bin',
                    'build/sfu/certificates/**/*.sollya','build/sfu/certificates/**/*.txt','build/sfu/certificates/**/*.log',
                    'build/sfu/candidates/selection.json','build/sfu/candidates/finalists.json',
                    'build/sfu/candidates/index.json','build/sfu/candidates/rejected.json',
                    'build/sfu/candidates/proofs/**/*.json','build/sfu/candidates/proofs/**/*.sollya',
                    'build/sfu/candidates/proofs/**/*.txt','build/asap7-functional/models.json','build/asap7-functional/cells.v'):
        paths.update(ROOT.glob(pattern))
    for directory in (ROOT/'build/rtl').glob('*-*-spm-network'):
        for pattern in ('*.json','*.sv','*.txt','*.trace'):paths.update(directory.glob(pattern))
    network = ROOT / "build/rtl/network"
    for pattern in ["*.json", "*.sv", "*.txt", "*.trace"]:
        paths.update(network.glob(pattern))
        paths.update((ROOT/'build/rtl/bf16-spm-network').glob(pattern))
    target = ROOT / f"dist/zircon-asic-{version}-rtl-evidence.zip"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        hashes = {}
        for path in sorted(paths):
            data = export_bytes(path); relative = path.relative_to(ROOT).as_posix()
            archive.writestr(relative, data)
            hashes[relative] = hashlib.sha256(data).hexdigest()
        archive.writestr("SHA256SUMS.json", json.dumps(hashes, indent=2) + "\n")
        archive.writestr("README.txt", f"Zircon-ASIC {version}: {len(expected)} qualified arithmetic configurations.\n"
                          "Each Unit.sv is an independent top; do not concatenate tops with duplicate module names.\n"
                          "For composition use the Chisel Arithmetic factory and generate the enclosing design.\n"
                          "Physical methods and results: reports/CONFIGURATIONS.md and reports/DECISIONS.md.\n"
                          "All source and evidence files are bound by SHA256SUMS.json.\n")
    print(f"Created {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
