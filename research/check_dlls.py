"""Inspect bundled import/export symbols without running the application."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/".builder"/"Lib"/"site-packages"))
import pefile

folder=ROOT/"build"/"diagnostic"/"PromptStudio"/"_internal"
files=list(folder.rglob("*.dll"))+list(folder.rglob("*.pyd"))
mapping={p.name.casefold():p for p in files}
exports={}
for path in files:
    pe=pefile.PE(str(path),fast_load=True,max_symbol_exports=65536)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    if hasattr(pe,"DIRECTORY_ENTRY_EXPORT"):
        exports[path.name.casefold()]={symbol.name for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols}
    pe.close()

for path in files:
    pe=pefile.PE(str(path),fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    for entry in getattr(pe,"DIRECTORY_ENTRY_IMPORT",[]):
        name=entry.dll.decode().casefold()
        if name in exports:
            missing=[str(v.name) for v in entry.imports if v.name is not None and v.name not in exports[name]]
            if missing: print(path.name,"->",name,"missing",len(missing),missing[:3],flush=True)
    if path.name in ("QtCore.pyd","Qt6Core.dll","pyside6.abi3.dll","shiboken6.abi3.dll"):
        print(path.name,"imports",[v.dll.decode() for v in getattr(pe,"DIRECTORY_ENTRY_IMPORT",[])],flush=True)
    pe.close()
