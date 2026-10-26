"""Explicit navigation roots and file locations; no editor or Qt ownership."""
from dataclasses import dataclass
from pathlib import Path
import os

from .local_documents import paths


KINDS = {'.pcs-category': 'category', '.pcs-workflow': 'workflow', '.pcs-chat': 'chat', '.md': 'markdown'}
INTERNAL_NAMES = frozenset(('.obsidian', '.pcs', 'assets'))


def path_key(path):
    return os.path.normcase(str(Path(path).absolute()))


def remembered_root(value, allow_missing=False):
    """A remembered disconnected folder is a location, never a create request."""
    candidate = Path(value)
    if candidate.is_dir() or not allow_missing:return paths.root_path(value)
    if not candidate.is_absolute() or candidate.exists() or any(p.casefold() in INTERNAL_NAMES for p in candidate.parts):
        raise ValueError('無法還原這個內容資料夾位置。')
    parent = next((p for p in candidate.parents if p.exists()), None)
    if parent is not None:paths.root_path(parent)  # Existing ancestors still reject reparse points.
    return candidate.absolute()


@dataclass(frozen=True)
class CommandContext:
    origin: str
    path: Path | None
    kind: str
    root: Path | None
    destination: Path | None
    asset_base: Path | None = None
    tab_key: str | None = None
    document_id: str | None = None
    pane_id: str | None = None
    view_key: str | None = None


class WorkspaceLocations:
    def __init__(self, root, data, saved=None):
        self.data = Path(data).absolute()
        self.initial = remembered_root(root, (self.data/'workspace-layout.json').is_file())
        self.roots = [self.permitted(self.initial)]
        self.current = self.initial
        if isinstance(saved, dict) and saved.get('version') == 1 and isinstance(saved.get('roots'), list):
            self.roots = []
            for value in saved['roots']:
                try:
                    folder = self.permitted(remembered_root(value, allow_missing=True))
                    if folder not in self.roots:
                        self.roots.append(folder)
                except (ValueError, OSError, TypeError):
                    continue
            wanted = saved.get('current')
            self.current = next((r for r in self.roots if str(r) == wanted), next(iter(self.roots), None))

    def permitted(self, path):
        if path == self.data or self.data in path.parents or any(p.casefold() in INTERNAL_NAMES for p in path.parts):
            raise ValueError('請選擇內容資料夾，不能使用附件或程式內部資料位置。')
        return path

    def directory(self, value):
        if not value or not Path(value).is_absolute():
            raise ValueError('請選擇已存在的完整資料夾位置。')
        return self.permitted(paths.root_path(value))

    def file(self, value):
        value = Path(value)
        if not value.is_absolute():
            raise ValueError('請使用文件的完整位置。')
        parent = self.directory(value.parent)
        result = self.permitted(paths.checked(parent, value.name))
        if not result.is_file() or result.suffix.casefold() not in KINDS:
            raise ValueError('請選擇分類、Canvas 工作流、對話或 Markdown 文件。')
        return result

    def root_for(self, path):
        if path is None:
            return None
        matches = [root for root in self.roots if path == root or root in path.parents]
        return max(matches, key=lambda r: len(r.parts), default=None)

    def snapshot(self):
        return dict(version=1, roots=[str(r) for r in self.roots], current=str(self.current) if self.current else None)

    def inventory(self, root):
        """Only enrolled roots are traversed; excluded subtrees are never entered."""
        pending = [self.directory(root)]
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                if path.name.casefold() in INTERNAL_NAMES or paths.is_link(path):
                    continue
                if path == self.data or self.data in path.parents:
                    continue
                try:
                    paths.relative_parts(path.name)
                except ValueError:
                    continue
                if path.is_dir():
                    yield path, 'folder'
                    pending.append(path)
                elif path.is_file() and path.suffix.casefold() in KINDS:
                    yield path, KINDS[path.suffix.casefold()]

    def recent(self, values):
        result = []
        for value in values[:20] if isinstance(values, list) else ():
            if not isinstance(value, str):
                continue
            try:
                candidate = Path(value) if Path(value).is_absolute() else paths.checked(self.initial, value)
                candidate = str(self.file(candidate))
                if candidate not in result:
                    result.append(candidate)
            except (ValueError, OSError):
                continue
        return result
