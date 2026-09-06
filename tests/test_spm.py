import numpy as np
import pytest
from zircon_asic import SPM, MemoryRequest as R, MemoryStatus as S, Inputs, UninitializedReadError, CycleModule, INT32Add


def initialized(**kw):
    s=SPM(**kw); s.load_image(bytes(s.capacity_bytes)); return s


@pytest.mark.parametrize('width',[8,16,32,64])
def test_bytes_masks_and_errors(width):
    s=initialized(capacity_bytes=64,data_width=width)
    w=width//8; ref=bytearray(64)
    for a in range(0,64,w):
        for mask in range(1<<w):
            data=(mask*73194572314771)&((1<<width)-1)
            s.compute(R(a,True,data,mask))
            for j in range(w):
                if mask>>j&1: ref[a+j]=(data>>(8*j))&255
            assert s.compute(R(a)).bits==int.from_bytes(ref[a:a+w],'little')
    assert s.dump_image()==ref
    assert s.compute(R(64)).status==S.OUT_OF_RANGE
    if w>1: assert s.compute(R(1)).status==S.MISALIGNED
    assert s.compute(R(0xffffffff)).status==S.OUT_OF_RANGE
    with pytest.raises(ValueError): s.compute(R(0,data=1<<width))
    with pytest.raises(ValueError): s.compute(R(0,mask=1<<w))


def test_latency_ii_and_snapshotted_read():
    s=initialized()
    responses=[]
    for k in range(12):
        r=R(0,True,k,tag=k) if k%2==0 else R(0,tag=k)
        o=s.step(Inputs(r))
        assert o.accepted
        assert o.out_valid==(k>=2)
        if o.delivered: responses.append(o.response)
    assert [r.tag for r in responses]==list(range(10))
    assert [r.bits for r in responses if not r.write]==list(range(0,10,2))


def test_backpressure_and_flush_retains_writes():
    s=initialized()
    assert s.step(Inputs(R(0,True,99,tag=1),False)).accepted
    assert s.step(Inputs(R(0,tag=2),False)).accepted
    for _ in range(8):
        o=s.step(Inputs(R(4),False))
        assert not o.accepted and o.response.tag==1 and o.occupancy==2
    s.step(Inputs(R(0,True,5),True,flush=True))
    assert s.compute(R(0)).bits==99
    s.reset(); assert s.compute(R(0)).bits==99


def test_round_robin_and_independent_banks():
    s=initialized(ports=3,banks=2)
    for k in range(30):
        out=s.step(tuple(Inputs(R(0,tag=p)) for p in range(3)))
        assert [p for p,o in enumerate(out) if o.accepted]==[k%3]
    s.flush()
    out=s.step((Inputs(R(0)),Inputs(R(4)),Inputs(R(0))))
    assert [o.accepted for o in out]==[True,True,False]
    before=s.debug_state(); s.eval((Inputs(),)*3); s.eval((Inputs(),)*3)
    assert s.debug_state()==before


def test_protocol_and_diagnostics():
    s=SPM()
    assert isinstance(s,CycleModule) and isinstance(INT32Add(),CycleModule)
    with pytest.raises(UninitializedReadError): s.compute(R(0))
    s.load_image(b'\x01\x02\x03\x04')
    assert s.compute(R(0)).bits==0x04030201
    s.eval(Inputs(R(0)))
    with pytest.raises(RuntimeError): s.compute(R(0))
    s.tick()
    with pytest.raises(RuntimeError): s.dump_image()
    s.flush()
    with pytest.raises(RuntimeError): s.tick()
    m=SPM(ports=2)
    with pytest.raises(ValueError): m.eval((Inputs(reset=True),Inputs()))


def test_functional_batch():
    s=initialized()
    r=s.compute_batch([0,0,4,4],write=[1,0,1,0],data=[17,0,23,0],backend='python')
    assert list(r.bits)==[0,17,0,23]
    assert s.cycle==0 and s.stats.accepted==0
