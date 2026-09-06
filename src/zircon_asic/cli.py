"""Command-line entry point for standalone arithmetic and contract inspection."""
import argparse
import json
from dataclasses import asdict
from .units import FloatingPointUnit,IntegerUnit
from .types import Request,Rounding
from .contract import contract


def main():
    p=argparse.ArgumentParser(prog="zircon")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("list",help="list supported implementations")
    for command in ("compute","describe"):
        q=sub.add_parser(command)
        q.add_argument("unit",help="e.g. fp32.fma or int8.div")
        q.add_argument("--unsigned",action="store_true")
        if command=="compute":
            for operand in ("a","b","c"):q.add_argument("--"+operand,type=lambda x:int(x,0),default=0)
            q.add_argument("--rounding",choices=[r.name for r in Rounding],default="RNE")
    args=p.parse_args()
    if args.command=="list":print("\n".join(contract()["units"]));return
    try:
        name,op=args.unit.split(".")
        unit=IntegerUnit(int(name[3:]),op,signed=not args.unsigned) if name.startswith("int") else FloatingPointUnit(name,op)
    except (KeyError,ValueError) as e:p.error(str(e))
    if args.command=="describe":result=unit.describe()
    else:
        r=unit.compute(Request(args.a,args.b,args.c,Rounding[args.rounding]));result=asdict(r);result["hex"]=hex(r.bits)
    print(json.dumps(result,indent=2))


if __name__=="__main__":main()
