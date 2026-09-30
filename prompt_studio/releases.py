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
    Release('--queue-preview','queue-preview-20260927','v0.82 工作佇列開發候選（0927）','Start-PCS-Queue-Preview','--v082-alpha'),
    Release('--v083-preview','v083-preview-20260927','v0.83 開發候選（0927）','Start-PCS-v0.83-Preview','--v083-alpha'),
    Release('--v083-inputs','v083-inputs-20260927','v0.83 圖片來源與預排程候選（0927）','Start-PCS-v0.83-Inputs','--v083-alpha'),
    Release('--v083-repair1','v083-repair1-20260927','v0.83 執行與工作區修復候選（0927）','Start-PCS-v0.83-Repair1','--v083-alpha'),
    Release('--v083-repair2','v083-repair2-20260928','v0.83 待核對阻塞修復候選（0928）','Start-PCS-v0.83-Repair2','--v083-alpha'),
    Release('--v083-direct','v083-direct-20260928','v0.83 直接執行修復候選（0928）','Start-PCS-v0.83-Direct','--v083-alpha'),
    Release('--v084-chain','v084-chain-20260929','v0.84 Stage 與分層預排程候選（0929）','Start-PCS-v0.84-Chain','--v083-alpha'),
    Release('--v084-repair1','v084-repair1-20260929','v0.84 綁定與介面修復候選（0929）','Start-PCS-v0.84-Repair1','--v083-alpha'),
    Release('--v084-repair2','v084-repair2-20260929','v0.84 流程與原生讀取修復候選（0929）','Start-PCS-v0.84-Repair2','--v083-alpha'),
    Release('--v084-alpha1','v084-alpha-1','v0.84 Alpha 1','Start-PCS-v0.84-Alpha','--v084-alpha'),
)}
CURRENT=RELEASES['--v084-alpha1']
# Historical build flags remain readable; new metadata uses canonical 0.84 flags.
RELEASE_ALIASES={
    '--v0831-chain':'--v084-chain',
    '--v0831-repair1':'--v084-repair1',
    '--v0831-repair2':'--v084-repair2',
    '--v0831-repair3':'--v084-alpha1',
    '--v084-repair3':'--v084-alpha1',
}
APP_BASENAME='PromptCalculusStudio'
EXTENSION_FOLDER='comfyui_prompt_calculus_studio'
SOURCE_ARCHIVE='PromptCalculusStudio-source.zip'
SHARED_MODULES=('core','snapshots','pnginfo','exclusions','composition','generation','multi_output','clip_flow','workflow_flow','flow_data','composition_image','workflow_transfer','workflow_import','state_loading','queued_work','native_graph','workspace_scene','chain_model','chain_connections','stage_model','module_contracts','result_data')

def select_release(arguments):
    # Preserve historical last-version-flag precedence in build_windows.py.
    arguments={RELEASE_ALIASES.get(flag,flag) for flag in arguments}
    selected=[r for flag,r in RELEASES.items() if flag in arguments]
    return selected[-1] if selected else None

def runtime_release():
    path=Path(__file__).parent/'assets/build-info.json'
    if not path.exists():return CURRENT
    info=json.loads(path.read_text(encoding='utf-8'))
    flag=RELEASE_ALIASES.get(info.get('release'),info.get('release'))
    if flag not in RELEASES:raise ValueError('安裝包版本資訊無效。')
    return RELEASES[flag]

def window_title():return 'Prompt Calculus Studio · '+runtime_release().version

def write_launchers(destination,release):
    destination=Path(destination)
    (destination/(release.launcher+'.cmd')).write_text('@echo off\ncd /d "%~dp0"\nset "PROMPT_STUDIO_DATA=%~dp0data"\nstart "" "%~dp0'+APP_BASENAME+'.exe" '+release.argument+'\n',encoding='utf-8')
    (destination/(release.launcher+'.vbs')).write_text('Option Explicit\nDim shell, files, folder\nSet shell = CreateObject("WScript.Shell")\nSet files = CreateObject("Scripting.FileSystemObject")\nfolder = files.GetParentFolderName(WScript.ScriptFullName)\nshell.Environment("PROCESS")("PROMPT_STUDIO_DATA") = files.BuildPath(folder, "data")\nshell.CurrentDirectory = folder\nshell.Run Chr(34) & files.BuildPath(folder, "'+APP_BASENAME+'.exe") & Chr(34) & " '+release.argument+'", 1, False\n',encoding='utf-8')
