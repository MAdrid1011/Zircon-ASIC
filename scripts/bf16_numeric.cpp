#include "@TOP@.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>

// Six little-endian uint32 fields: a, b, c, rounding, expected bits, flags.
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 2) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 2;
    std::vector<std::array<uint32_t,6>> rows;
    std::array<uint32_t,6> row;
    while (input.read(reinterpret_cast<char*>(row.data()), sizeof(row))) rows.push_back(row);
    @TOP@ dut;
    dut.clock=0; dut.reset=1; dut.io_flush=0; dut.io_in_valid=0; dut.io_out_ready=1;
    dut.eval(); dut.clock=1; dut.eval(); dut.clock=0; dut.reset=0;
    size_t sent=0, received=0;
    uint64_t cycle=0;
    while (received < rows.size()) {
        if (++cycle > rows.size()*100+100) return 3;
        dut.clock=0; dut.io_in_valid=sent < rows.size();
        if (sent < rows.size()) {
            auto &v=rows[sent];
            dut.io_in_bits_a=v[0]; dut.io_in_bits_b=v[1]; dut.io_in_bits_c=v[2];
            dut.io_in_bits_rounding=v[3]; dut.io_in_bits_tag=sent;
        }
        dut.eval();
        if (dut.io_out_valid) {
            auto &v=rows[received];
            if (dut.io_out_bits_bits != v[4] || dut.io_out_bits_flags != v[5] ||
                dut.io_out_bits_tag != received || dut.io_out_bits_remainder != 0) {
                std::cerr << "vector=" << received << " cycle=" << cycle-1
                    << " inputs=" << v[0] << ',' << v[1] << ',' << v[2] << ',' << v[3]
                    << " expected=" << v[4] << ',' << v[5]
                    << " actual=" << dut.io_out_bits_bits << ',' << unsigned(dut.io_out_bits_flags) << '\n';
                return 1;
            }
            ++received;
        }
        if (dut.io_in_valid && dut.io_in_ready) ++sent;
        dut.clock=1; dut.eval();
    }
    dut.final();
    std::cout << "PASS " << received << " vectors " << cycle << " cycles\n";
}
