"""Bounded, session-local directory choices for the Chrome connector."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets

from .conversation_archive import canonical
from .conversation_import import Destination
from .local_documents.directory_pin import pinned_parent

MAX_ROOTS = 32
MAX_FOLDERS = 200
MAX_ENTRIES = 4096
MAX_CHOICES = 4096
MAX_REPLY = 256 * 1024


class DestinationError(ValueError):
    def __init__(self, message='保存位置已改變，請重新選擇。', code='destination_changed'):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FolderChoice:
    destination: Destination
    root: Destination


class DestinationCatalog:
    def __init__(self, roots, default, check_isolation):
        self.roots = roots
        self.default = default
        self.check_isolation = check_isolation
        self.choices = {}
        self.keys = {}

    def allowed_roots(self):
        result = []
        candidates = list(self.roots()) + [self.default()]
        if len(candidates) > MAX_ROOTS + 1:
            raise DestinationError('工作區數量超過可選範圍。', 'destination_limit')
        for path in candidates:
            try:
                destination = Destination.choose(path)
                self.check_isolation(destination.path)
            except (ValueError, OSError):
                continue
            if not any(item.path == destination.path for item in result):
                result.append(destination)
        if len(result) > MAX_ROOTS:
            raise DestinationError('工作區數量超過可選範圍。', 'destination_limit')
        if not result:
            raise DestinationError('沒有可用的保存位置，請在 PCS 接入資料夾。')
        return result

    def check_bound(self, choice):
        try:
            choice.root.verify()
            choice.destination.verify()
            if not choice.destination.path.is_relative_to(choice.root.path):
                raise ValueError('outside root')
            if not any(root == choice.root for root in self.allowed_roots()):
                raise ValueError('root no longer enrolled')
            self.check_isolation(choice.destination.path)
        except (ValueError, OSError) as error:
            raise DestinationError() from error
        return choice.destination

    def resolve(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
            raise DestinationError()
        choice = self.choices.get(identity)
        if choice is None:
            raise DestinationError()
        self.check_bound(choice)
        return choice

    def register(self, destination, root):
        key = (str(root.path), root.identity, str(destination.path), destination.identity)
        identity = self.keys.get(key)
        if identity is None:
            if len(self.choices) >= MAX_CHOICES:
                raise DestinationError('這次瀏覽的位置過多，請重新連接 PCS。', 'destination_limit')
            identity = secrets.token_hex(16)
            self.keys[key] = identity
            self.choices[identity] = FolderChoice(destination, root)
        return dict(id=identity, name=destination.path.name or str(destination.path), path=str(destination.path))

    def list(self, folder_id=None):
        roots = self.allowed_roots()
        root_rows = [self.register(root, root) for root in roots]
        default = next((root for root in roots if root.path == Path(self.default()).absolute()), roots[0])
        default_id = self.register(default, default)['id']
        choice = self.resolve(default_id if folder_id is None else folder_id)
        destination, root = choice.destination, choice.root
        try:
            with pinned_parent(destination.path / '.connector-list.guard'):
                self.check_bound(choice)
                folders = []
                with os.scandir(destination.path) as entries:
                    for count, entry in enumerate(entries, 1):
                        if count > MAX_ENTRIES:
                            raise DestinationError('這個位置的項目過多，請選擇其他資料夾。', 'destination_limit')
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        try:
                            child = Destination.choose(entry.path)
                            if not child.path.is_relative_to(root.path):
                                continue
                            self.check_isolation(child.path)
                        except (ValueError, OSError):
                            continue
                        folders.append(child)
                        if len(folders) > MAX_FOLDERS:
                            raise DestinationError('這個位置的資料夾過多，請選擇其他位置。', 'destination_limit')
                current = self.register(destination, root)
                current['parent_id'] = (None if destination.path == root.path else
                                        self.register(Destination.choose(destination.path.parent), root)['id'])
                result = dict(roots=root_rows, current=current,
                              folders=[self.register(child, root) for child in sorted(folders, key=lambda d: d.path.name.casefold())],
                              default_id=default_id)
                if len(canonical(result)) > MAX_REPLY:
                    raise DestinationError('資料夾清單過大，請選擇其他位置。', 'destination_limit')
                return result
        except DestinationError:
            raise
        except (ValueError, OSError) as error:
            raise DestinationError('無法開啟這個資料夾，請重新選擇。') from error
