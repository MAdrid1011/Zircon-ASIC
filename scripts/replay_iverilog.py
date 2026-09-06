"""Replay the exact saved cycle trace with an independent RTL simulator."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import re
import subprocess
from validate_rtl import run


def replay(directory):
    directory=Path(directory).resolve()
    manifest=json.loads((directory/"manifest.json").read_text())
    width=int(manifest["format"][3:]) if manifest["format"].startswith("int") else manifest["contract"]["formats"][manifest["format"]]["width"]
    latency=manifest["latency"];counter=max(1,math.ceil(math.log2(latency+1)))
    sv=directory/"Unit.sv";top=re.search(r"^module (\w+)\(",sv.read_text(),re.M)[1]
    path=lambda name:json.dumps(str(directory/name),ensure_ascii=False)
    bench=f'''module replay;
reg clock=0,reset=1,io_flush=0,io_in_valid=0,io_out_ready=0;
reg [{width-1}:0] io_in_bits_a=0,io_in_bits_b=0,io_in_bits_c=0;
reg [2:0] io_in_bits_rounding=0;
reg [31:0] io_in_bits_tag=0;
wire io_in_ready,io_out_valid;
wire [{width-1}:0] io_out_bits_bits,io_out_bits_remainder;
wire [4:0] io_out_bits_flags;
wire [31:0] io_out_bits_tag;
wire [{latency-1}:0] io_stageValid;
wire [{counter-1}:0] io_occupancy,io_phase,io_iteration;
{top} dut(.*);
integer source,destination,count;
initial begin
 source=$fopen({path('stimulus.txt')},"r");
 destination=$fopen({path('icarus.trace')},"w");
 #1; clock=1; #1; clock=0; reset=0; #1;
 while(!$feof(source)) begin
  count=$fscanf(source,"%d %d %d %d %d %d %d %d %d\\n",reset,io_flush,io_in_valid,io_in_bits_a,io_in_bits_b,io_in_bits_c,io_in_bits_rounding,io_in_bits_tag,io_out_ready);
  if(count==9) begin
   #1;
   $fdisplay(destination,"%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",io_in_ready,io_out_valid,io_out_bits_bits,io_out_bits_flags,io_out_bits_tag,io_out_bits_remainder,io_stageValid,io_occupancy,io_phase,io_iteration);
   clock=1; #1; clock=0;
  end
 end
 $fclose(source);$fclose(destination);$finish;
end
endmodule
'''
    (directory/"icarus.sv").write_text(bench)
    run(["iverilog","-g2012","-s","replay","-o",str(directory/"icarus.bin"),str(sv),str(directory/"icarus.sv")],directory/"icarus-compile.log")
    run(["vvp",str(directory/"icarus.bin")],directory/"icarus-run.log")
    expected=(directory/"python.trace").read_text().splitlines()
    actual=(directory/"icarus.trace").read_text().splitlines()
    if len(expected)!=len(actual):raise AssertionError("Icarus trace length mismatch")
    for cycle,(p,r) in enumerate(zip(expected,actual)):
        p=p.split();r=r.split();fields=[0,1,6,7,8,9]+([2,3,4,5] if p[1]=="1" else [])
        if any(p[i]!=r[i] for i in fields):
            failure=dict(cycle=cycle,python=p,icarus=r)
            (directory/"icarus-failure.json").write_text(json.dumps(failure,indent=2))
            raise AssertionError(failure)
    report=dict(cycles=len(actual),rtl_sha256=hashlib.sha256(sv.read_bytes()).hexdigest(),
        stimulus_sha256=hashlib.sha256((directory/"stimulus.txt").read_bytes()).hexdigest(),
        iverilog=subprocess.check_output(["iverilog","-V"],stderr=subprocess.STDOUT,text=True).splitlines()[0],discrepancies=0)
    (directory/"icarus-replay.json").write_text(json.dumps(report,indent=2)+"\n")
    print(f"PASS Icarus {directory.name}: {len(actual)} cycles",flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("directories",nargs="+")
    for directory in parser.parse_args().directories:replay(directory)
