"""Immutable complete FP4 truth tables, packaged with the simulator."""
from functools import lru_cache
from importlib.resources import files
import json
import struct
from .types import Response,Flags,Rounding


@lru_cache(None)
def table():
    root=files("zircon_asic").joinpath("data")
    return root.joinpath("fp4.bin").read_bytes(),json.loads(root.joinpath("fp4-layout.json").read_text())["offsets"]


def fp4_compute(op,req):
    rm=int(Rounding(req.rounding))
    if not (0<=req.a<16 and 0<=req.b<16 and (op!="fma" or 0<=req.c<16)):
        raise ValueError("FP4 operands must be raw four-bit integers")
    data,offsets=table()
    index=(rm*16+int(req.a))*16+int(req.b)
    if op=="fma":index=index*16+int(req.c)
    packed=struct.unpack_from("<H",data,2*(offsets[op]+index))[0]
    return Response(packed&15,Flags(packed>>8),req.tag)
