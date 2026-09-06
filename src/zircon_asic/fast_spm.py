"""Optional compiled byte-level storage primitives."""
import numpy as np
from numba import njit
from .spm import MemoryBatchResponse, UninitializedReadError


@njit(cache=True)
def access(memory, initialized, word_bytes, address, write, data, mask):
    if address + word_bytes > len(memory): return np.uint64(0), 1
    if address % word_bytes: return np.uint64(0), 2
    bits=np.uint64(0)
    for j in range(word_bytes):
        at=int(address)+j
        if write:
            if (mask >> np.uint64(j)) & np.uint64(1):
                memory[at]=(data >> np.uint64(8*j)) & np.uint64(255)
                initialized[at]=True
        else:
            if not initialized[at]: raise ValueError("SPM read of uninitialized bytes")
            bits |= np.uint64(memory[at]) << np.uint64(8*j)
    return bits,0


@njit(cache=True)
def _batch(memory,initialized,word_bytes,reqs):
    out=np.zeros((len(reqs),2),np.uint64)
    for i in range(len(reqs)):
        a,w,d,m,t=reqs[i]
        out[i,0],out[i,1]=access(memory,initialized,word_bytes,a,w,d,m)
    return out


def batch(spm,requests,shape):
    reqs=np.array([[r.address,r.write,r.data,r.mask if r.mask is not None else (1<<spm.word_bytes)-1,r.tag] for r in requests],np.uint64).reshape(-1,5)
    try: out=_batch(spm.memory,spm.initialized,spm.word_bytes,reqs)
    except ValueError as e: raise UninitializedReadError(str(e)) from e
    return MemoryBatchResponse(out[:,0].reshape(shape),reqs[:,4].astype(np.uint32).reshape(shape),reqs[:,1].astype(bool).reshape(shape),out[:,1].astype(np.uint8).reshape(shape))
