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
    Release('--v082-alpha1','v082-alpha-1','v0.82 Alpha 1','Start-PCS-v0.82-Alpha','--v082-alpha'),
    Release('--v082-alpha1-repair1','v082-alpha-1-repair-1','v0.82 Alpha 1 Repair 1','Start-PCS-v0.82-Repair1','--v082-alpha'),
    Release('--v082-alpha1-repair2','v082-alpha-1-repair-2','v0.82 Alpha 1 Repair 2','Start-PCS-v0.82-Repair2','--v082-alpha'),
    Release('--v082-alpha1-repair3','v082-alpha-1-repair-3','v0.82 Alpha 1 Repair 3','Start-PCS-v0.82-Repair3','--v082-alpha'),
    Release('--v082-alpha1-repair4','v082-alpha-1-repair-4','v0.82 Alpha 1 Repair 4','Start-PCS-v0.82-Repair4','--v082-alpha'),
    Release('--v082-alpha1-repair5','v082-alpha-1-repair-5','v0.82 Alpha 1 Repair 5（0927-2 批次預覽候選）','Start-PCS-v0.82-Repair5','--v082-alpha'),
)}
CURRENT=RELEASES['--v082-alpha1-repair5']
APP_BASENAME='PromptCalculusStudio'
EXTENSION_FOLDER='comfyui_prompt_calculus_studio'
SOURCE_ARCHIVE='PromptCalculusStudio-source.zip'
SHARED_MODULES=('core','snapshots','pnginfo','exclusions','composition','generation','multi_output','clip_flow','workflow_flow','composition_image','workflow_transfer','workflow_import','state_loading')

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

def window_title():return 'Prompt Calculus Studio · '+runtime_release().version

def write_launchers(destination,release):
    destination=Path(destination)
    (destination/(release.launcher+'.cmd')).write_text('@echo off\ncd /d "%~dp0"\nset "PROMPT_STUDIO_DATA=%~dp0data"\nstart "" "%~dp0'+APP_BASENAME+'.exe" '+release.argument+'\n',encoding='utf-8')
    (destination/(release.launcher+'.vbs')).write_text('Option Explicit\nDim shell, files, folder\nSet shell = CreateObject("WScript.Shell")\nSet files = CreateObject("Scripting.FileSystemObject")\nfolder = files.GetParentFolderName(WScript.ScriptFullName)\nshell.Environment("PROCESS")("PROMPT_STUDIO_DATA") = files.BuildPath(folder, "data")\nshell.CurrentDirectory = folder\nshell.Run Chr(34) & files.BuildPath(folder, "'+APP_BASENAME+'.exe") & Chr(34) & " '+release.argument+'", 1, False\n',encoding='utf-8')
