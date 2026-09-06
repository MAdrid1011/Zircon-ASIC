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
    if len(units) != 38 or not all(r["qualified"] for r in units.values()):
        raise RuntimeError("release requires all 38 current configurations to qualify")
    paths = set((ROOT / "reports").glob("*.md"))
    paths.update(ROOT / p for p in ("LICENSE", "THIRD_PARTY_NOTICES.md"))
    paths.update((ROOT / "licenses").glob("*.txt"))
    paths.add(ROOT / "reports/validation.json")
    paths.add(ROOT / "src/zircon_asic/data/contract.json")
    paths.add(ROOT / "src/zircon_asic/data/qualification.json")
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
        paths.update(physical.parent / name for name in ["physical.json", "Unit.sv", "config.mk", "constraint.sdc"])
        paths.update((physical.parent / "reports").glob("*.rpt"))
        paths.update((physical.parent / "logs").glob("*.json"))
    for pattern in ["build/testfloat/*.json", "build/exhaustive/*.json", "build/python-validation.json", "build/benchmark.json"]:
        paths.update(ROOT.glob(pattern))
    network = ROOT / "build/rtl/network"
    for pattern in ["*.json", "*.sv", "*.txt", "*.trace"]:
        paths.update(network.glob(pattern))
    target = ROOT / f"dist/zircon-asic-{version}-rtl-evidence.zip"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        hashes = {}
        for path in sorted(paths):
            data = export_bytes(path); relative = path.relative_to(ROOT).as_posix()
            archive.writestr(relative, data)
            hashes[relative] = hashlib.sha256(data).hexdigest()
        archive.writestr("SHA256SUMS.json", json.dumps(hashes, indent=2) + "\n")
        archive.writestr("README.txt", f"Zircon-ASIC {version}: 38 qualified arithmetic configurations.\n"
                          "Each Unit.sv is an independent top; do not concatenate tops with duplicate module names.\n"
                          "For composition use the Chisel Arithmetic factory and generate the enclosing design.\n"
                          "Physical methods and results: reports/CONFIGURATIONS.md and reports/DECISIONS.md.\n"
                          "All source and evidence files are bound by SHA256SUMS.json.\n")
    print(f"Created {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
