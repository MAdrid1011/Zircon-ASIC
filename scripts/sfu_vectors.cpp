#include "@TOP@.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>

int main(int argc,char **argv) {
  Verilated::commandArgs(argc,argv);
  if(argc!=3) return 2;
  const uint64_t latency=std::stoull(argv[2]);
  @TOP@ dut;
  std::ifstream input(argv[1],std::ios::binary);
  if(!input) return 2;
  struct Expected {uint32_t bits,flags,tag;uint64_t cycle;};
  std::deque<Expected> pending;
  std::array<uint32_t,4> row{};
  uint32_t accepted=0,delivered=0;
  dut.clock=0;dut.reset=1;dut.io_flush=0;dut.io_in_valid=0;dut.io_out_ready=1;
  dut.eval();dut.clock=1;dut.eval();dut.clock=0;dut.reset=0;
  bool have=bool(input.read(reinterpret_cast<char*>(row.data()),sizeof(row)));
  uint64_t cycles=0;
  while(have || !pending.empty()) {
    dut.io_in_valid=have;dut.io_in_bits_a=row[0];dut.io_in_bits_b=0;dut.io_in_bits_c=0;
    dut.io_in_bits_rounding=row[3];dut.io_in_bits_tag=accepted;dut.eval();
    if(dut.io_out_valid) {
      if(pending.empty()) {std::cerr<<"unexpected response\n";return 1;}
      auto e=pending.front();pending.pop_front();
      if(cycles!=e.cycle+latency) {std::cerr<<"latency mismatch at vector "<<e.tag<<"\n";return 1;}
      if(dut.io_out_bits_bits!=e.bits || dut.io_out_bits_flags!=e.flags || dut.io_out_bits_tag!=e.tag || dut.io_out_bits_remainder!=0) {
        std::cerr<<"vector "<<e.tag<<" cycle "<<cycles<<" expected "<<e.bits<<" "<<e.flags<<" actual "<<dut.io_out_bits_bits<<" "<<dut.io_out_bits_flags<<"\n";return 1;
      }
      ++delivered;
    }
    bool fire=have && dut.io_in_ready;
    if(have && !fire) {std::cerr<<"II=1 violated at cycle "<<cycles<<"\n";return 1;}
    if(fire) pending.push_back({row[1],row[2],accepted++,cycles});
    dut.clock=1;dut.eval();dut.clock=0;
    if(fire) have=bool(input.read(reinterpret_cast<char*>(row.data()),sizeof(row)));
    ++cycles;
    if(cycles>uint64_t(accepted)+256) {std::cerr<<"drain timeout\n";return 1;}
  }
  dut.final();std::cout<<"PASS "<<delivered<<" vectors, "<<cycles<<" cycles\n";
  return accepted==delivered ? 0 : 1;
}
