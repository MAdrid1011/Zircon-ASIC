"""Exhaust all 65,536 raw unsigned 8x8 significand input pairs in RTL."""
import hashlib,json,shutil
from validate_rtl import ROOT,run,verilator_configuration

if __name__=='__main__':
    dest=ROOT/'build/bf16/cores';dest.mkdir(parents=True,exist_ok=True)
    run(['sbt',f'runMain zircon.GenerateBFloatCores {dest}'],dest/'generate.log',ROOT/'hardware')
    (dest/'core.cpp').write_text('''#include "VBFloatCores.h"
#include "verilated.h"
#include <iostream>
int main(int argc,char **argv) {
  Verilated::commandArgs(argc,argv); VBFloatCores d; d.clock=0;d.reset=0;
  for(unsigned a=0;a<256;++a) for(unsigned b=0;b<256;++b) {
    d.io_a=a;d.io_b=b;d.eval();
    if(d.io_direct!=a*b || d.io_booth!=a*b || d.io_native!=a*b) {
      std::cerr<<a<<" "<<b<<" "<<d.io_direct<<" "<<d.io_booth<<" "<<d.io_native;return 1;
    }
  } d.final();std::cout<<"PASS 65536 pairs, direct/Booth/native\\n";
}
    ''')
    version,flags=verilator_configuration()
    shutil.rmtree(dest/'obj',ignore_errors=True)
    run(['verilator',*flags,'--cc','--exe','--build','-j','4','--assert','-Wno-fatal','--top-module','BFloatCores',
         '--Mdir',str(dest/'obj'),str(dest/'Unit.sv'),str(dest/'core.cpp'),'-o','core'],dest/'compile.log')
    run([str(dest/'obj/core')],dest/'run.log')
    report=dict(pairs=65536,variants=['direct_dadda','booth_dadda','native'],discrepancies=0,verilator=version,
                rtl_sha256=hashlib.sha256((dest/'Unit.sv').read_bytes()).hexdigest(),
                compressor_sha256=hashlib.sha256((ROOT/'hardware/src/main/scala/zircon/Integer.scala').read_bytes()).hexdigest())
    (dest/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS BF16 significand cores')
