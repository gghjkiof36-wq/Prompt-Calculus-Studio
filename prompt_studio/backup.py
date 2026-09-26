"""Create a portable archive without reading external originals or model files."""
from pathlib import Path
import zipfile

def archive_data(directory, snapshot, destination, cancel=None):
    directory=Path(directory).resolve()
    destination=Path(destination).resolve()
    if destination.is_relative_to(directory):
        raise ValueError("請將 ZIP 備份儲存在 data 資料夾外。")
    with zipfile.ZipFile(destination,"w",compression=zipfile.ZIP_STORED) as archive:
        archive.write(snapshot,"data/studio.sqlite3")
        for name in ("thumbnails","originals","civitai/downloads"):
            folder=directory/name
            if not folder.is_dir(): continue
            for path in folder.rglob('*'):
                if cancel and cancel.is_set():
                    raise ValueError("已取消備份；未完成的 ZIP 不應用於還原。")
                if path.is_file() and path.resolve().is_relative_to(directory):
                    archive.write(path,"data/"+str(path.relative_to(directory)).replace("\\","/"))
    return str(destination)
