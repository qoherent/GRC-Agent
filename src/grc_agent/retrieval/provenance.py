"""Structured provenance for retrieval results."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Provenance:
    """Point back to the source file and logical location that produced a result."""

    kind: str
    path: str
    pointer: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-friendly provenance payload."""
        return {
            "kind": self.kind,
            "path": self.path,
            "pointer": self.pointer,
        }


def catalog_provenance(path: Path, pointer: str, *, kind: str) -> Provenance:
    """Build provenance for one GNU Radio catalog record."""
    return Provenance(kind=kind, path=str(path), pointer=pointer)


def session_provenance(path: Path | None, pointer: str, *, kind: str) -> Provenance:
    """Build provenance for one active-session record."""
    return Provenance(
        kind=kind,
        path=str(path) if path is not None else "<in-memory-flowgraph>",
        pointer=pointer,
    )
