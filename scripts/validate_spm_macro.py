"""Test the exact Chisel SRAM adapter against pinned official macro models."""
from pathlib import Path
import hashlib,json,shutil
from validate_spm import ROOT
from validate_rtl import run,verilator_configuration


def validate():
    dest=ROOT/'build/spm-macro';dest.mkdir(exist_ok=True)
    run(['sbt',f'runMain zircon.GenerateSRAMBank {dest}'],dest/'generate.log',ROOT/'hardware')
    cpp=r'''#include "VSRAMBank.h"
#include "verilated.h"
#include <cassert>
#include <cstdint>
int main(int argc,char** argv) {
 Verilated::commandArgs(argc,argv); VSRAMBank d;
 auto cycle=[&](bool en,bool wr,unsigned addr,uint32_t data,unsigned mask) {
   d.clock=0; d.io_enable=en; d.io_write=wr; d.io_address=addr;
   d.io_data=data; d.io_mask=mask; d.eval();
   d.clock=1; d.eval(); d.clock=0; d.eval(); return uint32_t(d.io_result);
 };
 d.reset=0;
 for(unsigned addr: {0u,1u,511u,1023u}) {
   for(unsigned mask=0;mask<16;mask++) {
     cycle(true,true,addr,0xffffffff,15);
     uint32_t expected=0xffffffff;
     cycle(true,true,addr,0,mask);
     for(unsigned b=0;b<4;b++) if(mask&(1u<<b)) expected&=~(255u<<(b*8));
     assert(cycle(true,false,addr,0,0)==expected);
     assert(cycle(false,true,addr,0x12345678,15)==expected);
     assert(cycle(true,false,addr,0,0)==expected);
     cycle(true,true,addr,0xa593e71b,mask);
     for(unsigned b=0;b<4;b++) if(mask&(1u<<b)) expected=(expected&~(255u<<(b*8)))|(0xa593e71bu&(255u<<(b*8)));
     assert(cycle(true,false,addr,0,0)==expected);
   }
 }
 d.final();
}
'''
    (dest/'test.cpp').write_text(cpp)
    ver,flags=verilator_configuration()
    sources=[dest/'Bank.sv',*sorted((ROOT/'build/ihp/verilog').glob('*.v'))]
    shutil.rmtree(dest/'obj',ignore_errors=True)
    run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','--timing','-DFUNCTIONAL','-Wno-fatal',
         '--top-module','SRAMBank','--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17',*map(str,sources),str(dest/'test.cpp'),'-o','test'],dest/'compile.log')
    run([str(dest/'obj/test')],dest/'run.log')
    report=dict(passed=True,verilator=ver,addresses=[0,1,511,1023],masks=list(range(16)),
                coverage=['overwrite ones with zero','masked preservation','zero mask','consecutive reads/writes','disabled write'],
                sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (dest/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS official macro / Chisel adapter: all 16 byte masks, four boundary rows')

if __name__=='__main__':validate()
