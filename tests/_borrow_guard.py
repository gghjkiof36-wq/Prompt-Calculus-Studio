"""Test-only, fail-before-write guards for the synchronous borrow boundaries.

Install before test discovery with PCS_BORROW_GUARD=1 and install_from_env().
Product modules never import this module. Shared JSON dicts/lists are replaced
with detached, recursively guarded copies; owned containers remain writable.
This diagnoses writes, not alias identity, late serialization or arbitrary
mutable Python objects. Explicit dict/list base-class calls can bypass it.
"""
import copy
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import sys
import traceback

events = []
calls = {}
current_test = None
_replacements = []
PRODUCT = 'prompt_calculus_studio'


class BorrowViolation(AssertionError):
    """A write was attempted through a borrowed container; nothing was written."""

    def __init__(self, event):
        self.event = event
        location = ' -> '.join(event['site']) or event['caller']
        super().__init__(f"Borrowed data write: {event['operation']} at {event['path']} "
                         f"(key={event['key']}); product call site: {location}")


def _deny(obj, operation, key=None):
    stack = traceback.extract_stack()[:-2]
    site = [f'{frame.filename}:{frame.lineno}:{frame.name}' for frame in stack
            if PRODUCT in Path(frame.filename).parts]
    caller = next((frame for frame in reversed(stack) if frame.filename != __file__), stack[-1])
    event = dict(operation=operation, path='/'.join(map(str, obj._path)), key=repr(key),
                 site=site[-4:], caller=f'{caller.filename}:{caller.lineno}:{caller.name}',
                 test=current_test, expected=False)
    events.append(event)
    raise BorrowViolation(event)


class GuardedDict(dict):
    def __setitem__(self, key, value): _deny(self, 'setitem', key)
    def __delitem__(self, key): _deny(self, 'delitem', key)
    def pop(self, key, *default):
        if key in self: _deny(self, 'pop', key)
        return dict.pop(self, key, *default)
    def popitem(self): _deny(self, 'popitem')
    def clear(self):
        if self: _deny(self, 'clear')
    def update(self, *args, **kwargs):
        other = dict(*args, **kwargs)
        if other: _deny(self, 'update', tuple(other))
    def setdefault(self, key, default=None):
        if key not in self: _deny(self, 'setdefault', key)
        return self[key]
    def __ior__(self, other):
        self.update(other)
        return self
    def __copy__(self): return dict(self)
    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        result.update((copy.deepcopy(k, memo), copy.deepcopy(v, memo)) for k, v in self.items())
        return result


class GuardedList(list):
    def __setitem__(self, key, value): _deny(self, 'setitem', key)
    def __delitem__(self, key): _deny(self, 'delitem', key)
    def append(self, value): _deny(self, 'append')
    def extend(self, values): _deny(self, 'extend')
    def insert(self, index, value): _deny(self, 'insert', index)
    def remove(self, value): _deny(self, 'remove', value)
    def pop(self, index=-1): _deny(self, 'pop', index)
    def clear(self):
        if self: _deny(self, 'clear')
    def sort(self, *args, **kwargs): _deny(self, 'sort')
    def reverse(self): _deny(self, 'reverse')
    def __iadd__(self, values): _deny(self, 'iadd')
    def __imul__(self, count): _deny(self, 'imul', count)
    def __copy__(self): return list(self)
    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(value, memo) for value in self)
        return result


def watch(obj, path, memo=None):
    """Detach JSON containers and block writes, preserving aliases within a copy."""
    memo = {} if memo is None else memo
    if isinstance(obj, (GuardedDict, GuardedList)):
        return obj
    if id(obj) in memo:
        return memo[id(obj)]
    if isinstance(obj, dict):
        result = GuardedDict()
        result._path = path
        memo[id(obj)] = result
        for key, value in obj.items():
            dict.__setitem__(result, key, watch(value, path + (key,), memo))
        return result
    if isinstance(obj, list):
        result = GuardedList()
        result._path = path
        memo[id(obj)] = result
        list.extend(result, (watch(value, path + (index,), memo) for index, value in enumerate(obj)))
        return result
    return obj


def guard_shared(view, base, path, memo=None):
    """Guard only containers identical to the corresponding input container."""
    memo = {} if memo is None else memo
    if isinstance(view, (dict, list)) and view is base:
        return watch(view, path, memo)
    if isinstance(view, dict) and isinstance(base, dict) and not isinstance(view, GuardedDict):
        for key in view:
            if key in base:
                view[key] = guard_shared(view[key], base[key], path + (key,), memo)
    elif isinstance(view, list) and isinstance(base, list) and not isinstance(view, GuardedList):
        for index in range(min(len(view), len(base))):
            view[index] = guard_shared(view[index], base[index], path + (index,), memo)
    return view


