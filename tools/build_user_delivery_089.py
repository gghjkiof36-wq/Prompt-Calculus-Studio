"""Assemble a fresh Windows delivery with an in-app Markdown welcome and lessons.

The executable remains byte-identical to its recorded build. Authored documents
are added to the initial files folder, then the package manifest is regenerated.
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


def make_library(root):
    """Author editable category documents, never merge over a user's library."""
    from prompt_calculus_studio import core, composition, materials, prompt_materials, prompt_groups
    from prompt_calculus_studio.local_documents import category, io
    root.mkdir()
    catalog = json.loads((TEMPLATES/'materials.json').read_text(encoding='utf-8'))
    for spec in catalog['categories']:
        state = core.initial_state()
        state.update(items=[], modules=[], dictionary={})
        state['workspaces'][0].update(fixed=[], picks={})
        owner = materials.add_category(state, spec['name'])
        ids = []
        for name, prompt in spec['entries']:
            node = prompt_materials.new('text', name)
            node.update(prompt=prompt, content_kind=spec['kind'])
            ids.append(materials.save(state, dict(name=name, prompt=prompt, module=owner,
                content_kind=spec['kind'], composition=node, notes='可直接修改、複製或加入自己的組合。')))
        for number, selected in enumerate((ids[:3], ids[3:]), 1):
            name = f'{spec["name"]}組合 {number}'
            node = prompt_materials.new('combination', name)
            node['combination']['modules'] = [composition.clone_node(
                composition.prototype(materials.item(state, ident))) for ident in selected]
            materials.save(state, dict(name=name, module=owner, prompt='', content_kind=spec['kind'],
                composition=node, material_type='combination', notes='三個素材的獨立副本，可自由替換。'))
        name = spec['name']+'流程'
        node = prompt_materials.new('flow', name)
        groups = [prompt_groups.new_group(title, prompt, content_kind=spec['kind'])
                  for title, prompt in spec['flow']]
        node['sequence'].update(mode='increment', start=groups[0]['id'],
                                selected=groups[0]['id'], groups=groups)
        materials.save(state, dict(name=name, module=owner, prompt='', content_kind=spec['kind'],
            composition=node, material_type='flow', notes='依序切換三個階段；不會自動呼叫模型。'))
        filename = spec['name']+'.pcs-category'
        result = io.create(root, filename, category.from_state(state, owner))
        if result.status != 'saved':
            raise RuntimeError(result)
        loaded = category.to_state(io.load(root, filename).document)
        assert len(loaded['items']) == 9
        assert {item['material_type'] for item in loaded['items']} == {'text', 'combination', 'flow'}
        for recovery in io.recovery_files(root, filename):
            if recovery.suffix != '.candidate' or recovery.read_bytes() != (root/filename).read_bytes():
                raise ValueError('Unexpected recovery content in new library.')
            recovery.unlink()


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


def assemble_learning(content):
    """Add the same welcome and lessons to a fresh source or desktop package."""
    content = Path(content)
    if content.exists():
        raise ValueError('Learning content requires a fresh destination.')
    content.mkdir()
    lessons = content/'入口'
    shutil.copytree(TEMPLATES/'Tutorial', lessons)
    shutil.copy2(TEMPLATES/'歡迎使用.md', content/'歡迎使用.md')
    (lessons/'媒體').mkdir()
    for name in PREVIEWS:
        shutil.copy2(ROOT/'docs/images'/name, lessons/'媒體'/name)
    make_materials(lessons)
    verify_learning(lessons)
    make_library(content/'素材')
    shutil.copytree(TEMPLATES/'Notes', content/'筆記')
    shutil.copytree(TEMPLATES/'Templates', content/'筆記模板')
    shutil.copytree(TEMPLATES/'Reference', content/'參考')
    shutil.copytree(ROOT/'tests/fixtures/markdown_compatibility', content/'Markdown 相容性測試')
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', (content/'歡迎使用.md').read_text(encoding='utf-8')):
        if '://' not in target and not (content/target).is_file():
            raise ValueError('Missing welcome reference: '+target)


def build(desktop, output, connector=None):
    from package_documents import package_manifest
    desktop, output = desktop.resolve(), output.absolute()
    if output.exists():
        raise ValueError('Use a fresh output directory.')
    for path in desktop.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Desktop contains a redirected path.')
    info = verify_desktop(desktop)
    if (desktop/'files').exists():
        raise ValueError('Use a pristine desktop build without user content.')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(desktop, output)
    assemble_learning(output/'files')
    if connector:
        connector = Path(connector).resolve()
        manifest = json.loads((connector/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
        expected = manifest['files']
        actual = {p.relative_to(connector).as_posix() for p in connector.rglob('*') if p.is_file()}
        if actual != set(expected) | {'PACKAGE_MANIFEST.json'}:
            raise ValueError('Connector package has unexpected or missing files.')
        for name, checksum in expected.items():
            path = connector/name
            if path.is_symlink() or path.is_junction() or sha(path) != checksum:
                raise ValueError('Connector package differs from its manifest: '+name)
        shutil.copytree(connector, output/'connector-package')
    (output/'沿用既有資料.bat').write_bytes(
        b'@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nstart "" /wait "%~dp0PromptCalculusStudio.exe" --choose-workspace\r\nexit /b %errorlevel%\r\n')
    (output/'啟動PCS.bat').write_bytes(
        b'@echo off\r\nstart "" "%~dp0PromptCalculusStudio.exe"\r\n')
    shutil.copy2(TEMPLATES/'Reference/素材使用.md', output/'使用說明.md')
    package_manifest(output, 'desktop')
    verify_desktop(output)
    full_zip = output.with_name(output.name+'.zip')
    archive(output, full_zip)
    receipt = dict(package=str(full_zip), size=full_zip.stat().st_size, sha256=sha(full_zip),
                   binary_git_head=info['binary_git_head'], source_git_head=info['git_head'],
                   package_manifest_sha256=sha(output/'PACKAGE_MANIFEST.json'),
                   welcome='files/歡迎使用.md', learning='files/入口',
                   desktop_manifest_verified=True, learning_materials_verified=True,
                   windows_public=False)
    write(output.parent/'USER-DELIVERY.json', json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--connector', type=Path, help='Verified PCS-Chrome-Connector folder.')
    args = parser.parse_args()
    build(args.desktop, args.output, args.connector)
