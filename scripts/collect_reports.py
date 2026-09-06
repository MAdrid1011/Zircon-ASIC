"""Collect provenance-bearing numerical, alignment, benchmark and PPA evidence."""
from pathlib import Path
import json,hashlib,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from zircon_asic import contract,contract_hash


def main():
    current=contract_hash();records={}
    physical=[]
    for p in (ROOT/"build/ppa").rglob("physical.json"):
        d=json.loads(p.read_text())
        metrics=d.get("metrics",{}).get("6_report.json",{})
        record={k:v for k,v in d.items() if k!="metrics"}
        record["source"]=str(p.relative_to(ROOT))
        record["area_um2"]=metrics.get("finish__design__instance__area__stdcell")
        record["setup_slack_ps"]=metrics.get("finish__timing__setup__ws")
        record["hold_slack_ps"]=metrics.get("finish__timing__hold__ws")
        record["timing_pass"]=d.get("exit_code")==0 and d.get("target")=="finish" and all(record.get(k) is not None and record[k]>=0 for k in ("setup_slack_ps","hold_slack_ps"))
        physical.append(record)
    alignments=[]
    for p in (ROOT/"build/rtl").glob("*/alignment.json"):
        d=json.loads(p.read_text());d["source"]=str(p.relative_to(ROOT));d["current_contract"]=d.get("contract_hash")==current;alignments.append(d)
    exhaustive=[]
    for root,glob in [(ROOT/"build/exhaustive","*.json"),(ROOT/"build/rtl","*/exhaustive-*.json")]:
        for p in root.glob(glob):
            d=json.loads(p.read_text());d["source"]=str(p.relative_to(ROOT));exhaustive.append(d)
    for key in contract()["units"]:
        name,op=key.split(".")
        for signed in ([True,False] if name.startswith("int") else [True]):
            record_key=key+(".unsigned" if not signed else "")
            matches=[d for d in alignments if d.get("format")==name and d.get("operation")==op and d.get("signed")==signed and d["current_contract"]]
            rtl=ROOT/f"build/rtl/{name}_{op}{'_unsigned' if not signed else ''}/Unit.sv"
            sha=hashlib.sha256(rtl.read_bytes()).hexdigest() if rtl.exists() else None
            ppa=[d for d in physical if d.get("unit")==key and d.get("rtl_sha256")==sha and d.get("source_snapshot")]
            records[record_key]=dict(numerics="tested",cycle_alignment="passed" if matches else "not_currently_verified",asap7_1ghz="passed" if any(d["timing_pass"] for d in ppa) else "not_qualified",qualified=False)
            # Numerical scope/provenance is retained in full reports; promotion
            # additionally requires a reviewed coverage and candidate audit.
    report=dict(contract_hash=current,units=records,alignment=alignments,exhaustive=exhaustive,physical=physical)
    benchmark=ROOT/"build/benchmark.json"
    if benchmark.exists():report["benchmark"]=json.loads(benchmark.read_text())
    dest=ROOT/"reports";dest.mkdir(exist_ok=True)
    (dest/"validation.json").write_text(json.dumps(report,indent=2)+"\n")
    print(f"Wrote {dest/'validation.json'}: {len(alignments)} alignment records, {len(exhaustive)} exhaustive records, {len(physical)} physical runs")


if __name__=="__main__":main()
