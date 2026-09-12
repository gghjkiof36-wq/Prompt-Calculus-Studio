"""Build a standalone extension from the same Qt-free desktop core."""
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / 'build' / 'comfyui' / 'comfyui_prompt_studio'


def build():
    DEST.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / 'comfyui_prompt_studio').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            relative = path.relative_to(ROOT / 'comfyui_prompt_studio')
            target = DEST / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    shared = DEST / 'shared'
    shared.mkdir(exist_ok=True)
    (shared / '__init__.py').write_text('', encoding='utf-8')
    for name in ('core.py', 'snapshots.py', 'pnginfo.py', 'exclusions.py'):
        shutil.copy2(ROOT / 'prompt_studio' / name, shared / name)
    shutil.copy2(ROOT / 'COMFYUI_GUIDE.md', DEST / 'README.md')
    archive = ROOT / 'release' / 'PromptStudio-ComfyUI.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in DEST.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.name != 'local_library.json':
                output.write(path, path.relative_to(DEST.parent))
    print(archive)


if __name__ == '__main__':
    build()
