"""Check local documentation links, anchors, and executable README examples."""
from pathlib import Path
import re,sys
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def anchors(text):
    used={};result=set()
    for title in re.findall(r'^#{1,6}\s+(.+)$',text,re.M):
        slug=re.sub(r'[^\w\- ]','',title.lower()).replace(' ','-')
        index=used.get(slug,0);used[slug]=index+1
        result.add(slug+(f'-{index}' if index else ''))
    return result


def main():
    pages=[ROOT/n for n in ('README.md','CONTRIBUTING.md','SECURITY.md','THIRD_PARTY_NOTICES.md')]
    pages+=sorted((ROOT/'docs').rglob('*.md'))+sorted((ROOT/'reports').glob('*.md'))
    checked=0
    for page in pages:
        text=page.read_text()
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            link=link.strip('<>')
            if re.match(r'[a-zA-Z]+:',link):continue
            name,_,anchor=unquote(link).partition('#')
            target=(page.parent/name).resolve() if name else page
            assert target.exists(),f'{page.relative_to(ROOT)}: missing {link}'
            if anchor:
                assert target.is_file() and anchor in anchors(target.read_text()),f'{page.relative_to(ROOT)}: missing anchor {link}'
            checked+=1
    for code in re.findall(r'```python\n(.*?)```',(ROOT/'README.md').read_text(),re.S):
        exec(compile(code,'README.md','exec'),{})
    print(f'PASS {len(pages)} documentation pages, {checked} local links, README Python examples')


if __name__=='__main__':main()
