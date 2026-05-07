"""Cross-process lock for retrieval eval gates that share a mutable vector index."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator


_LOCK_PATH = Path(".grc_agent/vector_index/.retrieval_eval.lock")


@contextmanager
def acquire_retrieval_eval_lock(gate_name: str) -> Iterator[None]:
    """Acquire a non-blocking lock; fail fast when parallel eval is detected."""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "retrieval eval lock is busy. "
                f"Run retrieval/vector gates sequentially (blocked gate: {gate_name})."
            ) from exc
        handle.write(f"pid={os.getpid()} gate={gate_name}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
