"""Run the complete Python suite and bind its result to packaged source hashes."""
import json,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from zircon_asic.evidence import implementation_hash
from zircon_asic.contract import contract_hash

if __name__=='__main__':
    before=implementation_hash();xml=ROOT/'build/pytest.xml';xml.parent.mkdir(exist_ok=True)
    result=subprocess.run([sys.executable,'-m','pytest','-q',f'--junitxml={xml}'],cwd=ROOT)
    suites=ET.parse(xml).getroot().findall('testsuite')
    report=dict(exit_code=result.returncode,implementation_hash=before,contract_hash=contract_hash(),
        unchanged_during_tests=before==implementation_hash(),collected=sum(int(s.get('tests',0)) for s in suites),
        skipped=sum(int(s.get('skipped',0)) for s in suites))
    (ROOT/'build/python-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    sys.exit(result.returncode)
