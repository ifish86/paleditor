"""TOML configuration loading and the safety rules enforced at startup.

The loader is deliberately strict. Every rule here exists because the write
path can modify a world that a dozen people play in, so an accidental
misconfiguration should stop the service rather than widen its blast radius.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

# Addresses we accept while allow_public is false. Python's ``is_private`` is
# the wrong test on both ends: it reports True for 0.0.0.0 (which must always
# be refused) and False for 100.64.0.0/10, the CGNAT range Tailscale hands out
# and the range the example config listens on.
_VPN_RANGES = (
    ipaddress.ip_network("100.64.0.0/10"),  # RFC 6598, used by Tailscale
)

_PLAINTEXT_HASH_HINT = re.compile(r"^\$argon2(id|i|d)\$")

# A cron expression with five fields. Enough to reject typos at startup; the
# scheduler does the real parsing.
_CRON_RE = re.compile(r"^\s*(\S+\s+){4}\S+\s*$")


@dataclass(frozen=True)
class ListenAddress:
    host: str
    port: int

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class ServerConfig:
    listen: tuple[ListenAddress, ...]
    allow_public: bool = False


@dataclass(frozen=True)
class AuthConfig:
    password_hash: str
    owner_password_hash: str
    session_days: int = 30
    # Signing key for session cookies. Generated and persisted on first run if
    # absent, so there is no committed default secret.
    session_key_file: Path | None = None


@dataclass(frozen=True)
class PalworldConfig:
    save_dir: Path
    server_unit: str = "palworld.service"
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password_file: Path | None = None
    # Which save backend to use. "cheahjs" is the real parser; "fixture" reads
    # a JSON dump and exists for development and tests.
    save_backend: str = "cheahjs"

    @property
    def level_sav(self) -> Path:
        return self.save_dir / "Level.sav"

    def rcon_password(self) -> str:
        """Read the RCON password from its own file, on demand.

        Kept out of the config object so the password is never held in memory
        longer than a command needs it, and so the main config file can stay
        world-readable while the secret stays 0600.
        """
        if self.rcon_password_file is None:
            raise ConfigError("palworld.rcon_password_file is not set")
        try:
            return self.rcon_password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(
                f"cannot read palworld.rcon_password_file {self.rcon_password_file}: {exc}"
            ) from exc


@dataclass(frozen=True)
class MaintenanceConfig:
    schedule: str = "0 5 * * *"
    shutdown_warning_seconds: int = 60
    backup_dir: Path = Path("/var/lib/paleditor/backups")
    backup_count: int = 10
    # How long to wait for the server process to actually exit after the
    # countdown. Step 3 of the window does not trust the countdown.
    shutdown_timeout_seconds: int = 180
    # Set false to let the scheduled window run unattended; the manual
    # endpoint always requires the owner password regardless.
    enabled: bool = True
    lock_file: Path = Path("/var/lib/paleditor/maintenance.lock")


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path = Path("/var/lib/paleditor/paleditor.db")


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    auth: AuthConfig
    palworld: PalworldConfig
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    source_path: Path | None = None


def _require(table: dict, key: str, section: str):
    if key not in table:
        raise ConfigError(f"[{section}] is missing required key '{key}'")
    return table[key]


def _parse_listen(raw: object) -> ListenAddress:
    if not isinstance(raw, str):
        raise ConfigError(f"[server] listen entries must be strings, got {raw!r}")
    text = raw.strip()
    # Bracketed IPv6, e.g. [::1]:8080
    if text.startswith("["):
        host, _, port_text = text.rpartition("]:")
        host = host.lstrip("[")
        if not port_text:
            raise ConfigError(f"[server] listen entry {raw!r} is missing a port")
    else:
        host, sep, port_text = text.rpartition(":")
        if not sep:
            raise ConfigError(
                f"[server] listen entry {raw!r} must be 'host:port'"
            )
    try:
        port = int(port_text)
    except ValueError:
        raise ConfigError(f"[server] listen entry {raw!r} has a non-numeric port") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"[server] listen entry {raw!r} has a port out of range")
    return ListenAddress(host=host, port=port)


def _is_allowed_without_public(host: str) -> bool:
    """True when binding ``host`` is safe while allow_public is false."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # A hostname could resolve anywhere; refuse rather than resolve it at
        # startup and bind something different later.
        return False
    if addr.is_unspecified:
        # 0.0.0.0 and ::. Note these report is_private=True, so they must be
        # rejected before any is_private check.
        return False
    if addr.is_loopback:
        return True
    if any(addr in net for net in _VPN_RANGES if addr.version == net.version):
        return True
    return bool(addr.is_private)


