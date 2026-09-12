"""Build a standalone onedir app. Run after installing PyInstaller locally."""
import os
import sys
import shutil
import struct
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/".builder"/"Lib"/"site-packages"))
sys.path.insert(0,str(ROOT/"vendor"))
os.environ["PYINSTALLER_CONFIG_DIR"]=str(ROOT/"build"/"cache")
# The host may have Poppler/LibreOffice on PATH. Their icuuc.dll is ABI-
# incompatible with the Windows ICU API used by Qt; keep discovery isolated.
system=Path(os.environ.get("SystemRoot",r"C:\Windows"))
os.environ["PATH"]=os.pathsep.join([str(ROOT/"vendor"/"PySide6"),str(ROOT/"vendor"/"shiboken6"),str(Path(sys.executable).parent),str(system/"System32"),str(system)])
os.chdir(ROOT)
# Rasterize the existing vector at each Windows icon size. PNG-compressed ICO
# entries retain transparent edges and avoid adding an imaging dependency.
from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
icon_path=ROOT/"prompt_studio"/"assets"/"studio.ico"
renderer=QSvgRenderer(str(icon_path.with_suffix(".svg")))
if not renderer.isValid(): raise RuntimeError("Invalid application icon")
images=[]
for size in (16,20,24,32,40,48,64,128,256):
    image=QImage(size,size,QImage.Format.Format_ARGB32); image.fill(0)
    painter=QPainter(image); renderer.render(painter); painter.end()
    data=QByteArray(); buffer=QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer,"PNG"): raise RuntimeError("Cannot render application icon")
    images.append((size,bytes(data)))
offset=6+16*len(images); entries=[]
for size,data in images:
    entries.append(struct.pack("<BBBBHHII",size%256,size%256,0,0,1,32,len(data),offset))
    offset+=len(data)
icon_path.write_bytes(struct.pack("<HHH",0,1,len(images))+b"".join(entries)+b"".join(data for _,data in images))
import PyInstaller.__main__
diagnostic="--diagnostic" in sys.argv
stage_only="--stage-only" in sys.argv
output_name="diagnostic" if diagnostic else ("package-icon" if "--icon-refresh" in sys.argv else "package")
package=ROOT/"build"/output_name/"PromptStudio"
if not package.resolve().is_relative_to(ROOT) or package.is_symlink() or package.is_junction():
    raise RuntimeError("Build output must stay in this workspace")
PyInstaller.__main__.run([
    "--noconfirm","--clean","--console" if diagnostic else "--windowed","--onedir","--name","PromptStudio",
    "--paths",str(ROOT/"vendor"),"--paths",str(ROOT),
    "--icon",str(icon_path),
    "--add-data",str(ROOT/"prompt_studio"/"assets")+os.pathsep+"prompt_studio/assets",
    "--distpath",str(package.parent),"--workpath",str(ROOT/"build"),
    "--exclude-module","numpy","--exclude-module","matplotlib",
    "--exclude-module","PIL","--exclude-module","tkinter",
    str(ROOT/"run.py")])
# Merge program files only, preserving the user's portable data directory.
if not diagnostic and not stage_only:
    runtime=ROOT/"release"/"PromptStudio"/"_internal"
    if runtime.exists():
        if runtime.resolve()!=runtime.absolute() or not runtime.resolve().is_relative_to(ROOT):
            raise RuntimeError("Refusing to replace a redirected runtime directory")
        # Only generated runtime files are replaced; portable data stays intact.
        shutil.rmtree(runtime)
    shutil.copytree(package,ROOT/"release"/"PromptStudio",dirs_exist_ok=True)
    for document in ("README.md","IMPLEMENTATION_NOTES.md","使用說明.txt","NEXT_UI.md","COMFYUI_GUIDE.md"):
        shutil.copy2(ROOT/document,ROOT/"release"/"PromptStudio"/document)
    print("Ready: release/PromptStudio/PromptStudio.exe")
