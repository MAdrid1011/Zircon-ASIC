#include "@TOP@.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 3) return 2;
    std::ifstream stimulus(argv[1]); std::ofstream trace(argv[2]);
    @TOP@ dut;
    dut.clock=0; dut.reset=1; dut.io_flush=0; dut.io_in_valid=0; dut.io_out_ready=0;
    dut.eval(); dut.clock=1; dut.eval(); dut.clock=0; dut.reset=0; dut.eval();
    uint64_t rst,flush,valid,a,b,c,rm,tag,ready;
    while(stimulus >> rst >> flush >> valid >> a >> b >> c >> rm >> tag >> ready) {
        dut.clock=0; dut.reset=rst; dut.io_flush=flush;
        dut.io_in_valid=valid; dut.io_in_bits_a=a; dut.io_in_bits_b=b; dut.io_in_bits_c=c;
        dut.io_in_bits_rounding=rm; dut.io_in_bits_tag=tag; dut.io_out_ready=ready;
        dut.eval();
        trace << unsigned(dut.io_in_ready) << ' ' << unsigned(dut.io_out_valid) << ' '
              << uint64_t(dut.io_out_bits_bits) << ' ' << unsigned(dut.io_out_bits_flags) << ' '
              << uint64_t(dut.io_out_bits_tag) << ' ' << uint64_t(dut.io_out_bits_remainder) << ' '
              << uint64_t(dut.io_stageValid) << ' ' << unsigned(dut.io_occupancy) << ' '
              << unsigned(dut.io_phase) << ' ' << unsigned(dut.io_iteration) << '\n';
        dut.clock=1; dut.eval();
    }
    dut.final();
    return 0;
}
