"""Bind numerical regression reports to actual package source content."""
from pathlib import Path
import json
from zircon_asic.evidence import implementation_hash
from zircon_asic import contract_hash

_passed, _skipped = [], []
_initial = None


def pytest_sessionstart(session):
    global _initial
    _initial = implementation_hash()


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed: _passed.append(report.nodeid)
    if report.skipped: _skipped.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    output = Path(__file__).resolve().parents[1] / "build/python-validation.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(dict(exit_code=int(exitstatus), collected=session.testscollected,
        passed=_passed, skipped=_skipped, implementation_hash=_initial,
        unchanged_during_tests=_initial == implementation_hash(), contract_hash=contract_hash()), indent=2) + "\n")
