"""Create an isolated source launcher and fresh synthetic review files."""
import argparse
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

from local_workspace_p2_fixture import prepare


def build(out, vendor, refresh_launcher=False, phase='p2'):
    source = Path(__file__).resolve().parents[1]
    python = Path(sys.executable)
    pythonw = python.with_name('pythonw.exe')
    vendor = vendor.resolve(strict=True)
    if not pythonw.exists() or not (vendor/'PySide6/__init__.py').is_file():
        raise ValueError('The local Python GUI runtime and PySide6 vendor directory are required.')
    out = out.absolute()
    root = out/'創作資料（合成）'
    if refresh_launcher:
        previous = json.loads((out/'PROVENANCE.json').read_text(encoding='utf-8'))
        if (Path(previous['source']) != source or previous.get('phase', 'p2') != phase
                or not root.is_dir() or not (out/'data').is_dir()):
            raise ValueError('Only this checkout\'s existing preview launcher can be refreshed.')
    else:
        prepare(out)  # Refuses an existing destination; never resets review edits.
        if phase in ('p3-a', 'p3-files', 'p3-sidebar', 'p4-a', 'p4-split'):
            external = out/'其他位置（合成）'
            external.mkdir()
            (external/'位置說明.md').write_text('# 其他位置\n\n可從內容選單將這個資料夾加入側欄，再於根內新增及整理內容；另存新檔仍可選擇其他位置。\n', encoding='utf-8')
    temporary = ''
    if phase in ('p3-a', 'p3-files', 'p3-sidebar', 'p4-a', 'p4-split'):
        (out/'temp').mkdir(exist_ok=True)
        temporary = 'set "TEMP=%~dp0temp"\nset "TMP=%~dp0temp"\n'
    commit = subprocess.check_output(['git', '-c', 'safe.directory='+str(source),
                                     'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    status = subprocess.check_output(['git', '-c', 'safe.directory='+str(source),
                                     'status', '--porcelain'], cwd=source, text=True)
    launch_source = source
    if phase in ('p4-a', 'p4-split'):
        if status.strip():
            raise ValueError('Commit the reviewed P4 source before making its isolated preview.')
        launch_source = out/'source'
        if refresh_launcher:
            if previous.get('commit') != commit or not (launch_source/'run_workspace.py').is_file():
                raise ValueError('A fixed-source preview needs a new destination for a new commit.')
        else:
            # Freeze this checkpoint so subsequent development cannot change a
            # running preview through modules that are imported lazily.
            payload = subprocess.check_output(['git', '-c', 'safe.directory='+str(source),
                                               'archive', '--format=zip', commit], cwd=source)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for name in archive.namelist():
                    item = PurePosixPath(name)
                    if item.is_absolute() or '..' in item.parts:
                        raise ValueError('Invalid path in source snapshot: '+name)
                archive.extractall(launch_source)
    preview_files = ''
    if phase == 'p4-a':
        preview_files = f' --open-left "%~dp0{root.name}\\街景.pcs-workflow" --open-right "%~dp0{root.name}\\筆記\\場景筆記.md"'
    launcher = f'''@echo off
chcp 65001 >nul
setlocal
set "PCS_ROOT={launch_source}"
set "PCS_PYTHON={python}"
set "PCS_PYTHONW={pythonw}"
set "PYTHONPATH={vendor}"
set "PROMPT_STUDIO_DATA=%~dp0data"
set "PROMPT_STUDIO_V08=1"
set "QT_QPA_PLATFORM=windows"
set "QT_SCALE_FACTOR="
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"
{temporary}if not exist "%PCS_PYTHONW%" goto missing
if not exist "%PYTHONPATH%\\PySide6\\__init__.py" goto missing
if not exist "%PCS_ROOT%\\run_workspace.py" goto missing
cd /d "%PCS_ROOT%"
if /i "%~1"=="--check" goto check
start "" "%PCS_PYTHONW%" "%PCS_ROOT%\\run_workspace.py" --root "%~dp0{root.name}" --data "%~dp0data"{preview_files}
exit /b 0
:check
"%PCS_PYTHON%" "%PCS_ROOT%\\run_workspace.py" --root "%~dp0{root.name}" --data "%~dp0data" --check
exit /b %errorlevel%
:missing
echo This preview needs its local PCS source and Python runtime.
echo Keep the workspace folders in place.
pause
exit /b 1
'''
    path = out/({'p3-a': 'Start-PCS-P3-A-Files.bat', 'p3-files': 'Start-PCS-P3-Files.bat',
                'p3-sidebar': 'Start-PCS-P3-Sidebar.bat', 'p4-a': 'Start-PCS-P4-A.bat',
                'p4-split': 'Start-PCS-P4-Split.bat'}.get(phase, 'Start-PCS-P2-Tabs.bat'))
    path.write_bytes(launcher.replace('\n', '\r\n').encode('utf-8'))
    manifest = dict(source=str(source), commit=commit, uncommitted=bool(status.strip()),
                    launch_source=str(launch_source), fixed_source=launch_source != source,
                    launcher=str(path), root=str(root), data=str(out/'data'),
                    python=str(python), vendor=str(vendor), synthetic=True,
                    phase=phase, scope=phase.upper()+' local editing preview; live services and generation are not enabled')
    (out/'PROVENANCE.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--refresh-launcher', action='store_true',
                        help='Refresh only the launcher and source record, preserving all review files/data.')
    parser.add_argument('--phase', choices=('p2', 'p3-a', 'p3-files', 'p3-sidebar', 'p4-a', 'p4-split'), default='p2')
    args = parser.parse_args()
    build(args.out, args.vendor, args.refresh_launcher, args.phase)
