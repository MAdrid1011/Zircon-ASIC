"""SPM functional access, shared arbitration, and a composed cycle network."""
from zircon_asic import SPM, MemoryRequest, Inputs, Network, INT32Mul, Field


def main():
    a=SPM(256);b=SPM(256)
    a.load_image(bytes(256));b.load_image(bytes(256))
    for address in range(0,64,4):a.compute(MemoryRequest(address,True,address+5))
    net=Network().add('input',a).add('mul',INT32Mul()).add('output',b)
    net.connect('input','mul',b=3).connect('mul','output',mapping={
        'address':Field('tag'),'write':True,'data':Field('bits'),'tag':Field('tag')})
    net.source('input',[MemoryRequest(address,tag=address) for address in range(0,64,4)]).sink('output')
    responses=net.run(32,backend='python')['output']
    assert responses[0][0]==7
    assert b.compute(MemoryRequest(0)).bits==15
    print('First write confirmation:',responses[0])
    shared=SPM(256,32,2,2);shared.load_image(bytes(256))
    first=shared.step((Inputs(MemoryRequest(0)),Inputs(MemoryRequest(0))))
    assert first[0].accepted and not first[1].accepted
    second=shared.step((Inputs(),Inputs(MemoryRequest(0))))
    assert second[1].accepted
    print('Shared bank grants:',[o.accepted for o in first],[o.accepted for o in second])

if __name__=='__main__':main()