def unexpected_events():
    return [event for event in events if not event['expected']]


@contextmanager
def expect_violation(path):
    """Positive controls acknowledge only the exact exception they catch."""
    try:
        yield
    except BorrowViolation as exc:
        if path not in str(exc):
            raise
        exc.event['expected'] = True
    else:
        raise AssertionError(f'Borrow guard did not reject a write at {path}')


def install_from_env():
    if os.environ.get('PCS_BORROW_GUARD') == '1':
        install()


def install():
    """Patch definitions and already imported product aliases; safe to repeat."""
    if _replacements:
        return
    from prompt_calculus_studio import stage_context as sc, workspace_scene as ws

    def replace(module, name, make_wrapper):
        original = getattr(module, name)
        label = module.__name__ + '.' + name
        wrapped = make_wrapper(original)

        @wraps(original)
        def counted(*args, **kwargs):
            calls[label] = calls.get(label, 0) + 1
            return wrapped(*args, **kwargs)

        # Later imports receive the patched definition. Fix earlier from-imports
        # too, without importing UI modules just to install a test guard.
        for loaded in list(sys.modules.values()):
            if loaded is None or not getattr(loaded, '__name__', '').startswith(PRODUCT + '.'):
                continue
            for key, value in list(vars(loaded).items()):
                if value is original:
                    setattr(loaded, key, counted)
        _replacements.append((original, counted))

    def execution_state(original):
        def guarded(state, suppliers=()):
            return guard_shared(original(state, suppliers), state, ('view',))
        return guarded

    def borrowed_base(original):
        def guarded(live, scene=None, *, copy_top=True):
            return guard_shared(original(live, scene, copy_top=copy_top), live, ('base',))
        return guarded

    def merge_saved(original):
        def guarded(outer, inner, *, borrow=False):
            result = original(outer, inner, borrow=borrow)
            if borrow:
                result = guard_shared(result, outer, ('saved',))
                result = guard_shared(result, inner, ('saved',))
            return result
        return guarded

    def capture(original):
        def guarded(state, plan, keys, directory, policies=None, per_round=False, *, borrow=False):
            saved = original(state, plan, keys, directory, policies, per_round, borrow=borrow)
            if borrow:
                for key, item in saved.get('values', {}).items():
                    for cid, record in item.get('origin', {}).get('canvas_records', {}).items():
                        record['canvas'] = guard_shared(record['canvas'], state['multi_output']['canvases'][cid],
                                                        ('capture', key, 'canvas', cid))
                        record['roots'] = guard_shared(record['roots'], state['uses'], ('capture', key, 'roots'))
            return saved
        return guarded

    def execution_scene(original):
        def guarded(state):
            result = guard_shared(original(state), state, ('scene',))
            result['generation_local'] = guard_shared(result['generation_local'], state.get('generation', {}),
                                                       ('scene', 'generation_local'))
            return result
        return guarded

    def copy_context(original):
        def guarded(context):
            return guard_shared(original(context), context, ('ctx',))
        return guarded

    def load(original):
        def guarded(state, value, *, readonly=False):
            profiles = state.get('generation', {}).get('profiles')
            original(state, value, readonly=readonly)
            if readonly:
                guard_shared(state, value, ('scene_validate',))
                guard_shared(state['generation'], value.get('generation_local', {}), ('scene_validate', 'generation'))
                if profiles is not None:
                    state['generation']['profiles'] = guard_shared(state['generation']['profiles'], profiles,
                                                                   ('scene_validate', 'generation', 'profiles'))
        return guarded

    def execution_transaction(original):
        def guarded(state):
            before = copy.deepcopy(state)
            transaction = original(state)
            def check():
                def differences(left, right, path=()):
                    if isinstance(left, dict) and isinstance(right, dict):
                        for key in left.keys() | right.keys():
                            if key not in left or key not in right: yield path+(key,)
                            else: yield from differences(left[key], right[key], path+(key,))
                    elif isinstance(left, list) and isinstance(right, list):
                        if len(left) != len(right): yield path
                        for i in range(min(len(left), len(right))):
                            yield from differences(left[i], right[i], path+(i,))
                    elif left != right: yield path
                for path in differences(before, state):
                    if not any(path[:len(p)] == p for p in transaction.records):
                        _deny(type('LivePath', (), {'_path': ('live',)+path})(), 'unregistered execution write')
            commit, rollback = transaction.commit, transaction.rollback
            def committed():
                commit()
                check()
            def rolled_back():
                rollback()
                check()
            transaction.commit = committed
            transaction.rollback = rolled_back
            return transaction
        return guarded

    replace(sc, 'ExecutionTransaction', execution_transaction)
    for name, wrapper in (('execution_state', execution_state), ('borrowed_base', borrowed_base),
                          ('merge_saved', merge_saved), ('capture', capture),
                          ('execution_scene', execution_scene), ('copy_context', copy_context)):
        replace(sc, name, wrapper)
    replace(ws, 'load', load)
    install_live_guard()


