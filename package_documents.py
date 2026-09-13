"""Package project documents and exact project sources, excluding local data."""
import shutil
import os
import sys
import zipfile
import json
import hashlib
import subprocess
from datetime import datetime,timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def bundle_documents(destination, desktop=False, release=None):
    from prompt_studio.releases import CURRENT
    release=release or CURRENT
    destination = Path(destination)
    for path in ROOT.iterdir():
        if path.is_file() and (path.suffix in ('.md', '.txt') or path.name == 'LICENSE'):
            shutil.copy2(path, destination/path.name)
    if (ROOT/'docs').is_dir():
        shutil.copytree(ROOT/'docs', destination/'docs', dirs_exist_ok=True)
    paths = [p for p in ROOT.iterdir() if p.is_file() and (p.suffix in ('.py','.ps1','.cmd','.vbs','.md','.txt') or p.name in ('LICENSE','.gitignore'))]
    for folder in ('prompt_studio','comfyui_prompt_studio','tests','docs'):
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    with zipfile.ZipFile(destination/'PromptStudio-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path, path.relative_to(ROOT))
    if desktop:
        licenses=destination/'third-party'; licenses.mkdir(exist_ok=True)
        for base in (ROOT/'vendor',ROOT/'.builder'/'Lib'/'site-packages'):
            for info in base.glob('*.dist-info'):
                if info.name.startswith(('pyside6','shiboken6','pyinstaller-')):
                    target=licenses/info.name; target.mkdir(exist_ok=True)
                    for path in info.rglob('*'):
                        if path.is_file() and (path.name=='METADATA' or 'licenses' in path.parts):
                            output=target/path.relative_to(info); output.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,output)
        python_license=Path(sys.executable).parent/'LICENSE.txt'
        if python_license.exists(): shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
    manifest=[dict(path=p.relative_to(ROOT).as_posix(),size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(set(paths))]
    (destination/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    def git(*args):
        try:
            value=subprocess.run([os.environ.get('PROMPT_STUDIO_BUILD_GIT','git'),*args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
            return value.stdout.decode('utf-8',errors='replace').strip() if value.returncode==0 else None
        except (OSError,subprocess.TimeoutExpired):return None
    previous=json.loads((destination/'BUILD_INFO.json').read_text(encoding='utf-8')) if (destination/'BUILD_INFO.json').exists() else {}
    info=dict(version=release.version,release=release.flag,built_utc=datetime.now(timezone.utc).isoformat(),git_head=git('rev-parse','HEAD'),working_changes=git('status','--porcelain'),source_sha256=hashlib.sha256((destination/'PromptStudio-source.zip').read_bytes()).hexdigest(),validation='docs/DEVELOPMENT_STATUS.md')
    if desktop:info['binary_git_head']=previous.get('binary_git_head',info['git_head'])
    (destination/'BUILD_INFO.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'BUILD_NOTICE.txt').write_text(
        'Prompt Studio '+release.version+'\n'
        '獨立 Alpha 測試包。來源與逐檔校驗見 BUILD_INFO.json、SOURCE_MANIFEST.json。\n'
        '目前驗證範圍及限制見 docs/DEVELOPMENT_STATUS.md；操作說明見 V081_ALPHA2.md。\n'
        '維護版本說明見 MAINTENANCE_081.md（若本版附有）。\n'
        'PromptStudio-source.zip 包含本包對應的專案原始碼與建置腳本，不含個人資料或第三方執行環境。\n'
        '專案授權見 LICENSE；第三方元件依各自授權，見 docs/LICENSING.md。\n'
        '正式對外發行前仍需完成 Qt 等第三方條款、通知與相應原始碼取得方式的完整稽核。\n', encoding='utf-8')
