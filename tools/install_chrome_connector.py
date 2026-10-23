"""Install the Chrome native host for the current Windows account on request."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

try:
    from .connector_host import EXTENSION_ID, HOST_NAME
except ImportError:
    from connector_host import EXTENSION_ID, HOST_NAME

REGISTRY_KEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"


class InstallError(Exception):
    pass


def _safe_directory(directory: Path) -> None:
    """Reject redirects before creating a deployment directory."""
    for item in (directory, *directory.parents):
        if not item.exists() and not item.is_symlink():
            continue
        attributes = item.lstat()
        if item.is_symlink() or getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise InstallError("安裝資料夾不能是重新導向的資料夾。")
        if not item.is_dir():
            raise InstallError("安裝位置不是資料夾。")
    directory.mkdir(parents=True, exist_ok=True)


def _write_new(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise InstallError("安裝位置已有不同的檔案，請選擇另一個資料夾。")
        return
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pcs-", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # os.link is atomic and fails if the destination appeared meanwhile.
        os.link(temporary, path)
    except FileExistsError as exc:
        raise InstallError("安裝位置已被其他檔案使用，請再試一次。") from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _register(manifest: Path) -> None:
    import winreg

    # Chrome consults the 32-bit view first. An old entry there must not shadow
    # the newly installed 64-bit executable (the registry view does not limit it).
    view = winreg.KEY_WOW64_32KEY
    previous: tuple | None = None
    existed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_READ | view) as key:
            existed = True
            try:
                previous = winreg.QueryValueEx(key, None)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE | view) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest))
    except OSError as exc:
        # A failed replacement must leave a previous installation registered.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE | view) as key:
                if previous is not None:
                    winreg.SetValueEx(key, None, 0, previous[1], previous[0])
                else:
                    try:
                        winreg.DeleteValue(key, None)
                    except FileNotFoundError:
                        pass
            if not existed:
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, access=view)
        except OSError:
            pass
        raise InstallError("無法登記 Chrome 連線工具。") from exc


def install(host_executable: str | Path, extension_id: str, destination: str | Path | None = None) -> Path:
    """Stage an immutable host, then register it. Call only after a user action.

    The caller supplies the packaged standalone host, never the PCS GUI EXE.
    Previous hash directories and the previous source installation are retained.
    """
    if os.name != "nt":
        raise InstallError("此連線工具需要 Windows。")
    if extension_id != EXTENSION_ID:
        raise InstallError("Chrome 擴充功能識別碼不符。")
    source = Path(host_executable).resolve(strict=True)
    if not source.is_file() or source.suffix.casefold() != ".exe":
        raise InstallError("找不到 Chrome 連線工具。")
    with source.open("rb") as stream:
        header = stream.read(2)
    if header != b"MZ":
        raise InstallError("Chrome 連線工具格式不正確。")
    if source.stat().st_size > 128 * 1024 * 1024:
        raise InstallError("Chrome 連線工具大小不正確。")
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if destination is None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise InstallError("找不到安裝位置。")
        base = Path(local) / "PromptCalculusStudio" / "Connector" / "native-host"
    else:
        base = Path(destination).absolute()
    directory = base / digest[:24]
    _safe_directory(directory)
    host = directory / "pcs-connector-host.exe"
    _write_new(host, content)
    manifest = directory / f"{HOST_NAME}.json"
    document = {
        "name": HOST_NAME,
        "description": "Prompt Calculus Studio Chrome connector",
        "path": str(host),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }
    _write_new(manifest, (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    _register(manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="安裝 PCS 的 Chrome 連線工具")
    parser.add_argument("--host", type=Path, default=Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "pcs-connector-host.exe")
    parser.add_argument("--extension-id", default=EXTENSION_ID)
    parser.add_argument("--destination", type=Path)
    arguments = parser.parse_args(argv)
    try:
        install(arguments.host, arguments.extension_id, arguments.destination)
    except (InstallError, OSError) as exc:
        print(f"安裝未完成：{exc}", file=sys.stderr)
        return 1
    print("Chrome 連線工具已安裝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