def uninstall():
    """Restore definitions and aliases, including aliases imported while active."""
    for original, wrapped in reversed(_replacements):
        for loaded in list(sys.modules.values()):
            if loaded is None or not getattr(loaded, '__name__', '').startswith(PRODUCT + '.'):
                continue
            for name, value in list(vars(loaded).items()):
                if value is wrapped:
                    setattr(loaded, name, original)
    _replacements.clear()
    uninstall_live_guard()


@contextmanager
def installed():
    active = bool(_replacements)
    install()
    try:
        yield
    finally:
        if not active:
            uninstall()


# Live content has a different contract from borrowed read views: writes are
# allowed only inside an entry/execution, or at an enumerated historical site.
_live_original = None
_live_allowlist = ()
_live_fields = frozenset()


def _same_live_owner(left, right):
    return left is right or (left is not None and
        getattr(left, 'document', None) is not None and
        getattr(left, 'document', None) is getattr(right, 'document', None))


def _live_check(obj, operation, key=None):
    owner = obj._owner()
    if owner is None or _live_original is None:
        return
    path = obj._path + ((key,) if not obj._path and key is not None else ())
    watched = not path or path[0] in _live_fields
    frame = sys._getframe(1)
    product = False
    authorized = False
    while frame is not None:
        module = frame.f_globals.get('__name__', '')
        qualname = frame.f_code.co_qualname
        local = frame.f_locals
        if module.startswith(PRODUCT + '.'):
            # Tests also call pure model helpers to construct fixtures. Those
            # are not UI dispatches: require an owner/controller on the stack,
            # or the explicit synthetic-handler scope used by positive tests.
            controller = local.get('self')
            window = getattr(controller, 'window', None) if controller is not None else None
            canvas = local.get('canvas') or (getattr(controller, 'canvas', None) if controller is not None else None)
            product = product or _same_live_owner(controller, owner) or _same_live_owner(window, owner)
            product = product or _same_live_owner(local.get('window'), owner) or _same_live_owner(getattr(canvas, 'window', None), owner)
            product = product or controller is getattr(owner, 'store', None)
            if module == PRODUCT + '.window' and qualname == 'Window.update_document' and _same_live_owner(local.get('self'), owner):
                fields = local['fields']
                if path and path[0] not in fields:
                    _deny(obj, 'undeclared document field', path[0])
                authorized = True
            elif module == PRODUCT + '.window' and qualname == 'Window.adopt_document.<locals>.publish' and _same_live_owner(local.get('self'), owner):
                authorized = True
            elif module == PRODUCT + '.document_operations' and qualname == 'DocumentOperations.commit':
                ops = local['self']
                if ops.state() is owner.state: authorized = True
            elif module == PRODUCT + '.stage_context' and qualname == 'ExecutionTransaction.rollback':
                if local['self'].state is owner.state: authorized = True
            for entry in _live_allowlist.get((module, qualname), ()):
                if entry.get('legacy_window_only') and getattr(owner, 'compact_editor', None) is not False:
                    continue
                if 'max_multi_version' in entry and owner.state.get('multi_output', {}).get('version', 0) > entry['max_multi_version']:
                    continue
                allowed = entry['fields']
                if '*' in allowed or path and path[0] in allowed:
                    authorized = True
        frame = frame.f_back
    # Fixture construction is not a UI handler. A test-defined handler can use
    # live_handler() below to opt into the same check without a fake filename.
    if not product and not getattr(owner, '_live_handler_test', False):
        return
    if authorized or not watched:
        return
    store = getattr(owner, 'store', None)
    manager = getattr(getattr(store, 'db', None), '_transactions', None)
    execution = getattr(manager, 'execution', None)
    if execution is not None and execution.state is owner.state:
        return  # S3's independent snapshot guard verifies write-ahead records.
    _deny(obj, 'live write outside document entry: '+operation, key)


def _live_wrap(value, owner, path=(), memo=None):
    import weakref
    if owner is None: return value
    memo = {} if memo is None else memo
    if isinstance(value, (LiveDict, LiveList)) and value._owner() is owner:
        return value
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, (dict, list)):
        result = LiveDict() if isinstance(value, dict) else LiveList()
        document = getattr(owner, 'document', None)
        if document is None:
            result._owner = weakref.ref(owner)
        else:
            document_ref, host_ref = weakref.ref(document), weakref.ref(owner)
            result._owner = lambda: (getattr(document_ref(), 'host', None) or host_ref())
        result._path = path
        memo[id(value)] = result
        if isinstance(value, dict):
            for key, child in value.items(): dict.__setitem__(result, key, _live_wrap(child, owner, path+(key,), memo))
        else:
            list.extend(result, (_live_wrap(child, owner, path+(i,), memo) for i, child in enumerate(value)))
        return result
    return value


