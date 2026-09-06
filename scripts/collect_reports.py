"""Collect content-addressed evidence; never promote an unmeasured implementation."""
from pathlib import Path
import json
import hashlib
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from zircon_asic import contract,contract_hash
from zircon_asic.evidence import implementation_hash


def read(path):
    return json.loads(path.read_text())


def complete_exhaustive(records, name, op, backend, sha=None):
    matches=[r for r in records if r.get("format")==name and r.get("operation")==op and r.get("backend")==backend
             and (sha is None or r.get("rtl_sha256")==sha)
             and (backend!="numba" or r.get("implementation_hash")==implementation_hash())
             and r.get("discrepancies",r.get("numerical_discrepancy",-1))==0]
    width=4 if name=="e2m1" else 8
    end=1 << (width*(3 if op=="fma" else 2))
    cursor=0
    for r in sorted(matches,key=lambda r:r["begin"]):
        if r["begin"]<=cursor and r.get("cases")==5*(r["end"]-r["begin"]): cursor=max(cursor,r["end"])
    return cursor==end


def main():
    current=contract_hash();implementation=implementation_hash();records={};physical=[]
    for p in (ROOT/"build/ppa").rglob("physical.json"):
        d=read(p);record={k:v for k,v in d.items() if k!="metrics"}
        metrics=d.get("metrics",{})
        if "6_report.json" in metrics:
            stage,prefix="6_report.json","finish";level="detailed-route"
        elif "5_1_grt.json" in metrics:
            stage,prefix="5_1_grt.json","globalroute";level="global-route-estimated-RC"
        else:
            stage,prefix="3_3_place_gp.json","globalplace";level="placement-only"
        m=metrics.get(stage,{})
        record.update(source=str(p.relative_to(ROOT)),evaluation_level=level,
            area_um2=m.get(prefix+"__design__instance__area__stdcell"),
            setup_slack_ps=m.get(prefix+"__timing__setup__ws"),hold_slack_ps=m.get(prefix+"__timing__hold__ws"))
        record["timing_pass"]=d.get("exit_code")==0 and level!="placement-only" and all(record.get(k) is not None and record[k]>=0 for k in ("setup_slack_ps","hold_slack_ps"))
        if record.get("area_um2") is not None and record.get("latency") is not None:
            record["area_latency_um2_ns"]=record["area_um2"]*record["latency"]
        physical.append(record)
    alignments=[]
    for p in (ROOT/"build/rtl").glob("*/alignment.json"):
        d=read(p);d["source"]=str(p.relative_to(ROOT));d["current_contract"]=d.get("contract_hash")==current;alignments.append(d)
    exhaustive=[]
    for root,pattern in [(ROOT/"build/exhaustive","*.json"),(ROOT/"build/rtl","*/exhaustive-*.json")]:
        for p in root.glob(pattern):
            d=read(p);d["source"]=str(p.relative_to(ROOT));exhaustive.append(d)
    py_path=ROOT/"build/python-validation.json"
    python=read(py_path) if py_path.exists() else {}
    python_pass=(python.get("exit_code")==0 and python.get("implementation_hash")==implementation
                 and python.get("contract_hash")==current and python.get("unchanged_during_tests")
                 and python.get("collected",0)>=100 and not python.get("skipped"))
    testfloat=[read(p) for p in (ROOT/"build/testfloat").glob("*.json")]
    for key,spec in contract()["units"].items():
        name,op=key.split(".")
        for signed in ([True,False] if name.startswith("int") else [True]):
            record_key=key+(".unsigned" if not signed else "")
            rtl=ROOT/f"build/rtl/{name}_{op}{'_unsigned' if not signed else ''}/Unit.sv"
            sha=hashlib.sha256(rtl.read_bytes()).hexdigest() if rtl.exists() else None
            matches=[d for d in alignments if d.get("format")==name and d.get("operation")==op and d.get("signed")==signed
                     and d["current_contract"] and d.get("rtl_sha256")==sha and d.get("cycle_discrepancy")==0]
            ppa=[d for d in physical if d.get("unit")==record_key and d.get("rtl_sha256")==sha
                 and d.get("source_snapshot") and d.get("corner")=="TC" and d.get("clock_period_ps")==1000]
            measured=sorted(ppa,key=lambda d:(d["timing_pass"],d["evaluation_level"]=="detailed-route"),reverse=True)
            selected=measured[0] if measured else None
            numeric=bool(python_pass)
            if name in ("e2m1","e4m3fn","e5m2"):
                numeric &= complete_exhaustive(exhaustive,name,op,"numba") and complete_exhaustive(exhaustive,name,op,"verilator",sha)
            elif name in ("fp16","fp32"):
                numeric &= any(t.get("format")==name and t.get("operation")==op and t.get("rtl_sha256")==sha
                               and t.get("numerical_discrepancy")==0 and t.get("cycle_discrepancy")==0 for t in testfloat)
            cycle=bool(matches);timing=bool(selected and selected["timing_pass"])
            status="dual-verified" if numeric and cycle and timing else "cycle-verified" if numeric and cycle else "unqualified"
            records[record_key]=dict(status=status,qualified=status=="dual-verified",numerics="passed" if numeric else "not_currently_verified",
                cycle_alignment="passed" if cycle else "not_currently_verified",asap7_1ghz="passed" if timing else "not_qualified",
                latency=spec["latency"],initiation_interval=1 if spec["kind"]=="elastic" else spec["latency"],
                rtl_sha256=sha,physical=selected)
    report=dict(contract_hash=current,implementation_hash=implementation,units=records,python=python,
                alignment=alignments,testfloat=testfloat,exhaustive=exhaustive,physical=physical)
    benchmark=ROOT/"build/benchmark.json"
    if benchmark.exists():report["benchmark"]=read(benchmark)
    dest=ROOT/"reports";dest.mkdir(exist_ok=True)
    (dest/"validation.json").write_text(json.dumps(report,indent=2)+"\n")
    package=dict(contract_hash=current,implementation_hash=implementation,units=records)
    (ROOT/"src/zircon_asic/data/qualification.json").write_text(json.dumps(package,indent=2)+"\n")
    lines=["# 实测配置清单","",f"共同契约 SHA-256：`{current}`。", "",
           "典型角 TC，ASAP7 RVT，0.70 V，0 °C，1 GHz；IO 预算 200 ps，时钟不确定度 50 ps。", "",
           "`dual-verified` 表示数值、逐周期与上述物理条件均通过。全局布线使用估算 RC；详细布线结果明确单列，均不代表流片签核或形式等价验证。", "",
           "| 配置 | L / II | 数值 | 周期 | 面积 µm² | setup / hold ps | 物理级别 | 状态 |",
           "|---|---:|---|---|---:|---:|---|---|"]
    for key,r in records.items():
        p=r["physical"] or {};area=p.get("area_um2");su=p.get("setup_slack_ps");ho=p.get("hold_slack_ps")
        lines.append(f"| {key} | {r['latency']} / {r['initiation_interval']} | {r['numerics']} | {r['cycle_alignment']} | {area if area is not None else '—'} | {f'{su:.2f} / {ho:.2f}' if su is not None and ho is not None else '—'} | {p.get('evaluation_level','未测量')} | {r['status']} |")
    lines += ["", "历史候选和失败测量保留于 `validation.json` 的 `physical` 字段。候选仅在正确性、时序和吞吐硬约束通过后比较面积×延迟；差异不足 5% 时优先较小面积。当前未通过配置保持未合格，不把初始结构声明为全局最优。", ""]
    (dest/"CONFIGURATIONS.md").write_text("\n".join(lines))
    print(f"Wrote {dest}: {sum(r['qualified'] for r in records.values())}/{len(records)} dual-verified configurations")


if __name__=="__main__":main()
