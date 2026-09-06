"""The same synchronous graph can execute with Python or optional Numba."""
from zircon_asic import Network, FIFO, DelayLine, INT8Add, INT8Mul, Request


def build():
    graph = Network().add("add", INT8Add()).add("fifo", FIFO(3))
    graph.add("mul", INT8Mul()).add("delay", DelayLine(2))
    graph.connect("add", "fifo").connect("fifo", "mul", b=3).connect("mul", "delay")
    graph.source("add", [Request(i, 2, tag=i) for i in range(32)]).sink("delay")
    return graph


print(build().run(100, backend="python"))
# Change backend to "numba" after installing zircon-asic[fast].
