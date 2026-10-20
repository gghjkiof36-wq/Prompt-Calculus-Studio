"""Assemble a fresh local Windows delivery with optional, isolated learning materials.

The inner desktop package remains byte-identical to its recorded build. This
outer package has its own manifest and never includes existing data/recovery files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMPLATES = Path(__file__).parent/'user_delivery'
PREVIEWS = (
    'workspace-split-0.8.9.png', 'markdown-source-reading-0.8.9.png',
    'markdown-outline-0.8.9.png', 'sidebar-peek-0.8.9.png',
)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, text, *, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8-sig' if bom else 'utf-8', newline='\n')


def make_materials(root):
    from prompt_calculus_studio import core, composition, materials, prompt_materials, prompt_groups
    from prompt_calculus_studio.local_documents import category, io
    from prompt_calculus_studio.workspace_files import create_file

    if any(root.glob('*.pcs-*')) or any(root.glob('*.recovery-*')):
        raise ValueError('Learning documents require a fresh destination.')
    state = core.initial_state()
    state.update(items=[], modules=[], dictionary={})
    state['workspaces'][0].update(fixed=[], picks={})
    owner = materials.add_category(state, '海岸素材')
    entries = (
        ('海岸場景', 'quiet coast, gentle waves', ['海岸', '海邊', '場景']),
        ('暖色光線', 'soft warm light, gentle shadows', ['光線', '暖光']),
        ('寬景構圖', 'wide shot, low horizon', ['構圖', '遠景']),
    )
    ids = []
    for name, prompt, aliases in entries:
        material = prompt_materials.new('text', name)
        material['prompt'] = prompt
        if not ids:
            groups = [prompt_groups.new_group('平靜海岸', prompt),
                      prompt_groups.new_group('潮汐海岸', 'rocky coast, low tide')]
            material['variants'] = dict(version=1, mode='increment', random_repeats=True,
                                       start=groups[0]['id'], selected=groups[0]['id'], groups=groups)
        ids.append(materials.save(state, dict(
            name=name, prompt=prompt, module=owner, aliases=aliases,
            notes='海岸練習教材，可修改或另存。', composition=material)))

    combination = prompt_materials.new('combination', '海岸組合')
    combination['combination']['modules'] = [
        composition.clone_node(composition.prototype(materials.item(state, ident))) for ident in ids]
    materials.save(state, dict(name='海岸組合', module=owner, prompt='',
                              composition=combination, material_type='combination',
                              aliases=['組合'], notes='場景、光線與構圖的組合練習。'))
    flow = prompt_materials.new('flow', '晨昏變化')
    groups = [prompt_groups.new_group('清晨', 'quiet coast, morning light'),
              prompt_groups.new_group('傍晚', 'quiet coast, sunset light')]
    flow['sequence'].update(mode='increment', start=groups[0]['id'],
                            selected=groups[0]['id'], groups=groups)
    materials.save(state, dict(name='晨昏變化', module=owner, prompt='',
                              composition=flow, material_type='flow',
                              aliases=['流程', '清晨', '傍晚'], notes='比較兩種時間的素材流程練習。'))
    document = category.from_state(state, owner)
    result = io.create(root, '02-海岸素材.pcs-category', document)
    if result.status != 'saved':
        raise RuntimeError(result)
    # The tutorial Canvas is the same clean entry as a real new document.
    canvas_path = create_file(root, 'workflow', '03-練習畫布')
    # Live document writes retain recovery copies by contract. These two
    # documents were authored just above for immutable distribution; keep only
    # their verified, published bytes, never recovery material from an old root.
    for path in (root/'02-海岸素材.pcs-category', canvas_path):
        for recovery in io.recovery_files(root, path.name):
            if recovery.suffix != '.candidate' or recovery.read_bytes() != path.read_bytes():
                raise ValueError('Unexpected recovery content during fresh material creation.')
            recovery.unlink()


def make_launchers(out):
    write(out/'Start-PCS.cmd', r'''@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0MyFiles" mkdir "%~dp0MyFiles"
set "PROMPT_STUDIO_DATA=%~dp0UserData"
start "" "%~dp0Program\PromptCalculusStudio.exe" --v0.8.9-alpha --root "%~dp0MyFiles" --data "%~dp0UserData"
''')
    write(out/'Start-Tutorial.cmd', r'''@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Open-Tutorial.ps1"
if errorlevel 1 pause
''')
    write(out/'Open-Tutorial.ps1', r'''param([switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
try {
    $kitRoot = $PSScriptRoot
    $practiceRoot = Join-Path $kitRoot 'Practice'
    $tutorialSource = Join-Path $kitRoot 'Tutorial'
    if (-not (Test-Path -LiteralPath $practiceRoot)) {
        Copy-Item -LiteralPath $tutorialSource -Destination $practiceRoot -Recurse -ErrorAction Stop
    }
    $guideFile = Join-Path $practiceRoot '00-從這裡開始.md'
    $canvasFile = Join-Path $practiceRoot '03-練習畫布.pcs-workflow'
    foreach ($requiredFile in @($guideFile, $canvasFile)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw '練習資料不完整。請先保留 Practice，再把它移到別處後重新開啟教學；既有內容不會自動覆寫。'
        }
    }
    if ($PrepareOnly) { return }
    $tutorialData = Join-Path $kitRoot 'TutorialData'
    $env:PROMPT_STUDIO_DATA = $tutorialData
    $program = Join-Path $kitRoot 'Program/PromptCalculusStudio.exe'
    # Pass paths as quoted arguments to support spaces and Chinese folder names.
    $arguments = @('--v0.8.9-alpha', '--root', ('"' + $practiceRoot + '"'),
        '--data', ('"' + $tutorialData + '"'), '--open-left', ('"' + $guideFile + '"'),
        '--open-right', ('"' + $canvasFile + '"'))
    Start-Process -FilePath $program -ArgumentList $arguments -WorkingDirectory $kitRoot
} catch {
    Write-Host ('無法開啟教學：' + $_.Exception.Message)
    exit 1
}
''', bom=True)
    write(out/'Open-Guide.cmd',
          '@echo off\nchcp 65001 >nul\ncd /d "%~dp0"\nstart "" "%~dp0開始使用.html"\n')


def make_html(out):
    from markdown_it import MarkdownIt
    parser = MarkdownIt('commonmark', {'html': False}).enable('table')
    body = parser.render((out/'開始使用.md').read_text(encoding='utf-8'))
    page = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>開始使用 PCS 0.8.9</title>
<style>
:root{color-scheme:dark;font-family:"Microsoft JhengHei UI","Segoe UI",sans-serif;background:#15191b;color:#edece7}
body{margin:0;line-height:1.8}main{max-width:960px;margin:auto;padding:48px 28px 72px}
h1{font-size:2.25rem;line-height:1.3;margin:0 0 20px}h2{font-size:1.5rem;margin:52px 0 14px}
h3{font-size:1.15rem;margin:30px 0 10px}p,ul,ol{max-width:76ch}
a{color:#e6d7b6;text-underline-offset:4px}a:hover{color:#fff0d1}
a:focus-visible{outline:2px solid #e6d7b6;outline-offset:5px}
img{max-width:100%;height:auto;display:block;border-radius:12px}
table{width:100%;border-collapse:collapse;margin:20px 0;text-align:left}
th,td{padding:12px 16px;border-bottom:1px solid #3d4345;vertical-align:top}
th{color:#e6d7b6;background:#24292b}code{font-size:.95em;color:#e6d7b6;overflow-wrap:anywhere}
::selection{background:#e6d7b6;color:#202527}*{scrollbar-color:#6c7273 #202527}
@media(max-width:600px){main{padding:28px 18px 48px}h1{font-size:1.8rem}th,td{padding:10px 8px}h2{margin-top:38px}}
</style><main>''' + body + '</main></html>'
    write(out/'開始使用.html', page)


def verify_desktop(desktop):
    manifest = json.loads((desktop/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
    files = {item['path']: item for item in manifest['files']}
    actual = {p.relative_to(desktop).as_posix() for p in desktop.rglob('*') if p.is_file()}
    if actual != set(files) | {'PACKAGE_MANIFEST.json'}:
        raise ValueError('Desktop package has unrecorded/missing files; use a pristine build.')
    for name, item in files.items():
        path = desktop/name
        if path.stat().st_size != item['size'] or sha(path) != item['sha256']:
            raise ValueError('Desktop file mismatch: '+name)
    return json.loads((desktop/'BUILD_INFO.json').read_text(encoding='utf-8'))


def verify_learning(root):
    from prompt_calculus_studio.local_documents import io, workflow, category
    from prompt_calculus_studio.canvas_starter import guide
    for path in root.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Unexpected link in learning materials.')
        if '.recovery-' in path.name or path.name in {'state.json', 'error.log'}:
            raise ValueError('Unexpected development data.')
    material = io.load(root, '02-海岸素材.pcs-category').document
    items = category.to_state(material)['items']
    assert len(items) == 5
    assert {item['material_type'] for item in items} == {'text', 'combination', 'flow'}
    canvas = workflow.to_state(io.load(root, '03-練習畫布.pcs-workflow').document)
    assert guide(canvas) and not canvas['uses'] and not canvas['items']
    assert len(canvas['multi_output']['connections']) == 5
    for path in root.rglob('*.md'):
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' not in target and not (path.parent/target).is_file():
                raise ValueError(f'Missing tutorial reference: {path.name}: {target}')


def archive(folder, destination):
    if destination.exists():
        raise ValueError('Archive already exists: '+str(destination))
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                stream.write(path, (Path(folder.name)/path.relative_to(folder)).as_posix())


def build(desktop, output):
    desktop, output = desktop.resolve(), output.absolute()
    if output.exists():
        raise ValueError('Use a fresh output directory.')
    for path in desktop.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Desktop contains a redirected path.')
    info = verify_desktop(desktop)
    output.mkdir(parents=True)
    shutil.copytree(desktop, output/'Program')
    shutil.copytree(TEMPLATES/'Tutorial', output/'Tutorial')
    shutil.copy2(TEMPLATES/'開始使用.md', output/'開始使用.md')
    (output/'Preview').mkdir()
    (output/'Tutorial/媒體').mkdir()
    for name in PREVIEWS:
        source = ROOT/'docs/images'/name
        shutil.copy2(source, output/'Preview'/name)
        shutil.copy2(source, output/'Tutorial/媒體'/name)
    make_materials(output/'Tutorial')
    make_launchers(output)
    make_html(output)
    verify_learning(output/'Tutorial')
    write(output/'先讀我.txt',
          'PCS 0.8.9 Alpha 1 本機交付更新\n\n'
          '先完整解壓，再開啟「開始使用.html」查看畫面與操作。\n'
          '日常工作：雙擊 Start-PCS.cmd。\n'
          '跟著教材練習：雙擊 Start-Tutorial.cmd。\n'
          '請保留 Program 整個資料夾；Windows 完整包目前僅供本機使用。\n')
    files = []
    for path in sorted(output.rglob('*')):
        if path.is_file():
            files.append(dict(path=path.relative_to(output).as_posix(),
                              size=path.stat().st_size, sha256=sha(path)))
    record = dict(format='pcs-user-delivery-1', version=info['version'],
                  binary_git_head=info['binary_git_head'], desktop_manifest_sha256=sha(desktop/'PACKAGE_MANIFEST.json'),
                  windows_public=False, tutorial='fresh authored materials; no existing data copied',
                  files=files)
    write(output/'DELIVERY_MANIFEST.json', json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    full_zip = output.with_name(output.name+'.zip')
    archive(output, full_zip)
    lesson_zip = output.parent/'PCS-v0.8.9-Alpha1-Tutorial.zip'
    archive(output/'Tutorial', lesson_zip)
    receipt = dict(package=str(full_zip), size=full_zip.stat().st_size, sha256=sha(full_zip),
                   tutorial=str(lesson_zip), tutorial_sha256=sha(lesson_zip),
                   binary_git_head=info['binary_git_head'], desktop_manifest_verified=True,
                   learning_materials_verified=True, files=len(files), windows_public=False)
    write(output.parent/'USER-DELIVERY.json', json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.desktop, args.output)
