"""Local companion-package discovery. Installation is owned by the verified installer."""
import json
import ipaddress
import logging
import os
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

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
        if os.path.lexists(self.path):
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(value, dict) or not isinstance(value.get('root'), str) or type(value.get('automatic')) is not bool:
                    raise ValueError()
                self.value = value
            except (ValueError, OSError):
                self.error = '擴充安裝設定無法讀取，請重新選擇資料夾。'

    def save(self):
        write_preferences(self.path, self.value)
        self.error = ''


def write_preferences(path, value, *, exclusive=False):
    """A complete file becomes visible at once; migration never replaces a winner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.extension-install-', delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if exclusive:
            try: os.link(temporary, path)
            except FileExistsError: pass
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def workspace_preferences(directory):
    """One-time, non-destructive migration from the former per-launch location."""
    directory = Path(directory)
    destination = directory / 'extension-install.json'
    if not os.path.lexists(destination):
        values = []
        sessions = directory / 'sessions'
        try:
            entries = sorted(sessions.iterdir()) if sessions.exists() else []
            for session in entries:
                source = session / 'app-services' / 'extension-install.json'
                if not source.exists(): continue
                try:
                    plain_path(source)
                    previous = InstallPreferences(source.parent)
                    if previous.error: raise ValueError(previous.error)
                    values.append(previous.value)
                except (OSError, ValueError) as exc:
                    logging.getLogger(__name__).warning('extension preference migration skipped %s: %s', source, exc)
        except OSError as exc:
            # An unreadable inventory must not silently enable automatic update.
            logging.getLogger(__name__).warning('extension preference inventory failed: %s', exc)
            result = InstallPreferences(directory)
            result.error = '擴充安裝設定無法讀取，請重新選擇資料夾。'
            return result
        value = dict(root='', automatic=True)
        if values:
            roots = {v['root'] for v in values}
            value = dict(root=values[0]['root'] if len(roots) == 1 else '',
                         automatic=all(v['automatic'] for v in values) if len(roots) == 1 else False)
            if len(roots) == 1 and len({v['automatic'] for v in values}) == 1:
                value = dict(values[0])
        write_preferences(destination, value, exclusive=True)
    return InstallPreferences(directory)


def preferences_for(window):
    workspace = getattr(window, 'workspace_owner', None)
    return workspace_preferences(workspace.data) if workspace is not None else InstallPreferences(window.store.directory)


def loopback_server(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        return False
    host = parsed.hostname
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        if host != 'localhost': return False
        try:
            addresses = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
            return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_loopback for item in addresses)
        except (OSError, ValueError):
            return False


def bind_reported(preferences, url, info):
    """Only save a verified binding. Never install, update, or change automatic."""
    if preferences.error: return False, 'preferences_unreadable'
    # Re-read before saving: a page/user may have bound it since negotiation.
    current = InstallPreferences(preferences.path.parent)
    if current.error: return False, 'preferences_unreadable'
    if current.value['root']: return False, 'already_bound'
    if not loopback_server(url): return False, 'not_loopback'
    try:
        reported = info.get('root') if isinstance(info, dict) else None
        if not isinstance(reported, str) or not reported or not Path(reported).is_absolute():
            return False, 'invalid_extension_path'
        reported = plain_path(reported)
        if reported.parent.name.casefold() != 'custom_nodes': return False, 'not_custom_nodes'
        root = comfy_root(reported.parent)
        actual = installed(root)
        if actual is None or os.path.normpath(str(actual)).casefold() != os.path.normpath(str(reported)).casefold():
            return False, 'installed_path_mismatch'
        current.value['root'] = str(root)
        current.save()
        preferences.value, preferences.error = current.value, current.error
        return True, 'bound'
    except (OSError, ValueError) as exc:
        return False, 'binding_rejected: ' + str(exc)
