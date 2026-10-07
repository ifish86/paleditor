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


def get_backend(name: str, **kwargs) -> SaveBackend:
    if name == "palworld":
        from .palworld import PalworldBackend

        return PalworldBackend(**kwargs)
    if name == "fixture":
        from .fixture import FixtureBackend

        return FixtureBackend()
    if name == "cheahjs":
        raise ConfigError(
            "the 'cheahjs' backend has been removed. palworld-save-tools only "
            "reads the PlZ (zlib) container, and this server writes PlM (Oodle), "
            "so it could not read the save at all. Use save_backend = 'palworld'."
        )
    raise ConfigError(f"unknown save backend {name!r}; expected 'palworld' or 'fixture'")


def available_backends() -> dict[str, bool]:
    out = {}
    for name in ("palworld", "fixture"):
        try:
            out[name] = get_backend(name).available()
        except Exception:
            out[name] = False
    return out
