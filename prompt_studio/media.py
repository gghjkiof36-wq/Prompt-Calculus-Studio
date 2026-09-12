"""File catalog, bounded previews and explicit metadata provenance.

Model files are only listed/copied/recycled. Their tensor data is never loaded.
"""
import copy
import json
import os
import shutil
import struct
import time
import uuid
import zlib
from pathlib import Path
from .pnginfo import png_metadata
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter

MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MODEL_FOLDERS = {"loras": "LoRA", "checkpoints": "CKPT", "diffusion_models": "Diffusion", "unet": "Diffusion"}


def checked_model(root, filename, expected=None):
    root = Path(root).resolve(strict=True)
    raw = Path(filename).absolute()
    path = raw.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in MODEL_EXTENSIONS:
        raise ValueError("檔案不在選定模型目錄內，或不是支援的模型格式。")
    # Do not mutate files reached through symlinks or Windows junctions.
    for part in [raw, *raw.parents]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("此路徑包含連結或 junction，請直接選擇實際資料夾。")
        if part == root:
            break
    stat = path.stat()
    if expected and (stat.st_size != expected["size"] or stat.st_mtime_ns != expected["mtime"]):
        raise ValueError("模型自上次掃描後已變更，請重新掃描後再操作。")
    return path


def scan_models(root, cancel=None):
    root = Path(root).resolve(strict=True)
    rows = []
    for folder, kind in MODEL_FOLDERS.items():
        base = root / folder
        if not base.is_dir():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory)/d).is_symlink()
                       and not (Path(directory)/d).is_junction()]
            for name in files:
                if cancel and cancel.is_set():
                    raise ValueError("已取消掃描，先前清單保留。")
                path = Path(directory)/name
                if path.suffix.lower() not in MODEL_EXTENSIONS:
                    continue
                try:
                    path = checked_model(root, path)
                    stat = path.stat()
                except (OSError, ValueError):
                    continue
                rows.append(dict(id=uuid.uuid5(uuid.NAMESPACE_URL, str(path).casefold()).hex,
                                 path=str(path), root=str(root), relative=str(path.relative_to(root)),
                                 kind=kind, size=stat.st_size, mtime=stat.st_mtime_ns))
                if len(rows) >= 20000:
                    raise ValueError("單次最多掃描 20,000 個模型，請選擇較小的模型根目錄。")
    return rows


def copy_model(source, root, folder, cancel=None):
    root = Path(root).resolve(strict=True)
    if folder not in MODEL_FOLDERS:
        raise ValueError("不支援的模型資料夾。")
    source = Path(source).resolve(strict=True)
    if source.suffix.lower() not in MODEL_EXTENSIONS or not source.is_file():
        raise ValueError("請選擇模型檔案。")
    destdir = root / folder
    if destdir.is_symlink() or destdir.is_junction() or not destdir.resolve().is_relative_to(root):
        raise ValueError("目標資料夾包含連結，請指定實際位置。")
    destdir.mkdir(exist_ok=True)
    dest = destdir / source.name
    if dest.exists():
        raise ValueError("目標已有同名檔案，未覆寫。")
    partial = destdir / (".prompt-studio-"+uuid.uuid4().hex+".partial")
    try:
        with source.open("rb") as src, partial.open("xb") as output:
            while block := src.read(4*1024*1024):
                if cancel and cancel.is_set():
                    raise ValueError("已取消匯入。")
                output.write(block)
        if dest.exists():
            raise ValueError("目標已有同名檔案，未覆寫。")
        # Windows rename is atomic and refuses to replace an existing target.
        os.rename(partial, dest)
    finally:
        if partial.exists():
            partial.unlink()
    return str(dest)


