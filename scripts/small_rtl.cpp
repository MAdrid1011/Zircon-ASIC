#include "@TOP@.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>
#include <cstdlib>
int main(int argc,char **argv) {
  if(argc!=8) return 2;
  Verilated::commandArgs(argc,argv);
  int w=std::atoi(argv[1]),op=std::atoi(argv[2]),rm=std::atoi(argv[3]),latency=std::atoi(argv[4]);
  uint64_t begin=std::strtoull(argv[5],nullptr,10),end=std::strtoull(argv[6],nullptr,10),n=end-begin,mask=(1<<w)-1;
  std::vector<uint16_t> ref(n); std::ifstream file(argv[7],std::ios::binary);
  file.read(reinterpret_cast<char*>(ref.data()),n*2);if(!file)return 3;
  @TOP@ dut;dut.clock=0;dut.reset=1;dut.io_flush=0;dut.io_in_valid=0;dut.io_out_ready=1;
  dut.eval();dut.clock=1;dut.eval();dut.clock=0;dut.reset=0;
  uint64_t offered=0,done=0,cycle=0;
  std::vector<uint64_t> accepted(n);
  while(done<n) {
    uint64_t i=begin+offered;
    dut.clock=0;dut.io_in_valid=offered<n;
    dut.io_in_bits_a=(op==2?i>>(2*w):i>>w)&mask;
    dut.io_in_bits_b=(op==2?i>>w:i)&mask;
    dut.io_in_bits_c=op==2?i&mask:0;dut.io_in_bits_rounding=rm;dut.io_in_bits_tag=offered;
    dut.eval();
    if(dut.io_in_valid&&dut.io_in_ready)accepted[offered++]=cycle;
    if(dut.io_out_valid) {
      if(dut.io_out_bits_tag!=done || dut.io_out_bits_bits!=(ref[done]&255) || dut.io_out_bits_flags!=(ref[done]>>8) || cycle-accepted[done]!=uint64_t(latency)) {
        std::cerr<<"divergence index="<<begin+done<<" rm="<<rm<<" cycle="<<cycle<<" got="<<unsigned(dut.io_out_bits_bits)<<","<<unsigned(dut.io_out_bits_flags)<<" expected="<<unsigned(ref[done]&255)<<","<<unsigned(ref[done]>>8)<<"\n";return 1;
      }
      ++done;
    }
    dut.clock=1;dut.eval();++cycle;
    if(cycle>(n+2)*uint64_t(latency+1))return 4;
  }
  dut.final();return 0;
}
