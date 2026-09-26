"""Build a standalone extension from the same Qt-free desktop core."""
import json
import shutil
import zipfile
import sys
from pathlib import Path

from prompt_studio.releases import EXTENSION_FOLDER

ROOT = Path(__file__).resolve().parent
ALPHA08='--v08' in sys.argv
DEST = ROOT / 'build' / ('v08-comfyui' if ALPHA08 else 'comfyui') / EXTENSION_FOLDER


def build(destination=None,archive=None):
    destination=Path(destination) if destination is not None else DEST
    if destination.exists() and any(destination.iterdir()):raise ValueError('Use a fresh extension output directory; old versions are preserved.')
    destination.mkdir(parents=True, exist_ok=True)
    from package_documents import source_paths
    for path in source_paths(ROOT):
        if path.is_relative_to(ROOT/'comfyui_prompt_studio'):
            relative = path.relative_to(ROOT / 'comfyui_prompt_studio')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    shared = destination / 'shared'
    shared.mkdir(exist_ok=True)
    (shared / '__init__.py').write_text('', encoding='utf-8')
    from prompt_studio.releases import SHARED_MODULES,select_release
    for name in SHARED_MODULES:
        shutil.copy2(ROOT / 'prompt_studio' / (name+'.py'), shared / (name+'.py'))
    from package_documents import bundle_documents
    bundle_documents(destination,release=select_release([arg for arg in sys.argv if arg!='--v08']))
    from package_documents import package_manifest
    package_manifest(destination,'comfyui')
    archive = Path(archive) if archive is not None else ROOT / 'build' / 'v08-comfyui' / 'PromptCalculusStudio-v0.8-ComfyUI.zip' if ALPHA08 else ROOT / 'release' / 'PromptCalculusStudio-ComfyUI.zip'
    archive.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in destination.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.name != 'local_library.json':
                output.write(path, path.relative_to(destination.parent))
    print(archive)


if __name__ == '__main__':
    build()
