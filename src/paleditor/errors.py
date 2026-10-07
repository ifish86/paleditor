"""Exception types shared across paleditor."""


class PaleditorError(Exception):
    """Base class for every error paleditor raises deliberately."""


class ConfigError(PaleditorError):
    """Configuration is missing, malformed or unsafe.

    Raised only at startup. The service must refuse to run rather than fall
    back to a permissive default, because the write path can modify the world.
    """


class SaveFormatError(PaleditorError):
    """The save file did not match the structure the parser expects.

    Treated as fatal for ingest and for the write path: a layout we do not
    recognise is a layout we must not write to.
    """


class ParserUnavailable(PaleditorError):
    """The configured save backend is not installed or not importable."""


class RconError(PaleditorError):
    """An RCON command failed, timed out, or the connection was refused."""


class MaintenanceError(PaleditorError):
    """The maintenance window could not complete."""


class WindowBusy(MaintenanceError):
    """Another maintenance run already holds the lock."""
