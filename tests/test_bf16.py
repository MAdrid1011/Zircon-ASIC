import numpy as np
import pytest
from zircon_asic import *
from rational_reference import compute as reference


@pytest.mark.parametrize('op', ['add', 'mul', 'fma', 'div'])
def test_bf16_reference(op):
    u = FloatingPointUnit('bf16', op)
    rng = np.random.default_rng(11509)
    for rm in Rounding:
        for a, b, c in rng.integers(0, 65536, (250, 3)):
            r = u.compute(Request(int(a), int(b), int(c), rm, 19))
            assert (r.bits, int(r.flags)) == reference('bf16', op, int(a), int(b), int(c), int(rm))
            assert r.tag == 19 and r.remainder == 0


def test_bf16_format_and_fusion():
    assert (BF16.width, BF16.exponent, BF16.fraction, BF16.bias) == (16, 8, 7, 127)
    assert BF16.nan_bits == 0x7fc0
    assert BF16.decode(1).exponent == -133
    # Exact product exceeds the destination range; the fused result is finite.
    r = BF16Fma().compute(Request(0x7f7f, 0x4000, 0xff7f))
    assert (r.bits, r.flags) == (0x7f7f, Flags(0))
    # A very small positive addend decides the direction at an exact midpoint.
    for rm in Rounding:
        for args in [(0x3f81, 0x3fc0, 1), (1, 1, 0x8000), (0x0080, 0x3f00, 1)]:
            r = BF16Fma().compute(Request(*args, rounding=rm))
            assert (r.bits, int(r.flags)) == reference('bf16', 'fma', *args, int(rm))
    for u in (BF16Add(), BF16Mul(), BF16Fma(), BF16Div()):
        with pytest.raises(ValueError): u.compute(Request(65536, 0))


def make_network(memory=True, reverse=False):
    n = Network()
    if memory:
        a, b = SPM(128, data_width=16), SPM(128, data_width=16)
        a.load_image(np.arange(0x3f00, 0x3f40, dtype='<u2').tobytes())
        b.load_image(bytes(128))
        nodes = [('a', a), ('fma', BF16Fma()), ('b', b)]
        for name, u in nodes[::(-1 if reverse else 1)]: n.add(name, u)
        n.connect('a', 'fma', b=0x4000, c=0x3f80)
        n.connect('fma', 'b', mapping={'address':Field('tag'), 'write':True, 'data':Field('bits'), 'tag':Field('tag')})
        n.source('a', [MemoryRequest(i*2, tag=i*2) for i in range(64)]).sink('b')
    else:
        n.add('a', BF16Add()).add('q', FIFO(3)).add('m', BF16Mul()).add('f', BF16Fma()).add('d', BF16Div()).add('out', DelayLine(2))
        n.connect('a','q').connect('q','m',b=0x3f80).connect('m','f',b=0x4000,c=0x3f80).connect('f','d',b=0x4000).connect('d','out')
        n.source('a',[Request(0x3e00+i,0x3f80,tag=i) for i in range(64)]).sink('out')
    return n


@pytest.mark.parametrize('memory', [False, True])
def test_bf16_network_switch(memory):
    pytest.importorskip('numba')
    a, b = make_network(memory), make_network(memory, True)
    rng = np.random.default_rng(2026)
    ready = rng.random(1200) > .25
    ready[55:100] = False
    reset = np.zeros(1200, bool); reset[19] = True
    flush = np.zeros(1200, bool); flush[37] = True
    expected = [a.step(ready=bool(ready[k]),reset=bool(reset[k]),flush=bool(flush[k])) for k in range(1200)]
    got = b.run(300, backend='numba', ready=ready[:300], reset=reset[:300], flush=flush[:300], trace=True)
    got += [b.step(ready=bool(ready[k]),reset=bool(reset[k]),flush=bool(flush[k])) for k in range(300,400)]
    got += b.run(800, backend='numba', ready=ready[400:], reset=reset[400:], flush=flush[400:], trace=True)
    assert got == expected
    for name, u in a.units.items():
        assert u.stats == b.units[name].stats
        if isinstance(u, SPM): assert u.dump_image() == b.units[name].dump_image()
    assert [s.position for s in a.sources.values()] == [s.position for s in b.sources.values()]


def test_bf16_buffer_bound():
    # A 523-bit bound covers the exact fused span and a possible sum carry.
    minimum = 2*(BF16.emin-BF16.fraction)
    maximum_exclusive = 2*(BF16.emax+1)+1
    assert maximum_exclusive-minimum <= 20*32
    rng=np.random.default_rng(737)
    a,b,c=rng.integers(0,65536,(3,1000),dtype=np.uint32)
    for op in ('add','mul','fma','div'):
        u=FloatingPointUnit('bf16',op)
        for rm in Rounding:
            x=u.compute_batch(a,b,c,rounding=rm,backend='python')
            y=u.compute_batch(a,b,c,rounding=rm,backend='numba')
            assert np.array_equal(x.bits,y.bits) and np.array_equal(x.flags,y.flags)
