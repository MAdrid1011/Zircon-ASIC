from zircon_asic import *
import pytest


def network(reverse=False):
    n = Network()
    items = [("a",INT8Add()),("fifo",FIFO(3)),("b",INT8Mul()),("d",DelayLine(2))]
    for name,u in reversed(items) if reverse else items: n.add(name,u)
    n.connect("a","fifo").connect("fifo","b",b=3).connect("b","d")
    n.source("a",[Request(i,2,tag=i) for i in range(100)]).sink("d")
    return n


def test_global_commit():
    n,m = network(),network(True)
    for k in range(400):
        ready = k%29 > 15
        assert n.step(ready=ready) == m.step(ready=ready)
    assert n.sinks["d"].received == m.sinks["d"].received
    assert [r.bits for _,r in n.sinks["d"].received] == [((i+2)*3)&255 for i in range(100)]
    assert all(u.stats.accepted == u.stats.delivered for u in n.units.values())


def test_network_latency_and_cycle_rejection():
    n = network()
    n.run(20)
    assert n.sinks["d"].received[0][0] == 6
    with pytest.raises(ValueError): n.connect("d","a")
    assert len(n.connections) == 3


@pytest.mark.parametrize("division", [False,True])
def test_compiled_network(division):
    import numpy as np
    pytest.importorskip("numba")
    n,m = network(),network(True)
    if division:
        n.units["b"],m.units["b"] = INT8Div(),INT8Div()
    rng = np.random.default_rng(88)
    ready = rng.random(1500) > .4
    resets,flushes = np.zeros(1500,bool),np.zeros(1500,bool)
    resets[92] = True; flushes[377] = True
    a = n.run(1500,ready=ready,reset=resets,flush=flushes,trace=True)
    b = m.run(1500,ready=ready,reset=resets,flush=flushes,trace=True,backend="numba")
    assert a == b
    assert n.sinks["d"].received == m.sinks["d"].received
    for name in n.units: assert n.units[name].stats == m.units[name].stats
    # State round-trips allow changing backends in the middle of a simulation.
    for k in range(30): assert n.step() == m.step()
