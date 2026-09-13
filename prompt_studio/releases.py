"""Release identity shared by launchers, builders and the desktop window."""
import json
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Release:
    flag:str
    folder:str
    version:str
    launcher:str
    argument:str

RELEASES={r.flag:r for r in (
    Release('--v08','v08-alpha','v0.8 Alpha','Start-v0.8-Alpha','--v08-alpha'),
    Release('--v08-repair','v08-alpha-2','v0.8 Alpha 2','Start-v0.8-Alpha','--v08-alpha'),
    Release('--v08-alpha3','v08-alpha-3','v0.8 Alpha 3','Start-v0.8-Alpha','--v08-alpha'),
    Release('--v08-alpha3-hotfix1','v08-alpha-3-hotfix-1','v0.8 Alpha 3 Hotfix 1','Start-v0.8-Alpha','--v08-alpha'),
    Release('--v08-alpha3-hotfix2','v08-alpha-3-hotfix-2','v0.8 Alpha 3 Hotfix 2','Start-v0.8-Alpha','--v08-alpha'),
    Release('--v081-alpha1','v081-alpha-1','v0.81 Alpha 1','Start-v0.81-Alpha','--v081-alpha'),
    Release('--v081-alpha2','v081-alpha-2','v0.81 Alpha 2','Start-v0.81-Alpha','--v081-alpha'),
    Release('--v081-maintenance','v081-maintenance-1','v0.81 Alpha 2 Maintenance 1','Start-v0.81-Maintenance','--v081-alpha'),
    Release('--v081-ui-repair1','v081-ui-repair-1','v0.81 Alpha 2 UI Repair 1','Start-v0.81-UI-Repair','--v081-alpha'),
)}
CURRENT=RELEASES['--v081-ui-repair1']
SHARED_MODULES=('core','snapshots','pnginfo','exclusions','composition','generation','multi_output','composition_image','workflow_transfer','workflow_import','state_loading')

def select_release(arguments):
    # Preserve historical last-version-flag precedence in build_windows.py.
    selected=[r for flag,r in RELEASES.items() if flag in arguments]
    return selected[-1] if selected else None

def runtime_release():
    path=Path(__file__).parent/'assets/build-info.json'
    if not path.exists():return CURRENT
    info=json.loads(path.read_text(encoding='utf-8'))
    if info.get('release') not in RELEASES:raise ValueError('安裝包版本資訊無效。')
    return RELEASES[info['release']]

def window_title():return 'Prompt Studio · '+runtime_release().version

def write_launchers(destination,release):
    destination=Path(destination)
    (destination/(release.launcher+'.cmd')).write_text('@echo off\ncd /d "%~dp0"\nset "PROMPT_STUDIO_DATA=%~dp0data"\nstart "" "%~dp0PromptStudio.exe" '+release.argument+'\n',encoding='utf-8')
    (destination/(release.launcher+'.vbs')).write_text('Option Explicit\nDim shell, files, folder\nSet shell = CreateObject("WScript.Shell")\nSet files = CreateObject("Scripting.FileSystemObject")\nfolder = files.GetParentFolderName(WScript.ScriptFullName)\nshell.Environment("PROCESS")("PROMPT_STUDIO_DATA") = files.BuildPath(folder, "data")\nshell.CurrentDirectory = folder\nshell.Run Chr(34) & files.BuildPath(folder, "PromptStudio.exe") & Chr(34) & " '+release.argument+'", 1, False\n',encoding='utf-8')