def _validate_server(server: ServerConfig) -> None:
    if not server.listen:
        raise ConfigError("[server] listen must contain at least one address")
    if server.allow_public:
        return
    for addr in server.listen:
        if not _is_allowed_without_public(addr.host):
            raise ConfigError(
                f"[server] refusing to bind {addr} while allow_public is false. "
                "The write path can modify the world, so exposing it must be "
                "deliberate: set allow_public = true to bind a public address."
            )


def _validate_auth(auth: AuthConfig) -> None:
    for name, value in (
        ("password_hash", auth.password_hash),
        ("owner_password_hash", auth.owner_password_hash),
    ):
        if not value or not value.strip():
            raise ConfigError(
                f"[auth] {name} is unset. There is no default password and no "
                "first-run open window; generate one with 'paleditor hash-password'."
            )
        if not _PLAINTEXT_HASH_HINT.match(value.strip()):
            raise ConfigError(
                f"[auth] {name} does not look like an argon2 hash. It must start "
                "with '$argon2id$'; generate one with 'paleditor hash-password'."
            )
    if auth.password_hash.strip() == auth.owner_password_hash.strip():
        raise ConfigError(
            "[auth] password_hash and owner_password_hash are identical, which "
            "gives the whole friend group owner rights over the write path."
        )
    if auth.session_days < 1:
        raise ConfigError("[auth] session_days must be at least 1")


def _validate_palworld(pal: PalworldConfig, *, check_paths: bool) -> None:
    if pal.save_backend not in {"cheahjs", "fixture"}:
        raise ConfigError(
            f"[palworld] save_backend {pal.save_backend!r} is unknown; "
            "expected 'cheahjs' or 'fixture'"
        )
    if not check_paths:
        return
    level = pal.level_sav
    if not level.is_file():
        raise ConfigError(
            f"[palworld] save_dir {pal.save_dir} does not contain a Level.sav"
        )
    if not os.access(level, os.R_OK):
        raise ConfigError(f"[palworld] cannot read {level}")
    if pal.rcon_password_file is not None:
        secret = pal.rcon_password_file
        if not secret.is_file():
            raise ConfigError(
                f"[palworld] rcon_password_file {secret} does not exist"
            )
        mode = stat.S_IMODE(secret.stat().st_mode)
        if mode & 0o077:
            raise ConfigError(
                f"[palworld] rcon_password_file {secret} is mode {mode:04o}; "
                "it holds a secret and must not be group- or world-readable "
                "(chmod 600)."
            )


def _validate_writable_dir(path: Path, label: str) -> None:
    if path.exists():
        if not path.is_dir():
            raise ConfigError(f"{label} {path} exists but is not a directory")
        if not os.access(path, os.W_OK | os.X_OK):
            raise ConfigError(f"{label} {path} is not writable by this process")
        return
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ConfigError(
            f"{label} {path} does not exist and its parent {parent} is not "
            "writable, so it cannot be created"
        )


def _validate_maintenance(mw: MaintenanceConfig, *, check_paths: bool) -> None:
    if not _CRON_RE.match(mw.schedule):
        raise ConfigError(
            f"[maintenance] schedule {mw.schedule!r} is not a five-field cron "
            "expression, e.g. '0 5 * * *'"
        )
    if mw.backup_count < 1:
        raise ConfigError("[maintenance] backup_count must be at least 1")
    if mw.shutdown_warning_seconds < 0:
        raise ConfigError("[maintenance] shutdown_warning_seconds cannot be negative")
    if mw.shutdown_timeout_seconds <= mw.shutdown_warning_seconds:
        raise ConfigError(
            "[maintenance] shutdown_timeout_seconds must exceed "
            "shutdown_warning_seconds, or the window times out during its own "
            "countdown"
        )
    if check_paths:
        _validate_writable_dir(mw.backup_dir, "[maintenance] backup_dir")
        # The window takes this lock before anything else, so an unwritable
        # location here means every window fails at runtime on a config that
        # otherwise validates.
        _validate_writable_dir(
            mw.lock_file.parent, "[maintenance] lock_file directory"
        )


