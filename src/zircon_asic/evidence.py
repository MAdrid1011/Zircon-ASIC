"""Packaged qualification is valid only for the measured implementation."""
import hashlib
import json
from importlib.resources import files
from .contract import contract_hash


def implementation_hash() -> str:
    root = files("zircon_asic")
    digest = hashlib.sha256()
    for directory in [root, root.joinpath("data")]:
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            if not path.is_file() or path.name == "qualification.json": continue
            if not path.name.endswith((".py", ".json", ".bin")): continue
            digest.update((directory.name + "/" + path.name).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def qualification(format_name: str, operation: str, signed=True) -> dict:
    path = files("zircon_asic").joinpath("data/qualification.json")
    if not path.is_file(): return {"status": "unqualified"}
    record = json.loads(path.read_text())
    if record.get("contract_hash") != contract_hash() or record.get("implementation_hash") != implementation_hash():
        return {"status": "unqualified", "reason": "implementation differs from qualification evidence"}
    key = f"{format_name}.{operation}" + (".unsigned" if not signed else "")
    return record.get("units", {}).get(key, {"status": "unqualified"})
