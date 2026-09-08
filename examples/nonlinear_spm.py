"""Read a scratchpad, evaluate exp or rsqrt × 2, and write another scratchpad."""
import argparse
import numpy as np
from zircon_asic import FloatingPointUnit, FpMul, Network, SPM, MemoryRequest, Field


def network(format="fp32", operation="exp", *, backend="python"):
    unit = FloatingPointUnit(format, operation)
    width = unit.format.width
    word = width // 8
    source, destination = SPM(256, data_width=width), SPM(256, data_width=width)
    # Raw encodings of finite positive inputs near 1. No conversion occurs in SPM.
    one = unit.format.bias << unit.format.fraction
    values = np.arange(one, one + 256 // word, dtype=f"<u{word}")
    source.load_image(values.tobytes())
    destination.load_image(bytes(256))
    net = Network().add("input", source).add("unary", unit).add("output", destination)
    net.connect("input", "unary")
    last = "unary"
    if operation == "rsqrt":
        two = (unit.format.bias + 1) << unit.format.fraction
        net.add("multiply", FpMul(format)).connect("unary", "multiply", b=two)
        last = "multiply"
    net.connect(last, "output", mapping={
        "address": Field("tag"), "write": True, "data": Field("bits"), "tag": Field("tag"),
    })
    # The caller supplies addresses. Tags carry explicit writeback addresses.
    net.source("input", [MemoryRequest(address, tag=address) for address in range(0, 256, word)])
    net.sink("output")
    cycles = 256 // word + sum(u.describe()["latency"] for u in net.units.values())
    completed = net.run(cycles, backend=backend)["output"]
    assert len(completed) == len(values)
    result = np.frombuffer(destination.dump_image(), dtype=f"<u{word}")
    return net, result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("fp32", "fp16", "bf16"), default="fp32")
    parser.add_argument("--operation", choices=("exp", "rsqrt"), default="exp")
    parser.add_argument("--backend", choices=("python", "numba"), default="python")
    args = parser.parse_args()
    net, values = network(args.format, args.operation, backend=args.backend)
    print(f"{len(values)} words written in {net.cycle} cycles")
    print("First eight raw results:", " ".join(f"0x{int(x):x}" for x in values[:8]))
