"""Windows-user protected credentials kept outside Prompt Studio state/backups."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buffer = ctypes.create_string_buffer(data or b"\0")
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _crypt(data, protect):
    if os.name != "nt": raise OSError("CivitAI Token 安全儲存目前只支援 Windows。")
    source, source_buffer = _blob(data); entropy, entropy_buffer = _blob(b"PromptStudio.CivitAI.v1")
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if protect:
        ok = function(ctypes.byref(source), "Prompt Studio CivitAI", ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    else:
        description = wintypes.LPWSTR()
        ok = function(ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    del source_buffer, entropy_buffer
    if not ok: raise OSError("Windows 無法保護 CivitAI Token。")
    try: return ctypes.string_at(output.pbData, output.cbData)
    finally: ctypes.windll.kernel32.LocalFree(output.pbData)


def token_path(directory):
    return Path(directory) / "credentials" / "civitai.bin"


def save_token(directory, token):
    path = token_path(directory); value = token.strip()
    if not value:
        clear_token(directory); return
    path.parent.mkdir(parents=True, exist_ok=True)
    protected = _crypt(value.encode("utf-8"), True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(protected); os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def load_token(directory):
    path = token_path(directory)
    if not path.is_file(): return ""
    try: return _crypt(path.read_bytes(), False).decode("utf-8")
    except (OSError, UnicodeError): raise ValueError("已保存的 CivitAI Token 無法由目前 Windows 帳號讀取，請重新設定。") from None


def clear_token(directory):
    path = token_path(directory)
    if path.exists(): path.unlink()

