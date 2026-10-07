"""Save backends. Pick one with ``get_backend(name)``."""

from __future__ import annotations

from ..errors import ConfigError
from .base import SaveBackend
from .types import (
    ApplyReport,
    BaseRecord,
    ChestRecord,
    SlotEdit,
    SlotRecord,
    WorldSnapshot,
)

__all__ = [
    "ApplyReport",
    "BaseRecord",
    "ChestRecord",
    "SaveBackend",
    "SlotEdit",
    "SlotRecord",
    "WorldSnapshot",
    "get_backend",
    "available_backends",
]


def get_backend(name: str) -> SaveBackend:
    if name == "cheahjs":
        from .cheahjs import CheahjsBackend

        return CheahjsBackend()
    if name == "fixture":
        from .fixture import FixtureBackend

        return FixtureBackend()
    raise ConfigError(f"unknown save backend {name!r}; expected 'cheahjs' or 'fixture'")


def available_backends() -> dict[str, bool]:
    out = {}
    for name in ("cheahjs", "fixture"):
        try:
            out[name] = get_backend(name).available()
        except Exception:
            out[name] = False
    return out
