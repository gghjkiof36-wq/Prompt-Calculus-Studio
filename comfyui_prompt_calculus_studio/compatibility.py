"""Report backend and frontend separately from the PCS document protocol."""
from functools import lru_cache
from importlib.metadata import PackageNotFoundError,version


@lru_cache(maxsize=1)
def runtime_versions():
    try:
        from comfyui_version import __version__
    except ImportError:
        __version__='unknown'
    try:frontend=version('comfyui-frontend-package')
    except PackageNotFoundError:frontend='unknown'
    return dict(backend=__version__,frontend=frontend)
