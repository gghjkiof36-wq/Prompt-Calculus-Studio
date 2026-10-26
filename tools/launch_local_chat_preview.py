"""Launch the current checkout in an isolated, user-owned preview directory.

This is a source preview bootstrap, not an installer or a model launcher. It is
only invoked when the user runs the delivered BAT; importing it starts nothing.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
from pathlib import Path
import sys
import traceback


EXPECTED_PYTHON = Path('D:/Prompt Calculus Studio/tools/python312/python.exe')
MARKER = 'PCS-PREVIEW-INITIALIZED.json'
INITIAL_CHAT = '本地對話.pcs-chat'
DEPENDENCIES = (
    'PySide6/__init__.py', 'PySide6/QtCore.pyd', 'PySide6/QtWidgets.pyd',
    'PySide6/QtWebEngineProcess.exe', 'shiboken6/__init__.py',
    'markdown_it/__init__.py', 'mdit_py_plugins/__init__.py',
    'pygments/__init__.py', 'spellchecker/__init__.py',
)


def real_directory(path):
    """Create only the requested preview directory; reject redirected storage."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('預覽資料夾不能是捷徑或連結位置：' + str(part))
        if part.exists() and not part.is_dir():
            raise ValueError('預覽資料夾的位置已被檔案占用：' + str(part))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def prepare_paths(home):
    source = Path(__file__).resolve().parents[1]
    vendor = source / 'vendor'
    if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve(strict=True):
        raise ValueError('請使用隨附 BAT 與 D 槽指定的 Python 啟動。')
    if not (source / 'run_workspace.py').is_file():
        raise ValueError('找不到此工作樹的 PCS 啟動程式：' + str(source))
    missing = [name for name in DEPENDENCIES if not (vendor / name).is_file()]
    if missing:
        raise ValueError('既有 vendor 依賴不完整；本入口不會自動安裝。缺少：' + ', '.join(missing))
    home = real_directory(home)
    if home == source or source in home.parents or home in source.parents:
        raise ValueError('預覽文件與資料必須放在工作樹以外的獨立資料夾。')
    files = real_directory(home / 'files')
    data = real_directory(home / 'data')
    temporary = real_directory(home / 'temp')
    marker = home / MARKER
    if marker.is_symlink():
        raise ValueError('預覽初始化紀錄不能是連結；現有文件與資料保持不變。')
    if marker.exists():
        if not marker.is_file() or marker.stat().st_size > 8192:
            raise ValueError('預覽初始化紀錄無法讀取；現有文件與資料保持不變。')
        value = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('format') != 'pcs-local-chat-preview-v1':
            raise ValueError('預覽初始化紀錄不是此入口的格式；現有文件與資料保持不變。')
    return source, vendor, home, files, data, temporary, marker


def seed_if_empty(files, data, marker):
    """Create one empty document once, without replacing review edits."""
    initial = files / INITIAL_CHAT
    if marker.exists():
        return initial if initial.is_file() and not initial.is_symlink() else None
    seeded = False
    # A directory containing any existing data is adopted unchanged. No release
    # examples, user workspaces, model registry, or other private data is copied.
    if not any(files.iterdir()) and not any(data.iterdir()):
        from prompt_calculus_studio.local_documents import chat, io
        result = io.create(files, INITIAL_CHAT, chat.empty())
        if result.status != 'saved':
            if not result.name_collision:
                raise ValueError('無法建立空白預覽對話：' + (result.error or '請查看此記錄。'))
        else:
            seeded = True
    record = dict(format='pcs-local-chat-preview-v1', synthetic_initial_chat=seeded,
                  files=str(files), data=str(data))
    try:
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    except FileExistsError:
        pass  # Another launch wrote the marker; never replace it or its edits.
    return initial if initial.is_file() and not initial.is_symlink() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview-home', required=True, type=Path)
    args = parser.parse_args()
    source, vendor, home, files, data, temporary, marker = prepare_paths(args.preview_home)
    os.environ.update(PROMPT_STUDIO_DATA=str(data), PROMPT_STUDIO_V08='1',
                      QT_QPA_PLATFORM='windows', TEMP=str(temporary), TMP=str(temporary))
    # These are per-process overrides; inherited diagnostic Qt scaling must not
    # accidentally turn this visible preview into an offscreen/scaled run.
    for name in ('QT_SCALE_FACTOR', 'QT_SCREEN_SCALE_FACTORS'):
        os.environ.pop(name, None)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(vendor), str(source)]
    os.chdir(source)
    print('PCS local chat source preview', flush=True)
    print('Source:', source, flush=True)
    print('Python:', sys.executable, flush=True)
    print('Vendor:', vendor, '->', vendor.resolve(), flush=True)
    print('Files:', files, flush=True)
    print('Data:', data, flush=True)
    print('No model is downloaded or loaded by this launcher.', flush=True)
    faulthandler.enable(all_threads=True)
    initial = seed_if_empty(files, data, marker)
    sys.argv = [str(source / 'run_workspace.py'), '--root', str(files), '--data', str(data)]
    if initial is not None:
        sys.argv.extend(['--open-left', str(initial)])
    from run_workspace import main as workspace_main
    result = workspace_main()
    print('PCS exited with status:', result or 0, flush=True)
    return result or 0


if __name__ == '__main__':
    try:
        status = main()
    except KeyboardInterrupt:
        print('啟動已取消。請先在 PCS 視窗內正常關閉，再關閉此視窗。', file=sys.stderr)
        status = 130
    except Exception:
        print('PCS 預覽未能完成啟動；詳細原因如下。既有文件與資料不會被重設。', file=sys.stderr)
        traceback.print_exc()
        status = 1
    raise SystemExit(status)
