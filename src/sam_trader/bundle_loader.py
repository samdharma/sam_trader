"""Bundle loader — minimal stub for Phase 1."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class BundleLoaderError(Exception):
    """Raised when bundle loading fails."""


class BundleValidationError(Exception):
    """Raised when bundle validation fails."""


def load_bundles(path: str) -> list:
    """Load strategy bundles from a YAML file.

    Parameters
    ----------
    path : str
        Path to the bundles YAML file.

    Returns
    -------
    list
        List of strategy configs.

    Raises
    ------
    BundleLoaderError
        If the file does not exist or cannot be read.
    BundleValidationError
        If the file content is invalid.

    """
    if not os.path.exists(path):
        raise BundleLoaderError(f"Bundles file not found: {path}")

    # Phase 1 stub: bundles will be loaded in Phase 7.
    logger.warning(
        "Bundle loading not yet implemented (Phase 7). Returning empty list."
    )
    return []