def load(path: str | os.PathLike[str], *, check_paths: bool = True) -> Config:
    """Read and validate a config file.

    ``check_paths`` exists so the config can be syntax-checked off the server
    (``paleditor check-config``) without a save directory present.
    """
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file {path} does not exist") from None
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid TOML: {exc}") from exc
    return from_dict(raw, source_path=path, check_paths=check_paths)


def from_dict(
    raw: dict,
    *,
    source_path: Path | None = None,
    check_paths: bool = True,
) -> Config:
    unknown = set(raw) - {"server", "auth", "palworld", "maintenance", "database"}
    if unknown:
        raise ConfigError(
            f"unknown config section(s): {', '.join(sorted(unknown))}"
        )

    server_raw = raw.get("server") or {}
    listen_raw = _require(server_raw, "listen", "server")
    if isinstance(listen_raw, str) or not isinstance(listen_raw, (list, tuple)):
        raise ConfigError("[server] listen must be an array of 'host:port' strings")
    server = ServerConfig(
        listen=tuple(_parse_listen(entry) for entry in listen_raw),
        allow_public=bool(server_raw.get("allow_public", False)),
    )

    auth_raw = raw.get("auth") or {}
    session_key_file = auth_raw.get("session_key_file")
    auth = AuthConfig(
        password_hash=str(_require(auth_raw, "password_hash", "auth")),
        owner_password_hash=str(_require(auth_raw, "owner_password_hash", "auth")),
        session_days=int(auth_raw.get("session_days", 30)),
        session_key_file=Path(session_key_file) if session_key_file else None,
    )

    pal_raw = raw.get("palworld") or {}
    secret_file = pal_raw.get("rcon_password_file")
    palworld = PalworldConfig(
        save_dir=Path(_require(pal_raw, "save_dir", "palworld")).expanduser(),
        server_unit=str(pal_raw.get("server_unit", "palworld.service")),
        rcon_host=str(pal_raw.get("rcon_host", "127.0.0.1")),
        rcon_port=int(pal_raw.get("rcon_port", 25575)),
        rcon_password_file=Path(secret_file).expanduser() if secret_file else None,
        save_backend=str(pal_raw.get("save_backend", "cheahjs")),
    )

    mw_raw = raw.get("maintenance") or {}
    defaults = MaintenanceConfig()
    maintenance = MaintenanceConfig(
        schedule=str(mw_raw.get("schedule", defaults.schedule)),
        shutdown_warning_seconds=int(
            mw_raw.get("shutdown_warning_seconds", defaults.shutdown_warning_seconds)
        ),
        backup_dir=Path(mw_raw.get("backup_dir", defaults.backup_dir)).expanduser(),
        backup_count=int(mw_raw.get("backup_count", defaults.backup_count)),
        shutdown_timeout_seconds=int(
            mw_raw.get("shutdown_timeout_seconds", defaults.shutdown_timeout_seconds)
        ),
        enabled=bool(mw_raw.get("enabled", defaults.enabled)),
        lock_file=Path(mw_raw.get("lock_file", defaults.lock_file)).expanduser(),
    )

    db_raw = raw.get("database") or {}
    database = DatabaseConfig(
        path=Path(db_raw.get("path", DatabaseConfig().path)).expanduser()
    )

    _validate_server(server)
    _validate_auth(auth)
    _validate_palworld(palworld, check_paths=check_paths)
    _validate_maintenance(maintenance, check_paths=check_paths)
    if check_paths:
        _validate_writable_dir(database.path.parent, "[database] path")

    return Config(
        server=server,
        auth=auth,
        palworld=palworld,
        maintenance=maintenance,
        database=database,
        source_path=source_path,
    )
