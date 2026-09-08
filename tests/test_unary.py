import copy
import numpy as np
import pytest

from zircon_asic import (
    FloatingPointUnit, FP32Exp, FP32Rcp, FP32Sqrt, FP32Rsqrt,
    FP16Exp, BF16Rsqrt, Request, Response, Inputs, Rounding, Flags,
    Network, SPM, MemoryRequest, Field, FpMul,
)

FORMATS = ('fp32','fp16','bf16')
OPS = ('exp','rcp','sqrt','rsqrt')


@pytest.mark.parametrize('name', FORMATS)
@pytest.mark.parametrize('op', OPS)
def test_unary_special_values(name, op):
    u=FloatingPointUnit(name,op);f=u.format;sign=1 << (f.width-1)
    one=f.bias << f.fraction;inf=f.inf_bits
    cases={
        'exp': [(0,one,0),(sign,one,0),(inf,inf,0),(inf|sign,0,0)],
        'rcp': [(0,inf,8),(sign,inf|sign,8),(inf,0,0),(inf|sign,sign,0)],
        'sqrt':[(0,0,0),(sign,sign,0),(inf,inf,0),(inf|sign,f.nan_bits,16)],
        'rsqrt':[(0,inf,8),(sign,inf|sign,8),(inf,0,0),(inf|sign,f.nan_bits,16)],
    }[op]+[(f.nan_bits,f.nan_bits,0),(inf|1,f.nan_bits,16)]
    for raw,bits,flags in cases:
        # Unused operands include signaling NaN, so accidental binary dispatch fails.
        assert u.compute(Request(raw,inf|1,inf|1,tag=31)) == Response(bits,Flags(flags),31)


def test_correct_rounding_and_exact_flags():
    for rm,bits in zip(Rounding,(0x3eaaaaab,0x3eaaaaaa,0x3eaaaaaa,0x3eaaaaab,0x3eaaaaab)):
        assert FP32Rcp().compute(Request(0x40400000,rounding=rm)) == Response(bits,Flags.NX)
    for cls,raw,answer in [(FP32Rcp,0x40000000,0x3f000000),(FP32Sqrt,0x40800000,0x40000000),
                           (FP32Rsqrt,0x40800000,0x3f000000),(FP32Exp,0,0x3f800000)]:
        assert cls().compute(Request(raw)) == Response(answer)
    for cls,base in [(FP32Sqrt,0x3fb504f3),(FP32Rsqrt,0x3f3504f3)]:
        for rm in Rounding:
            assert cls().compute(Request(0x40000000,rounding=rm)) == Response(base+int(rm==Rounding.RUP),Flags.NX)


@pytest.mark.parametrize('name',FORMATS)
@pytest.mark.parametrize('op',OPS)
def test_unary_batch_and_numba(name,op):
    pytest.importorskip('numba')
    u=FloatingPointUnit(name,op);rng=np.random.default_rng(701)
    a=rng.integers(0,1 << u.format.width,(12,43),dtype=np.uint64)
    modes=np.zeros(a.shape,np.uint8) if op=='exp' else rng.integers(0,5,a.shape,dtype=np.uint8)
    p=u.compute_batch(a,rounding=modes,tags=17,backend='python')
    n=u.compute_batch(a,rounding=modes,tags=17,backend='numba')
    for field in ('bits','flags','tags','remainder'):
        assert np.array_equal(getattr(p,field),getattr(n,field))
    assert p.bits.shape==a.shape


def test_unary_invalid_input_is_atomic():
    u=FP32Exp();u.step(Inputs(Request(0x3f800000)))
    before=copy.deepcopy((u.cycle,u.stats,u._slots,u._pending))
    for request in [Request(0,rounding=Rounding.RUP),Request(-1),Request(0,b=1 << 32),Request(0,c=-1)]:
        with pytest.raises(ValueError):u.step(Inputs(request))
        assert (u.cycle,u.stats,u._slots,u._pending)==before
    for backend in ('python','numba'):
        with pytest.raises(ValueError):u.compute_batch([0,1],rounding=[0,1],backend=backend)
    with pytest.raises(ValueError):FloatingPointUnit('e5m2','exp')


def test_network_rejects_invalid_exp_before_commit():
    for backend in ('python','numba'):
        if backend=='numba':pytest.importorskip('numba')
        u=FP32Exp();n=Network().add('u',u).source('u',[Request(0,rounding=Rounding.RUP)]).sink('u')
        with pytest.raises(ValueError):n.run(1,backend=backend)
        assert n.cycle==u.cycle==u.stats.cycles==0
        assert u._pending is None and not any(u._slots)
        n.source('u',[Request(0)])
        n.run(u.timing.latency+1,backend=backend)
        assert n.sinks['u'].received[0][1].bits==0x3f800000


