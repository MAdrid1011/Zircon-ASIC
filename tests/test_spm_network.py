import copy
import numpy as np
import pytest
from zircon_asic import *


def network(ports=3):
    s=SPM(256,32,2,ports);s.load_image(bytes(256))
    net=Network().add('mem',s)
    for p in range(ports):
        reqs=[MemoryRequest((k%16)*4,k%3!=0,(k*31+p)&0xffffffff,tag=p*1000+k) for k in range(120)]
        net.source('mem',reqs,port=p).sink('mem',port=p)
    return net


def test_mixed_stream_and_order():
    for reverse in (False,True):
        a=SPM(64);a.load_image(bytes(64));a.compute(MemoryRequest(0,True,17))
        b=SPM(64);b.load_image(bytes(64))
        nodes=[('a',a),('mul',INT32Mul()),('b',b)]
        n=Network()
        for k,u in nodes[::(-1 if reverse else 1)]:n.add(k,u)
        n.connect('a','mul',b=3).connect('mul','b',mapping={'address':0,'write':True,'data':Field('bits'),'tag':Field('tag')})
        n.source('a',[MemoryRequest(0,tag=7)]).sink('b')
        result=n.run(12)
        assert result['b'][0][0]==7  # 2 + INT32Mul(3) + 2
        assert b.compute(MemoryRequest(0)).bits==51


def test_numba_cycle_and_switch():
    pytest.importorskip('numba')
    rng=np.random.default_rng(917)
    ready=rng.random((700,3))>.3;reset=np.zeros(700,bool);flush=reset.copy();flush[[48,233]]=True;reset[421]=True
    a=network();b=network()
    # Python public run takes per-cycle scalar ready or a static port dictionary.
    expected=[]
    for k in range(700):
        expected.append(a.step(ready={a._key('mem',p):bool(ready[k,p]) for p in range(3)},reset=bool(reset[k]),flush=bool(flush[k])))
    got=b.run(300,backend='numba',ready=ready[:300],reset=reset[:300],flush=flush[:300],trace=True)
    for k in range(300,400):
        got.append(b.step(ready={b._key('mem',p):bool(ready[k,p]) for p in range(3)},reset=bool(reset[k]),flush=bool(flush[k])))
    got+=b.run(300,backend='numba',ready=ready[400:],reset=reset[400:],flush=flush[400:],trace=True)
    assert got==expected
    assert b.units['mem'].debug_state()==a.units['mem'].debug_state()
    assert b.units['mem'].dump_image()==a.units['mem'].dump_image()
    assert b.units['mem'].stats==a.units['mem'].stats
    assert b.units['mem'].bank_accesses==a.units['mem'].bank_accesses
    assert b.units['mem'].bank_conflicts==a.units['mem'].bank_conflicts
    assert b.units['mem'].credit_stalls==a.units['mem'].credit_stalls


def test_numba_batch():
    pytest.importorskip('numba')
    a=SPM(64,data_width=64);a.load_image(bytes(64));b=SPM(64,data_width=64);b.load_image(bytes(64))
    kwargs=dict(write=[1,0,1,0],data=np.array([0xffffffffffffffff,0,13,0],np.uint64),masks=[255,255,3,255],tags=[1,2,3,4])
    x=a.compute_batch([0,0,0,0],**kwargs,backend='python');y=b.compute_batch([0,0,0,0],**kwargs,backend='numba')
    for f in ('bits','tags','write','status'):assert np.array_equal(getattr(x,f),getattr(y,f))
    assert a.dump_image()==b.dump_image()


def test_numba_mixed():
    pytest.importorskip('numba')
    def make():
        a=SPM(64);a.load_image(bytes(range(64)));b=SPM(64);b.load_image(bytes(64))
        n=Network().add('a',a).add('mul',INT32Mul()).add('b',b)
        n.connect('a','mul',b=3).connect('mul','b',mapping={'address':Field('tag'),'write':True,'data':Field('bits'),'tag':Field('tag')})
        n.source('a',[MemoryRequest(k,tag=k) for k in range(0,64,4)]).sink('b');return n
    a,b=make(),make();assert a.run(60,trace=True)==b.run(60,trace=True,backend='numba')
    assert a.units['b'].dump_image()==b.units['b'].dump_image()


def test_reject_connection_cycle_and_bad_memory_response():
    n=network(1)
    with pytest.raises(ValueError):n.connect('mem','mem')
    a=SPM(64);a.load_image(bytes(64))
    n=Network().add('a',a).add('b',INT32Add()).connect('a','b').source('a',[MemoryRequest(0,True,1)])
    with pytest.raises(ValueError):n.run(5)
