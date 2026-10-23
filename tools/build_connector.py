"""Create an isolated Windows connector delivery without registering it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NATIVE_SOURCES = {"pcs-connector-host.exe": "tools/connector_host.py", "pcs-connector-install.exe": "tools/install_chrome_connector.py"}


def reuse_native(package: Path, staging: Path) -> dict:
    """Retain an already checked binary when only extension/package files change."""
    raw = (package / "PACKAGE_MANIFEST.json").read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    for binary, source in NATIVE_SOURCES.items():
        current = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
        if manifest.get("source_files", {}).get(source) != current:
            raise ValueError(f"Native source changed; rebuild required: {source}")
        expected = manifest.get("files", {}).get(binary)
        if not expected or hashlib.sha256((package / binary).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Native binary differs from its manifest: {binary}")
        shutil.copy2(package / binary, staging / binary)
        if hashlib.sha256((staging / binary).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Native binary changed while copying: {binary}")
    for name in ("Python-LICENSE.txt", "PyInstaller-LICENSE.txt", "OpenSSL-LICENSE.txt"):
        relative = "licenses/" + name
        expected = manifest.get("files", {}).get(relative)
        if not expected or hashlib.sha256((package / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Native license differs from its manifest: {relative}")
        (staging / "licenses").mkdir(exist_ok=True)
        shutil.copy2(package / relative, staging / relative)
    return {"reused_manifest_sha256": hashlib.sha256(raw).hexdigest(), "python": manifest.get("python")}


def build(output: Path, builder_path: Path | None = None, *, include_extension: bool = True, native_package: Path | None = None) -> Path:
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"輸出位置已存在：{output}")
    output.mkdir(parents=True)
    staging = output / "PCS-Chrome-Connector"
    staging.mkdir()
    native_identity = reuse_native(native_package, staging) if native_package else {"python": sys.version}
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONSTARTUP", None)
    environment["PYINSTALLER_CONFIG_DIR"] = str(output / "cache")
    # Discovery must not pick up DLLs from unrelated apps on the caller's PATH.
    system = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), str(Path(sys.base_prefix) / "DLLs"), str(system / "System32"), str(system)))
    if builder_path:
        environment["PYTHONPATH"] = str(builder_path.absolute())
    else:
        environment.pop("PYTHONPATH", None)
    for source, name in (() if native_package else (("connector_host.py", "pcs-connector-host"), ("install_chrome_connector.py", "pcs-connector-install"))):
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
                   "--name", name, "--distpath", str(staging), "--workpath", str(output / "build"),
                   "--specpath", str(output / "spec"), "--paths", str(ROOT / "tools"), str(ROOT / "tools" / source)]
        with (output / f"{name}-build.log").open("wb") as log:
            subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    licenses = staging / "licenses"
    licenses.mkdir(exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not native_package and not python_license.is_file():
        raise FileNotFoundError("Python runtime LICENSE.txt is required")
    if not native_package:
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    if builder_path:
        candidates = list(builder_path.glob("pyinstaller-*.dist-info/licenses/COPYING.txt"))
        installer_license = candidates[0] if len(candidates) == 1 else Path()
    elif not native_package:
        from importlib import metadata
        distribution = metadata.distribution("pyinstaller")
        candidates = [distribution.locate_file(entry) for entry in distribution.files or [] if str(entry).endswith("licenses/COPYING.txt")]
        installer_license = Path(candidates[0]) if len(candidates) == 1 else Path()
    if not native_package:
        if not installer_license.is_file():
            raise FileNotFoundError("PyInstaller COPYING.txt is required")
        shutil.copy2(installer_license, licenses / "PyInstaller-LICENSE.txt")
    shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
    if not native_package:
        shutil.copy2(ROOT / "docs" / "third-party" / "OpenSSL-3.5.8-LICENSE.txt", licenses / "OpenSSL-LICENSE.txt")
    (staging / "安裝連線工具.cmd").write_bytes(b'@echo off\r\n"%~dp0pcs-connector-install.exe" --host "%~dp0pcs-connector-host.exe"\r\nif errorlevel 1 (pause & exit /b 1)\r\npause\r\n')
    if include_extension:
        extension_source = ROOT / "extensions" / "chrome"
        manifest = json.loads((extension_source / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("manifest_version") != 3:
            raise ValueError("Chrome manifest_version must be 3")
        extension = staging / "chrome"
        shutil.copytree(extension_source, extension, ignore=shutil.ignore_patterns("node_modules", ".git", "*.map", "*.test.*", ".DS_Store"))
    source_root = staging / "source"
    source_files = [ROOT / "tools" / name for name in ("connector_host.py", "install_chrome_connector.py", "build_connector.py")]
    source_files.extend((ROOT / "tests" / "test_connector_host.py", ROOT / "LICENSE", ROOT / "docs" / "third-party" / "OpenSSL-3.5.8-LICENSE.txt"))
    if include_extension:
        source_files.extend(path for path in (ROOT / "extensions" / "chrome").rglob("*") if path.is_file())
    source_hashes = {}
    for source in source_files:
        relative = source.relative_to(ROOT)
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_hashes[relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    (staging / "開始使用.md").write_text(
        "# PCS Chrome 連線\n\n"
        "1. 在 PCS 的「ChatGPT 對話」頁面，選「設定 Chrome」→「安裝連接程式」。\n"
        "2. 在 Chrome 開啟 `chrome://extensions`，啟用「開發人員模式」。\n"
        "3. 選「載入未封裝項目」，選擇這裡的「chrome」資料夾。\n"
        "4. 回到 ChatGPT 對話，按擴充功能的「送到 PCS」。\n\n"
        "連線工具安裝在目前帳戶的 AppData 資料夾。\n",
        encoding="utf-8")
    files = {}
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            files[path.relative_to(staging).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    git = shutil.which("git")
    base_commit = None
    if git:
        revision = subprocess.run([git, "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
        if revision.returncode == 0:
            base_commit = revision.stdout.strip()
    identity = {"schema": 1, "base_commit": base_commit, "source_files": source_hashes,
                "python": native_identity["python"], "native_build": native_identity, "files": files}
    (staging / "PACKAGE_MANIFEST.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = output / "PCS-Chrome-Connector.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                bundle.write(path, str(path.relative_to(output)))
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--builder-path", type=Path)
    parser.add_argument("--host-only", action="store_true")
    parser.add_argument("--reuse-native", type=Path, help="Reuse a verified package when native sources are unchanged")
    arguments = parser.parse_args(argv)
    print(build(arguments.output, arguments.builder_path, include_extension=not arguments.host_only, native_package=arguments.reuse_native))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
