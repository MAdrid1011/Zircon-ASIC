"""Regenerate packed FP4 tables from the integer production kernel."""
from pathlib import Path
import json,struct,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from zircon_asic.formats import E2M1
from zircon_asic.numeric import float_compute
from zircon_asic.types import Request,Rounding

data=bytearray();offsets={}
for op in ("add","mul","fma","div"):
    offsets[op]=len(data)//2
    for rm in Rounding:
        for a in range(16):
            for b in range(16):
                for c in range(16) if op=="fma" else [0]:
                    r=float_compute(E2M1,op,Request(a,b,c,rm))
                    data+=struct.pack("<H",r.bits|(int(r.flags)<<8))
(ROOT/"src/zircon_asic/data/fp4.bin").write_bytes(data)
(ROOT/"src/zircon_asic/data/fp4-layout.json").write_text(json.dumps(dict(version=1,offsets=offsets,entry="little-endian uint16: flags[12:8], bits[3:0]"),indent=2)+"\n")
