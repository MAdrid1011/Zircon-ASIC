import random
import pytest
from zircon_asic import *


@pytest.mark.parametrize("factory", [FP32Fma, FP16Add, FP4Div, INT32Mul, INT8Div, FP32Div])
def test_latency(factory):
    u = factory()
    accepted = {}
    for k in range(100):
        o = u.step(Inputs(Request(1, 1, tag=k)))
        if o.accepted: accepted[k] = k
        if o.delivered:
            assert k-accepted[o.response.tag] == u.timing.latency
    assert u.stats.delivered > 0


@pytest.mark.parametrize("factory", [FP32Fma, INT8Div])
def test_random_stall_flush(factory):
    u, rng, queue = factory(), random.Random(491), []
    held = None
    for k in range(3000):
        inp = Inputs(Request(k & 7, 2, tag=k) if rng.random() < .85 else None,
                     rng.random() > .5, reset=k%137 == 0, flush=k%109 == 0)
        o = u.eval(inp)
        assert u.eval(inp) == o
        if inp.reset or inp.flush:
            queue.clear()
            assert not o.accepted and not o.delivered
            held = None
        else:
            if held is not None: assert o.response == held and o.out_valid
            if o.delivered: assert o.response == queue.pop(0)
            if o.accepted: queue.append(u.compute(inp.request))
            held = o.response if o.out_valid and not inp.out_ready else None
        u.tick()
        assert sum(x is not None for x in u._slots) == len(queue)
    u.flush()
    assert u.stats.accepted == u.stats.delivered + u.stats.cancelled


def test_custom_profile_and_tick_guard():
    with pytest.raises(ValueError): FP32Add(timing=Timing(1, "elastic", ("a",), "custom"))
    u = FP32Add(timing=Timing(1, "elastic", ("a",), "custom", matched=False))
    assert u.describe()["cycle_profile"] == "exploration"
    with pytest.raises(RuntimeError): u.tick()
