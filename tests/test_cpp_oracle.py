"""Cross-check the high-volume C++ oracle against independent Fraction code."""
from pathlib import Path
import shutil
import subprocess
import numpy as np
import pytest
from rational_reference import compute


@pytest.fixture(scope="session")
def cpp_oracle(tmp_path_factory):
    compiler = shutil.which("c++")
    if not compiler: pytest.skip("C++ compiler required for oracle audit")
    output = tmp_path_factory.mktemp("oracle") / "small_oracle"
    source = Path(__file__).resolve().parents[1] / "scripts/small_oracle.cpp"
    subprocess.run([compiler, "-std=c++17", "-O2", str(source), "-o", str(output)], check=True)
    return output


@pytest.mark.parametrize("name", ["e2m1", "e4m3fn", "e5m2"])
@pytest.mark.parametrize("op", ["add", "mul", "fma", "div"])
def test_exact_oracles_agree(cpp_oracle, name, op):
    width = 4 if name == "e2m1" else 8
    mask = (1 << width) - 1
    total = 1 << (width * (3 if op == "fma" else 2))
    if width == 4:
        windows = [(0, total)]
    else:
        rng = np.random.default_rng(4119)
        starts = [0, total - 64, *map(int, rng.integers(0, total - 64, 4))]
        windows = [(start, start + 64) for start in starts]
    for begin, end in windows:
        for rm in range(5):
            raw = subprocess.check_output([str(cpp_oracle), name, str(["add", "mul", "fma", "div"].index(op)),
                                           str(begin), str(end), str(rm)])
            expected = []
            for index in range(begin, end):
                a = (index >> (2 * width if op == "fma" else width)) & mask
                b = (index >> width if op == "fma" else index) & mask
                c = index & mask if op == "fma" else 0
                bits, flags = compute(name, op, a, b, c, rm)
                expected.append(bits | flags << 8)
            np.testing.assert_array_equal(np.frombuffer(raw, dtype="<u2"), expected)
