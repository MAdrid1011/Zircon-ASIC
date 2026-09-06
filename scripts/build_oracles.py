"""Fetch immutable, test-only Berkeley oracles and build on macOS/Linux."""
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SF_REV = "a0c6494cdc11865811dec815d5c0049fba9d82a8"
TF_REV = "a9c849f1b0eb0264b626d9686ffae167d996e3be"


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    base = ROOT / "build/oracles"
    sf = base / "softfloat"
    if not sf.exists(): run("git", "clone", "https://github.com/ucb-bar/berkeley-softfloat-3.git", str(sf))
    run("git", "-C", str(sf), "checkout", "--detach", SF_REV)
    build = base / "native-softfloat"
    build.mkdir(parents=True, exist_ok=True)
    for name in ("Makefile", "platform.h"):
        shutil.copyfile(sf / "build/Linux-RISCV64-GCC" / name, build / name)
    source = sf / "source"
    compile = f'cc -c -fPIC -DSOFTFLOAT_FAST_INT64 -DSOFTFLOAT_ROUND_ODD -DINLINE_LEVEL=5 -DSOFTFLOAT_FAST_DIV32TO16 -DSOFTFLOAT_FAST_DIV64TO32 -I. -I{source}/RISCV -I{source}/include -O2 -o $@'
    run("make", "-s", "-j8", f"SOURCE_DIR={source}", f"COMPILE_C={compile}", cwd=build)
    output = base / ("liboracle.dylib" if sys.platform == "darwin" else "liboracle.so")
    run("cc", "-dynamiclib" if sys.platform == "darwin" else "-shared", "-fPIC", "-O2",
        f"-I{source}/include", str(ROOT / "scripts/softfloat_shim.c"), str(build/"softfloat.a"), "-o", str(output))
    tf = base / "testfloat"
    if not tf.exists(): run("git", "clone", "https://github.com/ucb-bar/berkeley-testfloat-3.git", str(tf))
    run("git", "-C", str(tf), "checkout", "--detach", TF_REV)
    tfbuild = base / "native-testfloat"
    tfbuild.mkdir(parents=True, exist_ok=True)
    for name in ("Makefile", "platform.h"):
        shutil.copyfile(tf / "build/Linux-RISCV64-GCC" / name, tfbuild / name)
    tfsource = tf / "source"
    tfcompile = f'cc -c -DFLOAT16 -DFLOAT64 -DBFLOAT16 -DFLOAT128 -DFLOAT_ROUND_ODD -I. -I{tfsource}/subj-C -I{tfsource} -I{source}/include -O2 -o $@'
    run("make", "-s", "-j8", "testfloat_gen", f"SOURCE_DIR={tfsource}",
        f"SOFTFLOAT_DIR={sf}", f"SOFTFLOAT_LIB={build}/softfloat.a",
        f"COMPILE_C={tfcompile}", f"COMPILE_SLOWFLOAT_C={tfcompile}", "LINK=cc -o $@", cwd=tfbuild)
    (base / "versions.txt").write_text(f"SoftFloat {SF_REV}\nTestFloat {TF_REV}\n")
    print(output)


if __name__ == "__main__": main()