@pytest.mark.parametrize('name',FORMATS)
@pytest.mark.parametrize('op',OPS)
def test_unary_latency_capacity_flush(name,op):
    u=FloatingPointUnit(name,op);lat=u.timing.latency
    deliveries=[]
    for k in range(lat+20):
        o=u.step(Inputs(Request(u.format.bias << u.format.fraction,tag=k) if k<20 else None))
        if o.delivered:deliveries.append((k,o.response.tag))
    assert deliveries==[(lat+i,i) for i in range(20)]
    u.reset()
    for k in range(lat):assert u.step(Inputs(Request(0,tag=k),False)).accepted
    held=u.step(Inputs(Request(0,tag=100),False))
    assert not held.in_ready and held.occupancy==lat
    for _ in range(7):assert u.step(Inputs(None,False)).response==held.response
    cancelled=u.step(Inputs(Request(0),True,flush=True))
    assert not cancelled.accepted and not cancelled.delivered
    assert u.stats.cancelled==lat and not any(u._slots)


def unary_network(op='exp',reverse=False):
    a,b=SPM(128,data_width=16),SPM(128,data_width=16)
    a.load_image(np.arange(0x3b80,0x3bc0,dtype='<u2').tobytes());b.load_image(bytes(128))
    nodes=[('input',a),('u',FloatingPointUnit('bf16',op)),('output',b)]
    if op=='rsqrt':nodes.insert(2,('mul',FpMul('bf16')))
    n=Network()
    for name,unit in nodes[::(-1 if reverse else 1)]:n.add(name,unit)
    n.connect('input','u',mapping={'a':Field('bits'),'tag':Field('tag')})
    if op=='rsqrt':n.connect('u','mul',b=0x3f80)
    n.connect('u' if op=='exp' else 'mul','output',mapping={
        'address':Field('tag'),'write':1,'data':Field('bits'),'tag':Field('tag')})
    return n.source('input',[MemoryRequest(i*2,tag=i*2) for i in range(64)]).sink('output')


@pytest.mark.parametrize('op',['exp','rsqrt'])
def test_unary_mixed_network_switch(op):
    pytest.importorskip('numba');a,b=unary_network(op),unary_network(op,True)
    rng=np.random.default_rng(1729);ready=rng.random(600)>.3;ready[30:91]=False
    flush=np.zeros(600,bool);flush[51]=True
    expected=a.run(600,backend='python',ready=ready,flush=flush,trace=True)
    actual=b.run(130,backend='numba',ready=ready[:130],flush=flush[:130],trace=True)
    actual+=b.run(170,backend='python',ready=ready[130:300],flush=flush[130:300],trace=True)
    actual+=b.run(300,backend='numba',ready=ready[300:],flush=flush[300:],trace=True)
    assert actual==expected
    for name in a.units:
        assert a.units[name].stats==b.units[name].stats
    assert a.units['output'].dump_image()==b.units['output'].dump_image()


def test_unary_shared_spm_competition_and_backend_switch():
    pytest.importorskip('numba')
    def make(reverse=False):
        a,b=SPM(4096,banks=4,ports=4),SPM(4096,banks=4,ports=4)
        a.load_image(np.full(1024,0x40000000,dtype='<u4').tobytes());b.load_image(bytes(4096))
        nodes=[('input',a),('output',b)]+[(op,FloatingPointUnit('fp32',op)) for op in OPS]
        n=Network()
        for key,u in nodes[::(-1 if reverse else 1)]:n.add(key,u)
        for p,op in enumerate(OPS):
            n.connect('input',op,source_port=p)
            n.connect(op,'output',destination_port=p,mapping={
                'address':Field('tag'),'write':True,'data':Field('bits'),'tag':Field('tag')})
            n.source('input',[MemoryRequest((i%64)*16,tag=p*256+i*4) for i in range(64)],port=p)
            n.sink('output',port=p)
        return n
    a,b=make(),make(True);keys=[a._key(n,p) for n in a._order() for p in range(getattr(a.units[n],'ports',1))]
    rng=np.random.default_rng(257);ready=rng.random((1000,len(keys)))>.2
    ready[70:350,keys.index(('output',1))]=False
    flush=np.zeros(1000,bool);flush[111]=True
    expected=a.run(1000,ready=ready,flush=flush,trace=True)
    actual=b.run(137,backend='numba',ready=ready[:137],flush=flush[:137],trace=True)
    actual+=b.run(81,backend='python',ready=ready[137:218],flush=flush[137:218],trace=True)
    actual+=b.run(782,backend='numba',ready=ready[218:],flush=flush[218:],trace=True)
    assert actual==expected
    for name,u in a.units.items():assert u.stats==b.units[name].stats
    assert a.units['input'].bank_conflicts==b.units['input'].bank_conflicts
    assert a.units['output'].dump_image()==b.units['output'].dump_image()
