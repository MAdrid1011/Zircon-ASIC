# Implementation log

The accepted scope is a standalone Python arithmetic/cycle library and native
Chisel implementations of FP32/FP16/E4M3FN/E5M2/E2M1 add/mul/FMA/div and
INT8/16/32 add/mul/div. Exact numerical and cycle alignment are separate gates.

Current work: establish the neutral contract, Python numerical/cycle kernels,
Numba acceleration, Chisel datapaths, independent reference tests and RTL traces.
ASAP7 1 GHz validation is required before any timing-qualified claim.

No numerical, alignment, or physical-design result is assumed from the design plan.
