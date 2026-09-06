#include <stdint.h>
#include "softfloat.h"

/* Test-only C ABI. Call serially: SoftFloat's status registers are global. */
uint64_t zircon_oracle(int width, int op, uint32_t a, uint32_t b, uint32_t c, int rm) {
    softfloat_roundingMode = rm;
    softfloat_detectTininess = softfloat_tininess_afterRounding;
    softfloat_exceptionFlags = 0;
    uint32_t bits;
    if (width == 32) {
        float32_t x = {a}, y = {b}, z = {c}, r;
        switch (op) {
            case 0: r = f32_add(x, y); break;
            case 1: r = f32_mul(x, y); break;
            case 2: r = f32_mulAdd(x, y, z); break;
            default: r = f32_div(x, y); break;
        }
        bits = r.v;
    } else {
        float16_t x = {(uint16_t)a}, y = {(uint16_t)b}, z = {(uint16_t)c}, r;
        switch (op) {
            case 0: r = f16_add(x, y); break;
            case 1: r = f16_mul(x, y); break;
            case 2: r = f16_mulAdd(x, y, z); break;
            default: r = f16_div(x, y); break;
        }
        bits = r.v;
    }
    return bits | ((uint64_t)softfloat_exceptionFlags << 32);
}
