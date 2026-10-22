"""Worker-local image input receipts; oversized inputs keep their legacy path."""
from dataclasses import dataclass
import hashlib
from pathlib import Path


# This bounds the optimization, not supported image size or decoder limits.
MAX_CAPTURE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class CapturedImage:
    path: Path
    data: bytes | None
    digest: str | None


def capture_image(path, cancel=None):
    """Hash exactly the bytes consumers will use, without trusting file stat."""
    path = Path(path).resolve(strict=True)
    blocks = []
    size = 0
    with path.open('rb') as stream:
        while True:
            if cancel is not None and cancel.is_set():
                raise ValueError('已取消圖片匯入。')
            block = stream.read(min(1024 * 1024, MAX_CAPTURE_BYTES + 1 - size))
            if not block:
                break
            size += len(block)
            if size > MAX_CAPTURE_BYTES:
                return CapturedImage(path, None, None)
            blocks.append(block)
    data = b''.join(blocks)
    return CapturedImage(path, data, hashlib.sha256(data).hexdigest())
