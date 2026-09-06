"""Make exported evidence paths portable without changing measured identities."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def portable(value):
    if isinstance(value, dict):
        return {portable(key): portable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [portable(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(ROOT) + "/", "")
    return value


def export_bytes(path):
    if path.suffix == ".json":
        return (json.dumps(portable(json.loads(path.read_text())), indent=2) + "\n").encode()
    return path.read_bytes()
