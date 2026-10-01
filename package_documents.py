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


def package_manifest(destination,kind):
    """Hash delivered files, separately from the source-only manifest."""
    destination=Path(destination)
    info=json.loads((destination/'BUILD_INFO.json').read_text(encoding='utf-8'))
    files=[]
    for path in sorted(destination.rglob('*')):
        if path.is_symlink() or path.is_junction():raise ValueError('Package contains a redirected entry')
        relative=path.relative_to(destination)
        if any(p.casefold() in {'data','local_library.json','credentials','__pycache__'} for p in relative.parts):raise ValueError('Package contains local data')
        if path.is_file() and relative.as_posix()!='PACKAGE_MANIFEST.json':
            with path.open('rb') as stream: checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            files.append(dict(path=relative.as_posix(),size=path.stat().st_size,sha256=checksum))
    value=dict(format='pcs-package-1',kind=kind,version=info['version'],release=info['release'],git_head=info['git_head'],files=files)
    (destination/'PACKAGE_MANIFEST.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def git_output(root,*args):
    value=subprocess.run([os.environ.get('PROMPT_STUDIO_BUILD_GIT','git'),*args],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
    if value.returncode:raise ValueError('無法核對 Git 來源，已停止打包。')
    return value.stdout.decode('utf-8')


def source_paths(root=ROOT):
    """Package reviewed repository files, never arbitrary files beside them."""
    root=Path(root).resolve()
    if Path(git_output(root,'rev-parse','--show-toplevel').strip()).resolve()!=root:
        raise ValueError('建置來源必須是獨立 Git 根目錄。')
    blocked={'data','qa','build','dist','vendor','.builder','__pycache__','credentials','backups','local_library.json','settings.json','fault.log','error.log'}
    folders={'prompt_studio','comfyui_prompt_studio','tests','docs'}
    suffixes={'.py','.js','.mjs','.css','.ps1','.cmd','.vbs','.md','.txt','.svg','.ico','.json'}
    paths=[]
    for name in git_output(root,'ls-files','-z','--cached').split('\0'):
        if not name:continue
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or any(part.casefold() in blocked for part in rel.parts):continue
        if len(rel.parts)>1 and rel.parts[0] not in folders:continue
        if rel.suffix.lower() not in suffixes and rel.name not in ('LICENSE','.gitignore','.gitattributes'):continue
        path=root/rel
        if any(p.is_symlink() or p.is_junction() for p in (path,*path.parents) if p!=root and p.is_relative_to(root)):
            raise ValueError('來源包含連結，已停止打包：'+name)
        if not path.is_file():raise ValueError('來源檔案缺失：'+name)
        paths.append(path)
    if not paths:raise ValueError('沒有已登記的專案來源，已停止打包。')
    return sorted(set(paths))


def bundle_documents(destination, desktop=False, release=None):
    from prompt_studio.releases import CURRENT,SOURCE_ARCHIVE
    release=release or CURRENT
    destination = Path(destination)
    paths=source_paths(ROOT)
    for path in paths:
        relative=path.relative_to(ROOT)
        if relative.parts[0]=='docs' or (len(relative.parts)==1 and (path.suffix in ('.md','.txt') or path.name in ('LICENSE','deploy_common.ps1','install_comfyui.ps1','update_desktop.ps1'))):
            target=destination/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
    with zipfile.ZipFile(destination/SOURCE_ARCHIVE, 'w', zipfile.ZIP_DEFLATED) as archive:
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
    info=dict(version=release.version,release=release.flag,built_utc=datetime.now(timezone.utc).isoformat(),git_head=git('rev-parse','HEAD'),working_changes=git('status','--porcelain'),source_sha256=hashlib.sha256((destination/SOURCE_ARCHIVE).read_bytes()).hexdigest(),validation='docs/DEVELOPMENT_STATUS.md')
    if desktop:info['binary_git_head']=previous.get('binary_git_head',info['git_head'])
    (destination/'BUILD_INFO.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'BUILD_NOTICE.txt').write_text(
        'Prompt Calculus Studio '+release.version+'\n'
        '獨立 Alpha 測試包。來源與逐檔校驗見 BUILD_INFO.json、SOURCE_MANIFEST.json。\n'
        '目前驗證範圍及限制見 docs/DEVELOPMENT_STATUS.md；本版操作見 docs/GETTING_STARTED.md，版本對照見 docs/VERSIONING.md。\n'
        '維護版本說明見 MAINTENANCE_081.md（若本版附有）。\n'+
        SOURCE_ARCHIVE+' 包含本包對應的專案原始碼與建置腳本，不含個人資料或第三方執行環境。\n'
        '專案授權見 LICENSE；第三方元件依各自授權，見 docs/LICENSING.md。\n'
        '正式對外發行前仍需完成 Qt 等第三方條款、通知與相應原始碼取得方式的完整稽核。\n', encoding='utf-8')
