"""Sharded small-format RTL exhaustive checking with a streaming C++ driver."""
from pathlib import Path
import argparse,json,re,subprocess,sys,time
from validate_rtl import ROOT,run,validate
sys.path.insert(0,str(ROOT/"src"))
from zircon_asic import contract


def check(name,op,shard=0,shards=1):
    # Establish an assertion-enabled build and independent random control check.
    validate(name,op,cycles=12000)
    dest=ROOT/f"build/rtl/{name}_{op}";sv=dest/"Unit.sv"
    top=re.search(r"^module (\w+)\(",sv.read_text(),re.M)[1]
    cpp=dest/"small_rtl.cpp";cpp.write_text((ROOT/"scripts/small_rtl.cpp").read_text().replace("@TOP@",f"V{top}"))
    run(["verilator","--cc","--exe","--build","-j","4","--assert","-Wno-fatal","--top-module",top,"--Mdir",str(dest/"small_obj"),"-CFLAGS","-std=c++17 -O3",str(sv),str(cpp),"-o","check"],dest/"small_compile.log")
    w=4 if name=="e2m1" else 8;lat=contract()["units"][f"{name}.{op}"]["latency"]
    total=1<<(w*(3 if op=="fma" else 2));begin,end=total*shard//shards,total*(shard+1)//shards
    start=time.monotonic();count=0;opid=["add","mul","fma","div"].index(op)
    for a in range(begin,end,65536):
        b=min(a+65536,end)
        for rm in range(5):
            ref=dest/"reference.bin"
            with ref.open("wb") as f:subprocess.run([str(ROOT/"build/small_oracle"),name,str(opid),str(a),str(b),str(rm)],stdout=f,check=True)
            subprocess.run([str(dest/"small_obj/check"),str(w),str(opid),str(rm),str(lat),str(a),str(b),str(ref)],check=True)
            count+=b-a
    result=dict(format=name,operation=op,backend="verilator",shard=shard,shards=shards,begin=begin,end=end,cases=count,seconds=time.monotonic()-start,numerical_discrepancy=0,cycle_discrepancy=0)
    (dest/f"exhaustive-{shard}-of-{shards}.json").write_text(json.dumps(result,indent=2)+"\n")
    print(result,flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--format",choices=["e2m1","e4m3fn","e5m2"]);p.add_argument("--op",choices=["add","mul","fma","div"]);p.add_argument("--shard",type=int,default=0);p.add_argument("--shards",type=int,default=1)
    a=p.parse_args()
    if not 0<=a.shard<a.shards:p.error("invalid shard")
    for name in [a.format] if a.format else ["e2m1","e4m3fn","e5m2"]:
        for op in [a.op] if a.op else ["add","mul","fma","div"]:check(name,op,a.shard,a.shards)
