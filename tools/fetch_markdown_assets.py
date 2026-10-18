"""Explicit developer step; the app never downloads preview dependencies."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

PACKAGES={'mermaid':'12.2.0','katex':'0.19.0','dompurify':'3.4.17'}
ROOT=Path(__file__).resolve().parents[1]/'prompt_calculus_studio/assets/markdown'


def fetch():
    manifest=[]
    for name,version in PACKAGES.items():
        url=f'https://registry.npmjs.org/{name}/-/{name}-{version}.tgz'
        with urllib.request.urlopen(url,timeout=60) as response:raw=response.read()
        wanted={'mermaid':['dist/mermaid.min.js','LICENSE'],
                'katex':['dist/katex.min.js','dist/katex.min.css','LICENSE'],
                'dompurify':['dist/purify.min.js','LICENSE','LICENSE-MPL']}[name]
        files=[]
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as package:
            for item in package.getmembers():
                relative=item.name.removeprefix('package/')
                if not item.isfile() or not (relative in wanted or name=='katex' and relative.startswith('dist/fonts/') and relative.endswith('.woff2')):continue
                target=ROOT/name/relative.removeprefix('dist/')
                if not target.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Invalid archive path')
                data=package.extractfile(item).read();target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                files.append({'path':target.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(data).hexdigest()})
        manifest.append(dict(package=name,version=version,url=url,archive_sha256=hashlib.sha256(raw).hexdigest(),files=files))
    (ROOT/'DEPENDENCIES.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__=='__main__':fetch()
