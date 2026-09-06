"""Fetch pinned public SRAM views, retaining license notices and content hashes."""
from pathlib import Path
import hashlib, json, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT/'src/zircon_asic/data/spm.json').read_text())['ihp']
DEST = ROOT/'build/ihp'

def fetch():
    macro, commit = SPEC['macro'], SPEC['commit']
    names = [f'verilog/{macro}.v', 'verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v',
             'verilog/RM_IHPSG13_1P_core_behavioral.v',f'lef/{macro}.lef',f'gds/{macro}.gds']
    names += [f'lib/{macro}_{corner}.lib' for corner in ('typ_1p20V_25C','slow_1p08V_125C')]
    base = f'https://raw.githubusercontent.com/IHP-GmbH/IHP-Open-PDK/{commit}/ihp-sg13g2/libs.ref/sg13g2_sram/'
    def get(name):
        p = DEST/name; p.parent.mkdir(parents=True,exist_ok=True)
        data = urllib.request.urlopen(base+name).read()
        p.write_bytes(data)
        return name,hashlib.sha256(data).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as pool: hashes = dict(pool.map(get,names))
    report = dict(commit=commit,source=base,files=hashes)
    (DEST/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return report

if __name__ == '__main__': fetch()
