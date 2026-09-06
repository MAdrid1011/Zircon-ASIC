"""Run after installing zircon-asic; no hardware toolchain is needed."""
from zircon_asic import FP32Fma, Inputs, Request

unit = FP32Fma()
print(unit.describe())
print(unit.compute(Request(0x3f800000, 0x40000000, 0x40400000)))
print(unit.compute_batch([0x3f800000] * 4, 0x40000000, 0x40400000))

pending = [Request(0x3f800000, 0x40000000, 0x40400000, tag=i) for i in range(4)]
for cycle in range(unit.timing.latency + 12):
    request = pending[0] if pending else None
    ports = unit.step(Inputs(request, out_ready=cycle not in [8, 9, 10]))
    if ports.accepted:
        pending.pop(0)
        print("accepted", cycle, request.tag)
    if ports.delivered:
        print("delivered", cycle, ports.response)
