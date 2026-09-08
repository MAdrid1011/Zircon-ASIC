"""Create the local SPM RTL/evidence bundle after both profiles qualify."""
from pathlib import Path
import hashlib,json,zipfile,shutil,tomllib
from collect_spm_reports import ROOT,main
from report_paths import export_bytes

def package():
    report=main();paths=set()
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    for key in ('4096:32:1:1','16384:32:4:4'):
        r=report['configurations'][key]
        if r['status']!='dual-verified':raise RuntimeError(f'{key}: qualification incomplete')
        rtl=(ROOT/r['alignment_source']).parent;phy=(ROOT/r['physical']['source']).parent
        paths.add(phy/'results/6_final.odb')
        for folder in (rtl,phy):
            for path in folder.rglob('*'):
                if path.is_file() and path.suffix in ('.json','.sv','.v','.tcl','.sdc','.spef','.rpt','.stim','.python','.rtl','.trace','.mk','.gds','.lef') and not {'obj','objects'}.intersection(path.parts):paths.add(path)
    paths.update((ROOT/'build/ihp').rglob('*'))
    paths.update((ROOT/'build/spm-macro').glob('*.json'))
    paths.update((ROOT/'scripts').glob('*.py'))
    paths.update((ROOT/'scripts').glob('*.tcl'))
    paths.update(ROOT/p for p in ('LICENSE','THIRD_PARTY_NOTICES.md','CONTRIBUTING.md','SECURITY.md'))
    paths.update((ROOT/'licenses').glob('*.txt'))
    paths.update(ROOT/p for p in ('reports/SPM.md','reports/spm-validation.json','src/zircon_asic/data/spm.json','src/zircon_asic/data/spm_qualification.json','docs/hardware/spm.md','examples/spm.py','examples/spm_controller.py'))
    target=ROOT/f'dist/zircon-asic-{version}-spm-evidence.zip';target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        hashes={}
        for path in sorted(paths):
            if not path.is_file():continue
            data=export_bytes(path);name=str(path.relative_to(ROOT));z.writestr(name,data);hashes[name]=hashlib.sha256(data).hexdigest()
        z.writestr('SHA256SUMS.json',json.dumps(hashes,indent=2))
    print(target,target.stat().st_size)
    for suffix in ('.jar','-sources.jar','.pom'):
        source=ROOT/'hardware/target/scala-2.13'/(f'zircon-asic_2.13-{version}'+suffix)
        shutil.copy2(source,ROOT/'dist'/source.name)
    artifacts=sorted(p for p in (ROOT/'dist').glob(f'*{version}*') if not p.name.startswith('SHA256SUMS'))
    (ROOT/f'dist/SHA256SUMS-{version}.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in artifacts if p.is_file()))

if __name__=='__main__':package()
