"""Catalog-specific errors and public error payload helpers."""

from typing import Any


class CatalogError(RuntimeError):
    """Base class for catalog metadata and description failures."""


class CatalogLoadError(CatalogError):
    """Raised when the GNU block catalog cannot be discovered or loaded."""


class BlockNotFoundError(CatalogError):
    """Raised when a block id is absent from the resolved GNU catalog."""

    def __init__(self, block_id: str, *, catalog_root: str) -> None:
        self.block_id = block_id
        self.catalog_root = catalog_root
        super().__init__(f"Block '{block_id}' not found in catalog.")


def build_error_payload(
    *,
    error_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the public error payload returned by catalog entry points."""
    payload: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }
    if details:
        payload["details"] = details
    return payload