class LiveDict(dict):
    def __setitem__(self, key, value):
        _live_check(self, 'setitem', key)
        dict.__setitem__(self, key, _live_wrap(value, self._owner(), self._path+(key,)))
    def __delitem__(self, key):
        _live_check(self, 'delitem', key); dict.__delitem__(self, key)
    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items(): self[key] = value
    def setdefault(self, key, default=None):
        if key not in self: self[key] = default
        return self[key]
    def pop(self, key, *default):
        if key in self: _live_check(self, 'pop', key)
        return dict.pop(self, key, *default)
    def popitem(self):
        if self: _live_check(self, 'popitem', next(reversed(self)))
        return dict.popitem(self)
    def clear(self):
        for key in self: _live_check(self, 'clear', key)
        dict.clear(self)
    def __ior__(self, other):
        self.update(other); return self
    def __copy__(self): return dict(self)
    def __deepcopy__(self, memo):
        result = {}; memo[id(self)] = result
        result.update((copy.deepcopy(k, memo), copy.deepcopy(v, memo)) for k, v in self.items())
        return result


class LiveList(list):
    def __setitem__(self, key, value):
        _live_check(self, 'setitem', key)
        if isinstance(key, slice): value = [_live_wrap(v, self._owner(), self._path+(i,)) for i, v in enumerate(value)]
        else: value = _live_wrap(value, self._owner(), self._path+(key,))
        list.__setitem__(self, key, value)
    def __delitem__(self, key):
        _live_check(self, 'delitem', key); list.__delitem__(self, key)
    def append(self, value):
        _live_check(self, 'append'); list.append(self, _live_wrap(value, self._owner(), self._path+(len(self),)))
    def extend(self, values):
        for value in values: self.append(value)
    def insert(self, index, value):
        _live_check(self, 'insert', index); list.insert(self, index, _live_wrap(value, self._owner(), self._path+(index,)))
    def remove(self, value):
        _live_check(self, 'remove'); list.remove(self, value)
    def pop(self, index=-1):
        _live_check(self, 'pop', index); return list.pop(self, index)
    def clear(self):
        if self: _live_check(self, 'clear')
        list.clear(self)
    def sort(self, *args, **kwargs):
        _live_check(self, 'sort'); list.sort(self, *args, **kwargs)
    def reverse(self):
        _live_check(self, 'reverse'); list.reverse(self)
    def __iadd__(self, values):
        self.extend(values); return self
    def __imul__(self, count):
        _live_check(self, 'imul'); list.__imul__(self, count); return self
    def __copy__(self): return list(self)
    def __deepcopy__(self, memo):
        result = []; memo[id(self)] = result
        result.extend(copy.deepcopy(value, memo) for value in self)
        return result


@contextmanager
def live_handler(owner):
    """Exercise a synthetic UI handler (fixture writes otherwise stay allowed)."""
    old = getattr(owner, '_live_handler_test', False)
    owner._live_handler_test = True
    try: yield
    finally: owner._live_handler_test = old


def install_live_guard():
    global _live_original, _live_allowlist, _live_fields
    if _live_original is not None: return
    import json
    from prompt_calculus_studio.window import Window
    from prompt_calculus_studio.document_operations import MULTI_HISTORY_FIELDS
    _live_fields = frozenset(MULTI_HISTORY_FIELDS) | {'dictionary', 'generation'}
    entries = json.loads(Path(__file__).with_name('live_write_allowlist.json').read_text(encoding='utf-8'))['entries']
    _live_allowlist = {}
    for entry in entries:
        _live_allowlist.setdefault((entry['module'], entry['function']), []).append(entry)
    _live_original = Window.__setattr__
    original = _live_original
    def set_attribute(owner, key, value):
        if key == 'state' and isinstance(value, dict):
            old = getattr(owner, 'state', None)
            if isinstance(old, LiveDict):
                # Rebinding is itself a content write, even if nested writes
                # never pass through a monitored dict/list method.
                _live_check(old, 'replace-document')
            value = _live_wrap(value, owner)
        return original(owner, key, value)
    Window.__setattr__ = set_attribute


def uninstall_live_guard():
    global _live_original
    if _live_original is not None:
        from prompt_calculus_studio.window import Window
        Window.__setattr__ = _live_original
        _live_original = None
