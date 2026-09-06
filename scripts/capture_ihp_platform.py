"""Record the exact same-process standard-cell and extraction views used by ORFS."""
from pathlib import Path
import hashlib,json,subprocess
from ppa import IMAGE
from ppa_spm import ROOT

def capture():
    dest=ROOT/'build/ihp/platform';dest.mkdir(exist_ok=True)
    names=['lib/sg13g2_stdcell_typ_1p20V_25C.lib','lib/sg13g2_stdcell_slow_1p08V_125C.lib',
           'lef/sg13g2_stdcell.lef','lef/sg13g2_tech.lef','gds/sg13g2_stdcell.gds',
           'verilog/sg13g2_stdcell.v','IHP_rcx_patterns.rules','rcx_patterns.rules','setRC.tcl','config.mk','pdn.tcl']
    hashes={}
    for name in names:
        data=subprocess.check_output(['docker','run','--rm','--platform','linux/amd64',IMAGE,
            'cat','/OpenROAD-flow-scripts/flow/platforms/ihp-sg13g2/'+name])
        path=dest/name;path.parent.mkdir(exist_ok=True);path.write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    report=dict(image=IMAGE,files=hashes,source='ORFS SG13G2 platform views; distinct from unmodified upstream PDK files')
    (dest/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    return report

if __name__=='__main__':capture()
