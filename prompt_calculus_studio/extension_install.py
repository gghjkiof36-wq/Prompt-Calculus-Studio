"""Local companion-package discovery. Installation is owned by the verified installer."""
import json
import os
import sys
from pathlib import Path

from .releases import EXTENSION_FOLDER, RELEASES


def plain_path(value):
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('安裝位置不能是捷徑或重新導向的資料夾。')
    return path


def comfy_root(value):
    path = plain_path(value)
    if path.name.casefold() == 'custom_nodes':
        path = path.parent
    if not (path / 'custom_nodes').is_dir() and (path / 'ComfyUI/custom_nodes').is_dir():
        path = plain_path(path / 'ComfyUI')
    if not (path / 'custom_nodes').is_dir() or not ((path / 'main.py').is_file() or
            ((path / 'models').is_dir() and (path / 'user').is_dir())):
        raise ValueError('請選擇 ComfyUI 資料夾，或其中的 custom_nodes 資料夾。')
    plain_path(path / 'custom_nodes')
    return path


def build_info(folder):
    try:
        info = json.loads((folder / 'BUILD_INFO.json').read_text(encoding='utf-8-sig'))
        if not isinstance(info, dict) or not all(isinstance(info.get(key), str) for key in ('release', 'version', 'git_head')):
            return {}
        return info
    except (OSError, ValueError):
        return {}


def installed(root):
    custom = plain_path(root / 'custom_nodes')
    found = [plain_path(custom / name) for name in (EXTENSION_FOLDER, 'comfyui_prompt_studio') if (custom / name).exists()]
    if len(found) > 1:
        raise ValueError('發現兩份 PCS 擴充；請先保留備份並把未使用的一份移出 custom_nodes。')
    if found and not found[0].is_dir():
        raise ValueError('PCS 擴充位置不是資料夾。')
    return found[0] if found else None


def available_package():
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    return base, base / 'extensions' / EXTENSION_FOLDER


def update_state(candidate, previous):
    if not candidate:
        return 'unavailable'
    if not previous:
        return 'install'
    if candidate.get('git_head') == previous.get('git_head') and candidate.get('release') == previous.get('release'):
        return 'current'
    order = list(RELEASES)
    if previous.get('release') not in order or candidate.get('release') not in order:
        return 'unknown'
    if previous['release'] == candidate['release']:
        return 'unknown'
    if order.index(previous['release']) > order.index(candidate['release']):
        return 'newer'
    return 'update'


class InstallPreferences:
    def __init__(self, directory):
        self.path = Path(directory) / 'extension-install.json'
        self.value = {'root': '', 'automatic': True}
        self.error = ''
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(value, dict) or not isinstance(value.get('root'), str) or type(value.get('automatic')) is not bool:
                    raise ValueError()
                self.value = value
            except (ValueError, OSError):
                self.error = '擴充安裝設定無法讀取，請重新選擇資料夾。'

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        self.error = ''