def thumbnail(source, data_dir):
    folder = Path(data_dir) / "thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    reader = QImageReader(str(source))
    reader.setAutoTransform(True)
    reader.setAllocationLimit(128)
    size = reader.size()
    if size.isValid():
        size.scale(QSize(512,512), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    img = reader.read()
    if img.isNull():
        raise ValueError("無法建立預覽：格式不支援、檔案受損，或圖片超過解碼記憶體限制。")
    img = img.scaled(512,512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    canvas = QImage(img.size(), QImage.Format.Format_RGB32)
    canvas.fill(Qt.GlobalColor.black)
    painter = QPainter(canvas)
    painter.drawImage(0,0,img)
    painter.end()
    relative = "thumbnails/"+uuid.uuid4().hex+".jpg"
    if not canvas.save(str(Path(data_dir)/relative), "JPEG", 85):
        raise ValueError("縮圖儲存失敗。")
    return relative


def import_image(path, data_dir, album, copy_original=False, cancel=None):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("不支援的圖片格式。")
    preview = thumbnail(path, data_dir)
    try:
        metadata = png_metadata(path)
    except (OSError, ValueError):
        metadata = dict(source="none", note="無法讀取內嵌資料；仍可手動附註。")
    original = str(path)
    owned = False
    original_relative=""
    if copy_original:
        dest = Path(data_dir)/"originals"/(uuid.uuid4().hex+path.suffix.lower())
        dest.parent.mkdir(exist_ok=True)
        try:
            with path.open("rb") as source, dest.open("xb") as output:
                while block := source.read(4*1024*1024):
                    if cancel and cancel.is_set(): raise ValueError("已取消圖片匯入。")
                    output.write(block)
        except Exception:
            if dest.exists(): dest.unlink()
            raise
        original, owned = str(dest), True
        original_relative=str(dest.relative_to(Path(data_dir))).replace("\\","/")
    return dict(id=uuid.uuid4().hex, album=album, path=original, owned=owned,
                name=path.name, thumb=preview, created=time.time(), metadata=metadata,
                manual=None, notes="", original_relative=original_relative)


class Catalog:
    """Images stay in separate SQLite rows and are paged, never in prompt state."""
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.execute("CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, kind TEXT, parent TEXT, name TEXT, body TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS resource_page ON resources(kind,parent,name)")
        self.db.commit()

    def put(self, kind, row, parent=""):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?)",
                            (row["id"],kind,parent,row.get("name", ""),json.dumps(row,ensure_ascii=False)))

    def get(self, ident):
        row = self.db.execute("SELECT body FROM resources WHERE id=?", (ident,)).fetchone()
        return self.resolve_original(json.loads(row[0])) if row else None

    def resolve_original(self,row):
        if row.get("owned") and row.get("original_relative"):
            path=(self.store.directory/row["original_relative"]).resolve()
            if path.is_relative_to(self.store.directory.resolve()): row["path"]=str(path)
        return row

    def rows(self, kind, parent=None, limit=60, offset=0, search=""):
        where, args = "kind=?", [kind]
        if parent is not None:
            where += " AND parent=?"; args.append(parent)
        if search:
            where += " AND name LIKE ? ESCAPE '\\'"
            args.append("%"+search.replace("\\","\\\\").replace("%",r"\%").replace("_",r"\_")+"%")
        # Gallery pages do not hold 60 full ComfyUI workflow graphs in memory.
        columns="json_object('id',id,'name',name,'path',json_extract(body,'$.path'),'thumb',json_extract(body,'$.thumb'),'owned',json_extract(body,'$.owned'),'original_relative',json_extract(body,'$.original_relative'))" if kind in ("image","recent") else "body"
        order="json_extract(body,'$.created') DESC,id" if kind=='recent' else 'name,id'
        rows = self.db.execute("SELECT "+columns+" FROM resources WHERE "+where+" ORDER BY "+order+" LIMIT ? OFFSET ?", (*args,limit,offset))
        return [self.resolve_original(json.loads(r[0])) for r in rows]

    def count(self, kind, parent=None, search=""):
        sql, args = "SELECT COUNT(*) FROM resources WHERE kind=?", [kind]
        if parent is not None:
            sql += " AND parent=?"; args.append(parent)
        if search:
            sql += " AND name LIKE ? ESCAPE '\\'"
            args.append("%"+search.replace("\\","\\\\").replace("%",r"\%").replace("_",r"\_")+"%")
        return self.db.execute(sql,args).fetchone()[0]

    def delete(self, ident):
        with self.db:
            self.db.execute("DELETE FROM resources WHERE id=?",(ident,))

    def merge_models(self, rows, root):
        parent=str(Path(root).resolve())
        def write(row):
            self.db.execute("INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?)",(row["id"],"model",parent,row["name"],json.dumps(row,ensure_ascii=False)))
        with self.db:
            for row in rows:
                previous = self.get(row["id"]) or {}
                current = {**previous, **row, "missing":False}
                current.setdefault("name", Path(row["path"]).stem)
                write(current)
            found = {r["id"] for r in rows}
            for previous in self.rows("model",str(Path(root).resolve()),limit=20000):
                if previous["id"] not in found:
                    previous["missing"] = True
                    write(previous)

    def snapshot(self, state):
        from .core import build_prompt
        workspace = next(w for w in state["workspaces"] if w["id"] == state["workspace"])
        return dict(source="manual_recommendation", attached=time.time(), workspace=workspace["name"],
                    prompt=state["draft"] if state["draft"] is not None else build_prompt(state),
                    parameters=copy.deepcopy(workspace.get("parameters",{})),
                    note="手動附上的工作區建議，未驗證是否為實際生成參數。")
