"""A caller-owned clocked controller performs a read/modify/write sequence."""
from zircon_asic import SPM, MemoryRequest, Inputs


def main():
    memory = SPM()
    memory.load_image((17).to_bytes(4, "little"))
    phase = "read"
    pending = MemoryRequest(0, tag=7)
    for cycle in range(20):
        # Only the controller chooses addresses and the next operation. Keep
        # pending unchanged whenever the memory does not accept the request.
        output = memory.step(Inputs(pending, out_ready=True))
        if output.accepted:
            pending = None
        if output.delivered:
            if phase == "read":
                assert output.response.bits == 17
                pending = MemoryRequest(0, write=True, data=51, tag=8)
                phase = "write"
            else:
                assert output.response.write
                assert memory.compute(MemoryRequest(0)).bits == 51
                print(f"Write acknowledged at cycle {cycle}")
                return
    raise AssertionError("controller did not complete")


if __name__ == "__main__":
    main()
