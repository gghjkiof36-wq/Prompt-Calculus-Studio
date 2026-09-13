"""Install pinned Qt wheels locally, without moving ACL-restricted temp folders."""
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "vendor"

def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("This launcher supports Windows 64-bit. Use pip on other systems.")
    DEST.mkdir(exist_ok=True)
    for package in ("shiboken6", "PySide6-Essentials"):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/6.11.2/json", timeout=30) as response:
            info = json.load(response)
        wheel = next(v for v in info["urls"] if v["filename"].endswith("cp310-abi3-win_amd64.whl"))
        target = DEST / wheel["filename"]
        print("Downloading", package, flush=True)
        import hashlib
        digest = hashlib.sha256()
        with urllib.request.urlopen(wheel["url"], timeout=60) as response, target.open("wb") as output:
            while block := response.read(1024*1024):
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != wheel["digests"]["sha256"]:
            raise RuntimeError("Download checksum mismatch")
        with zipfile.ZipFile(target) as archive:
            for item in archive.infolist():
                resolved = (DEST / item.filename).resolve()
                if not resolved.is_relative_to(DEST.resolve()):
                    raise RuntimeError("Invalid archive path")
            archive.extractall(DEST)
        target.unlink()
        print("Ready:", package, flush=True)

if __name__ == "__main__":
    main()
