"""Shared test helpers for SAM Trader V3.

Utilities that are reused across multiple test modules.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator


@contextmanager
def patch_path_attrs(module: ModuleType, **kwargs: Path) -> Iterator[None]:
    """Patch Path attributes on importlib-loaded modules.

    `unittest.mock.patch.object` breaks `Path.exists()` when the target
    module was loaded via `importlib.util.spec_from_file_location`.
    The interaction between `module_from_spec` (separate namespace) and
    `patch.object` (`__dict__` manipulation) causes the `Path` object to
    lose its filesystem binding when accessed through the patched attribute.

    Use this helper instead of `patch.object` for Path attributes on
    dynamically-loaded modules.

    Example:
        with patch_path_attrs(wizard, ENV_PATH=env_path, TEMPLATE_PATH=template):
            rc = wizard.main()
    """
    originals: dict[str, Path] = {}
    for attr, value in kwargs.items():
        originals[attr] = getattr(module, attr)
        setattr(module, attr, value)
    try:
        yield
    finally:
        for attr, original in originals.items():
            setattr(module, attr, original)
