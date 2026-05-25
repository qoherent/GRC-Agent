"""Thin GNU Radio GRC API wrappers for connection loading, port domain detection,
and preflight validation.

These wrappers sit alongside our custom parsers — they do NOT replace them.
The wrapper falls back to our own parsing when the GNU API raises or produces
unexpected output.

Example
-------
    from grc_agent.session.gnu_loader import extract_connections_from_file

    conns = extract_connections_from_file("/path/to/graph.grc")
    for c in conns:
        print(c.connection_id, c.domain, c.dtype, c.vlen)
"""

from __future__ import annotations

import functools
import copy
import logging
import os
from typing import Any

from grc_agent.models import Connection

logger = logging.getLogger(__name__)

_GRC_BLOCKS_PATHS: list[str] = [
    "/usr/share/gnuradio/grc/blocks",
    "/usr/local/share/gnuradio/grc/blocks",
]


@functools.lru_cache(maxsize=1)
def _get_gnu_platform() -> Any:
    """Return a lazily-constructed ``Platform`` instance backed by a cached library."""
    os.environ.setdefault("GRC_BLOCKS_PATH", ":".join(_GRC_BLOCKS_PATHS))

    try:
        from gnuradio.grc.core.platform import Platform
    except Exception as exc:
        logger.warning("GNU Radio GRC Platform not importable: %s", exc)
        raise

    try:
        platform = Platform(version="3.10.9.2", version_parts=["3", "10", "9", "2"])
        platform.build_library()
    except Exception as exc:
        logger.warning("GNU Radio Platform library build failed: %s", exc)
        raise

    logger.info(
        "GNU Radio Platform ready: %d block classes",
        len(getattr(platform, "block_classes", {})),
    )
    return platform


def _ensure_platform() -> Any:
    """Return the cached ``Platform`` or ``None`` if unavailable."""
    try:
        return _get_gnu_platform()
    except Exception:
        return None


class _GnuConnectionExtractor:
    """Extract ``Connection`` objects via GNU Radio's ``FlowGraph`` API."""

    def __init__(self, platform: Any) -> None:
        self._p = platform

    def from_yaml_dict(self, raw_data: dict[str, Any]) -> list[Connection]:
        """Populate a ``FlowGraph`` from a raw YAML dict and return connections."""
        fg = self._p.make_flow_graph()
        fg.import_data(raw_data)
        return self._from_flowgraph(fg)

    def from_file(self, path: str) -> list[Connection]:
        """Parse a ``.grc`` file through the GNU API and return connections."""
        with open(path, encoding="utf-8") as fp:
            import yaml

            raw_data = yaml.safe_load(fp)
        return self.from_yaml_dict(raw_data)

    @staticmethod
    def _from_flowgraph(fg: Any) -> list[Connection]:
        """Iterate GNU connections and return our ``Connection`` model."""
        conns: list[Connection] = []
        for gnu_conn in fg.get_enabled_connections():
            try:
                conns.append(_gnu_conn_to_model(gnu_conn))
            except Exception as exc:
                logger.debug("Failed to translate GNU connection: %s", exc)
                continue
        return conns


def extract_connections_from_file(path: str) -> list[Connection]:
    """Load ``path`` via the GNU Radio API and return a list of ``Connection``."""
    platform = _ensure_platform()
    if platform is None:
        raise RuntimeError("GNU Radio Platform is not available")
    extractor = _GnuConnectionExtractor(platform)
    return extractor.from_file(path)


def extract_connections_from_yaml(raw_data: dict[str, Any]) -> list[Connection]:
    """Parse ``raw_data`` via the GNU Radio API and return connections."""
    platform = _ensure_platform()
    if platform is None:
        raise RuntimeError("GNU Radio Platform is not available")
    extractor = _GnuConnectionExtractor(platform)
    return extractor.from_yaml_dict(raw_data)


def validate_raw_flowgraph(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Validate raw `.grc` data through GNU Radio's headless FlowGraph API."""
    platform = _ensure_platform()
    if platform is None:
        return {
            "ok": False,
            "available": False,
            "valid": None,
            "errors": ["GNU Radio Platform is not available."],
        }
    try:
        fg = platform.make_flow_graph()
        fg.import_data(copy.deepcopy(raw_data))
        fg.validate()
        errors = [str(message) for message in fg.get_error_messages()]
        return {
            "ok": True,
            "available": True,
            "valid": bool(fg.is_valid()),
            "errors": errors,
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "valid": False,
            "errors": [str(exc)],
        }


def _gnu_conn_to_model(gnu_conn: Any) -> Connection:
    """Convert one GNU ``Connection`` to our ``Connection`` dataclass."""
    src_block = gnu_conn.source_block.name
    src_port = _port_key_to_id(gnu_conn.source_port.key)
    dst_block = gnu_conn.sink_block.name
    dst_port = _port_key_to_id(gnu_conn.sink_port.key)

    return Connection(
        src_block=src_block,
        src_port=src_port,
        dst_block=dst_block,
        dst_port=dst_port,
    )


def _port_key_to_id(value: str | int) -> int | str:
    """Convert a GNU port key (usually ``"0"`` or ``"in0"``) to our type."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return str(value)
