"""Versioned, tool-independent shared implementation contract."""
import hashlib
import json
from importlib.resources import files
from dataclasses import dataclass


def contract() -> dict:
    return json.loads(files("zircon_asic").joinpath("data/contract.json").read_text())


def contract_hash() -> str:
    payload = json.dumps(contract(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class Timing:
    latency: int
    kind: str
    phases: tuple[str, ...]
    variant: str
    matched: bool = True

    @property
    def capacity(self) -> int:
        return self.latency if self.kind == "elastic" else 1

    @property
    def initiation_interval(self) -> int:
        return 1 if self.kind == "elastic" else self.latency

    def __post_init__(self):
        if self.latency < 1 or self.kind not in ("elastic", "iterative"):
            raise ValueError("positive latency and elastic/iterative kind required")
        if len(self.phases) != self.latency:
            raise ValueError("one phase description per latency cycle is required")


def timing_for(format_name: str, op: str) -> Timing:
    item = contract()["units"][f"{format_name}.{op}"]
    return Timing(item["latency"], item["kind"], tuple(item["phases"]), item["variant"])

