"""Remote ``sftp://`` dataset support.

The module is standard-library only at import time: ``paramiko`` is imported
lazily and only when an ``sftp://`` target is actually opened, so local
datasets never require the optional dependency.

Design: the remote SQLite catalog is mirrored into a per-dataset local cache
directory and opened read-only by ``FoggyVisionLibrary``.  Images and masks are
mirrored lazily by :func:`make_remote_decoder`, which runs on the image cache
worker thread, so no network I/O happens on the Tk UI thread.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import posixpath
import re
import stat as stat_module
import sys
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from PIL import Image

    from .image_cache import Decoder

DATABASE_FILE_NAME = "database.sqlite3"
STATE_FILE_NAME = "database.json"
STATE_VERSION = 1
SFTP_PREFIX = "sftp://"
DEFAULT_PORT = 22

_CHUNK_SIZE = 1024 * 1024
_APP_CACHE_DIR = "foggy-vision-viewer"
_CONNECT_TIMEOUT = 15.0
_CHANNEL_TIMEOUT = 30.0


class RemoteError(RuntimeError):
    """A remote dataset could not be parsed, reached, or read."""


class RemoteDependencyError(RemoteError):
    """An ``sftp://`` target needs the optional ``paramiko`` dependency."""


@dataclass(frozen=True, slots=True)
class RemoteLocation:
    """A parsed ``sftp://`` URL."""

    host: str
    username: str
    port: int
    path: str  # percent-decoded; a leading "~" is preserved for normalize()

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        if self.port == DEFAULT_PORT:
            return f"{self.username}@{host}"
        return f"{self.username}@{host}:{self.port}"

    @property
    def display(self) -> str:
        path = self.path if self.path.startswith("/") else "/" + self.path
        return f"sftp://{self.authority}{path}"


@dataclass(frozen=True, slots=True)
class RemoteStat:
    """The subset of remote file attributes the cache needs."""

    mtime: float
    size: int
    mode: Optional[int] = None

    @property
    def is_dir(self) -> bool:
        return self.mode is not None and stat_module.S_ISDIR(self.mode)


class Transport(Protocol):
    """The SFTP operations the cache relies on (easily faked in tests)."""

    def normalize(self, path: str) -> str: ...

    def stat(self, path: str) -> RemoteStat: ...

    def get(self, remote_path: str, destination: Path) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------- parsing


def is_remote_target(value: object) -> bool:
    """Whether ``value`` is an explicit ``sftp://`` URL.

    Only the literal prefix is accepted.  Inspecting ``urlsplit(value).scheme``
    would misclassify a Windows path such as ``C:\\data\\db.sqlite3`` as scheme
    ``c``, so detection deliberately does not do that.
    """
    return isinstance(value, str) and value.lower().startswith(SFTP_PREFIX)


def parse_sftp_url(value: str) -> RemoteLocation:
    """Parse an ``sftp://user@host:port/path`` URL."""
    if not is_remote_target(value):
        raise RemoteError(f"not an sftp:// URL: {value}")

    parts = urllib.parse.urlsplit(value)
    if parts.scheme.lower() != "sftp":
        raise RemoteError(f"not an sftp:// URL: {value}")
    if parts.query or parts.fragment:
        raise RemoteError("sftp:// URLs must not contain a query or fragment")
    if parts.password is not None:
        raise RemoteError(
            "do not embed a password in the sftp:// URL; use ssh-agent or a key "
            "file instead (passwords are never read from the command line)"
        )

    host = parts.hostname
    if not host:
        raise RemoteError("sftp:// URL is missing a host name")

    try:
        port = parts.port
    except ValueError as exc:
        raise RemoteError(f"invalid port in sftp:// URL: {exc}") from exc
    if port is None:
        port = DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise RemoteError(f"invalid port in sftp:// URL: {port}")

    username = urllib.parse.unquote(parts.username) if parts.username else ""
    if not username:
        username = _default_username()

    raw_path = parts.path
    if not raw_path:
        raise RemoteError("sftp:// URL is missing the remote database path")

    path = urllib.parse.unquote(raw_path)
    if "\x00" in path:
        raise RemoteError("sftp:// URL path must not contain NUL characters")
    if path.endswith("/"):
        directory = path.rstrip("/") or "/"
        raise RemoteError(
            "expected the database.sqlite3 file, got a directory: "
            f"{directory}; pass {posixpath.join(directory, DATABASE_FILE_NAME)}"
        )

    # "/~/data/db.sqlite3" means the remote home directory, not a path
    # component literally named "~".
    if path == "/~":
        path = "~"
    elif path.startswith("/~/"):
        path = path[1:]

    return RemoteLocation(host=host, username=username, port=port, path=path)


def _default_username() -> str:
    try:
        name = getpass.getuser()
    except Exception:  # pragma: no cover - depends on the host environment
        name = ""
    if name:
        return name
    for variable in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(variable)
        if value:
            return value
    raise RemoteError(
        "sftp:// URL has no user name and none could be determined; use "
        "sftp://user@host/path/database.sqlite3"
    )


# ---------------------------------------------------------------------- cache


def default_cache_root() -> Path:
    """The per-user cache root for mirrored remote datasets."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / _APP_CACHE_DIR / "remote"
        return Path.home() / "AppData" / "Local" / _APP_CACHE_DIR / "remote"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / _APP_CACHE_DIR / "remote"
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / _APP_CACHE_DIR / "remote"


def remote_cache_dir(
    location: RemoteLocation,
    remote_db_path: str,
    remote_images_path: str,
    *,
    root: Optional[Path] = None,
) -> Path:
    """The stable per-dataset cache directory for a remote target."""
    base = Path(root) if root is not None else default_cache_root()
    identity = (
        f"{location.username}@{location.host}:{location.port}\0"
        f"{remote_db_path}\0{remote_images_path}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    host = re.sub(r"[^A-Za-z0-9._-]+", "_", location.host).strip("._") or "host"
    return base / f"sftp_{host[:40]}_{location.port}_{digest}"


class RemoteSource:
    """A mirrored remote dataset: local DB copy plus lazy image cache."""

    def __init__(
        self,
        location: RemoteLocation,
        transport: Transport,
        cache_dir: Path,
        remote_db_path: str,
        remote_images_path: str,
        *,
        log: Callable[[str], None],
    ) -> None:
        self._location = location
        self._transport = transport
        self._cache_dir = cache_dir
        self._db_path = cache_dir / DATABASE_FILE_NAME
        self._images_dir = cache_dir / "images"
        self._state_path = cache_dir / STATE_FILE_NAME
        self._remote_db_path = remote_db_path
        self._remote_images_path = remote_images_path
        self._log = log
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def open(
        cls,
        url: str,
        *,
        images_dir: Optional[str | os.PathLike[str]] = None,
        cache_root: Optional[Path] = None,
        transport_factory: Optional[Callable[[RemoteLocation], Transport]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> "RemoteSource":
        """Connect, mirror the database, and return a ready source."""
        logger = log if log is not None else (lambda _message: None)
        location = parse_sftp_url(url)
        factory = transport_factory or ParamikoTransport.connect
        transport = factory(location)

        try:
            remote_db = _normalize(transport, location.path, "database")
            remote_images = _resolve_remote_images_dir(
                images_dir, transport, remote_db
            )
            attributes = _stat_database(transport, remote_db)
            if attributes.is_dir:
                raise RemoteError(
                    "expected the database.sqlite3 file, got a directory: "
                    f"{remote_db}; pass "
                    f"{posixpath.join(remote_db, DATABASE_FILE_NAME)}"
                )
            cache_dir = remote_cache_dir(
                location, remote_db, remote_images, root=cache_root
            )
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RemoteError(
                    f"could not create the local cache directory "
                    f"{cache_dir}: {exc}"
                ) from exc
        except BaseException:
            transport.close()
            raise

        source = cls(
            location,
            transport,
            cache_dir,
            remote_db,
            remote_images,
            log=logger,
        )
        try:
            source._sync_database(attributes)
        except BaseException:
            transport.close()
            raise
        return source

    # ------------------------------------------------------------- properties

    @property
    def db_path(self) -> Path:
        """Local mirror of ``database.sqlite3``."""
        return self._db_path

    @property
    def images_dir(self) -> Path:
        """Local mirror of the remote images tree (pass as ``images_dir``)."""
        return self._images_dir

    @property
    def display_label(self) -> str:
        """User-facing remote URL, suitable for the window title."""
        return self._location.display

    @property
    def remote_db_path(self) -> str:
        return self._remote_db_path

    @property
    def remote_images_path(self) -> str:
        return self._remote_images_path

    # ---------------------------------------------------------------- mirrors

    def ensure_image(self, relative: str) -> Path:
        """Return the local mirror path, downloading it if necessary."""
        parts = _validated_relative(relative)
        local = self._images_dir.joinpath(*parts)
        with self._lock:
            if self._closed:
                raise RemoteError("the remote dataset is closed")
            if local.is_file():
                return local
            remote = posixpath.join(self._remote_images_path, *parts)
            try:
                self._download(remote, local)
            except OSError as exc:
                raise RemoteError(f"could not download {remote}: {exc}") from exc
            return local

    def _sync_database(self, attributes: RemoteStat) -> None:
        state = _read_state(self._state_path)
        if (
            self._db_path.is_file()
            and state is not None
            and _state_matches(state, attributes)
            and self._db_path.stat().st_size == attributes.size
        ):
            return

        try:
            self._download(
                self._remote_db_path, self._db_path, expected_size=attributes.size
            )
        except (RemoteError, OSError) as exc:
            if self._db_path.is_file() and self._db_path.stat().st_size > 0:
                self._log(
                    f"could not refresh the remote database ({exc}); "
                    f"using the cached copy at {self._db_path}"
                )
                return
            if isinstance(exc, RemoteError):
                raise
            raise RemoteError(
                f"could not download remote database {self._remote_db_path}: "
                f"{exc}"
            ) from exc

        try:
            _write_state(
                self._state_path,
                attributes,
                self._remote_db_path,
                self._remote_images_path,
            )
        except OSError as exc:
            self._log(
                f"could not write cache metadata {self._state_path}: {exc}"
            )

    def _download(
        self,
        remote_path: str,
        destination: Path,
        *,
        expected_size: Optional[int] = None,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(handle)
        temporary_path = Path(temporary)
        try:
            self._transport.get(remote_path, temporary_path)
            if expected_size is not None:
                actual = temporary_path.stat().st_size
                if actual != expected_size:
                    raise RemoteError(
                        f"incomplete download of {remote_path}: expected "
                        f"{expected_size} bytes, got {actual}"
                    )
            os.replace(temporary_path, destination)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the transport.  Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self._transport.close()


# --------------------------------------------------------------------- decoder


def make_remote_decoder(
    source: RemoteSource, local_root: Path | str
) -> "Decoder":
    """Wrap ``decode_for_display`` so the file is mirrored before decoding."""
    from .imaging import decode_for_display

    root = Path(local_root)

    def decode(local_path: str) -> "Image.Image":
        path = Path(local_path)
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise RemoteError(
                f"image path is outside the local mirror: {path}"
            ) from exc
        source.ensure_image(relative)
        return decode_for_display(path)

    return decode


# ---------------------------------------------------------------- validation


def _validated_relative(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative:
        raise RemoteError("empty image path")
    if "\x00" in relative or "\\" in relative:
        raise RemoteError(f"unsafe image path: {relative!r}")
    path = PurePosixPath(relative)
    if path.is_absolute():
        raise RemoteError(f"absolute image path is not allowed: {relative!r}")
    parts = path.parts
    if not parts or any(part in ("", ".", "..") or ":" in part for part in parts):
        raise RemoteError(f"unsafe image path: {relative!r}")
    return parts


def _resolve_remote_images_dir(
    value: Optional[str | os.PathLike[str]],
    transport: Transport,
    remote_db_path: str,
) -> str:
    default = posixpath.join(posixpath.dirname(remote_db_path), "images")
    if value is None:
        return default
    text = os.fspath(value).strip()
    if not text:
        return default
    if is_remote_target(text):
        raise RemoteError(
            "--images-dir must be a remote POSIX path, not another sftp:// URL"
        )
    if "\\" in text or re.match(r"^[A-Za-z]:", text):
        raise RemoteError(
            "--images-dir for an sftp:// database is a remote POSIX path on the "
            f"same host, got {text!r}; use an absolute path such as "
            "/srv/foggyvision/images"
        )
    if text.startswith("~"):
        if text != "~" and not text.startswith("~/"):
            raise RemoteError(
                "only '~' and '~/...' are supported in --images-dir"
            )
        return _normalize(transport, text, "images directory")
    if not text.startswith("/"):
        raise RemoteError(
            "--images-dir for an sftp:// database must be an absolute remote "
            f"path or start with '~/', got {text!r}"
        )
    return posixpath.normpath(text)


def _normalize(transport: Transport, path: str, label: str) -> str:
    try:
        return transport.normalize(path)
    except FileNotFoundError as exc:
        raise RemoteError(f"remote {label} not found: {path}") from exc
    except PermissionError as exc:
        raise RemoteError(f"permission denied for remote {label}: {path}") from exc
    except (OSError, EOFError) as exc:
        raise RemoteError(f"could not resolve remote {label} {path}: {exc}") from exc


def _stat_database(transport: Transport, path: str) -> RemoteStat:
    try:
        return transport.stat(path)
    except FileNotFoundError as exc:
        raise RemoteError(f"remote database not found: {path}") from exc
    except PermissionError as exc:
        raise RemoteError(f"permission denied reading remote database: {path}") from exc
    except (OSError, EOFError) as exc:
        raise RemoteError(f"could not read remote database {path}: {exc}") from exc


# ------------------------------------------------------------------- metadata


def _read_state(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return None
    return data


def _state_matches(state: dict, attributes: RemoteStat) -> bool:
    try:
        return (
            float(state["mtime"]) == float(attributes.mtime)
            and int(state["size"]) == int(attributes.size)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _write_state(
    path: Path,
    attributes: RemoteStat,
    remote_db_path: str,
    remote_images_path: str,
) -> None:
    payload = {
        "version": STATE_VERSION,
        "mtime": attributes.mtime,
        "size": attributes.size,
        "remote_db": remote_db_path,
        "remote_images": remote_images_path,
    }
    handle, temporary = tempfile.mkstemp(
        prefix=".database.", suffix=".json.tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


# --------------------------------------------------------------- paramiko I/O


def _import_paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RemoteDependencyError(
            "remote databases need the optional 'paramiko' package.\n"
            'Install it with:  python -m pip install "foggy-vision-viewer[sftp]"'
        ) from exc
    return paramiko


class _RejectUnknownHostKey:
    """paramiko policy that refuses keys absent from ``known_hosts``."""

    def __init__(self, location: RemoteLocation) -> None:
        self._location = location

    def missing_host_key(self, client, hostname, key) -> None:
        raise RemoteError(_unknown_host_key_message(self._location, key))


class ParamikoTransport:
    """Real SFTP transport backed by a single paramiko connection."""

    def __init__(self, location: RemoteLocation, client, sftp) -> None:
        self._location = location
        self._client = client
        self._sftp = sftp
        self._closed = False

    @classmethod
    def connect(cls, location: RemoteLocation) -> "ParamikoTransport":
        paramiko = _import_paramiko()
        client, sftp = _open_session(paramiko, location)
        return cls(location, client, sftp)

    # ---------------------------------------------------------------- queries

    def normalize(self, path: str) -> str:
        try:
            return self._sftp.normalize(path)
        except (OSError, EOFError) as exc:
            raise RemoteError(
                f"could not resolve remote path {path}: {exc}"
            ) from exc

    def stat(self, path: str) -> RemoteStat:
        try:
            attributes = self._sftp.stat(path)
        except (FileNotFoundError, PermissionError):
            raise
        except (OSError, EOFError) as exc:
            raise RemoteError(f"could not stat remote path {path}: {exc}") from exc
        return RemoteStat(
            mtime=float(getattr(attributes, "st_mtime", 0.0) or 0.0),
            size=int(getattr(attributes, "st_size", 0) or 0),
            mode=getattr(attributes, "st_mode", None),
        )

    def get(self, remote_path: str, destination: Path) -> None:
        paramiko = _import_paramiko()
        transient = (OSError, EOFError, paramiko.SSHException)
        try:
            self._get_once(remote_path, destination)
            return
        except (FileNotFoundError, PermissionError):
            raise
        except transient:
            pass

        self._reconnect()
        try:
            self._get_once(remote_path, destination)
        except (FileNotFoundError, PermissionError):
            raise
        except transient as exc:
            raise RemoteError(
                f"could not download {remote_path}: {exc}"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_handles()

    # ---------------------------------------------------------------- helpers

    def _get_once(self, remote_path: str, destination: Path) -> None:
        with self._sftp.open(remote_path, "rb") as remote:
            with open(destination, "wb") as local:
                while True:
                    chunk = remote.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    local.write(chunk)
                local.flush()
                os.fsync(local.fileno())

    def _reconnect(self) -> None:
        self._close_handles()
        if self._closed:
            raise RemoteError("the remote connection is closed")
        paramiko = _import_paramiko()
        self._client, self._sftp = _open_session(paramiko, self._location)

    def _close_handles(self) -> None:
        for handle in (getattr(self, "_sftp", None), getattr(self, "_client", None)):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                pass
        self._sftp = None
        self._client = None


def _open_session(paramiko, location: RemoteLocation):
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(_RejectUnknownHostKey(location))
        client.connect(
            location.host,
            port=location.port,
            username=location.username,
            allow_agent=True,
            look_for_keys=True,
            timeout=_CONNECT_TIMEOUT,
            auth_timeout=_CONNECT_TIMEOUT,
            banner_timeout=_CONNECT_TIMEOUT,
        )
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(_CHANNEL_TIMEOUT)
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
    except BaseException as exc:
        client.close()
        translated = _translate_connect_error(paramiko, location, exc)
        if translated is exc:
            raise
        raise translated from exc
    return client, sftp


def _translate_connect_error(paramiko, location: RemoteLocation, exc: BaseException):
    if isinstance(exc, RemoteError):
        return exc
    if isinstance(exc, paramiko.BadHostKeyException):
        return RemoteError(_mismatched_host_key_message(location, exc))
    if isinstance(exc, paramiko.AuthenticationException):
        return RemoteError(_authentication_message(location))
    if isinstance(exc, (paramiko.SSHException, OSError, EOFError)):
        return RemoteError(
            f"could not connect to {location.authority}: {exc}"
        )
    return exc


# ------------------------------------------------------------------ messages


def _fingerprint(key) -> str:
    data = getattr(key, "asbytes", None)
    if callable(data):
        digest = hashlib.sha256(data()).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    try:  # pragma: no cover - very old paramiko
        digest = key.get_fingerprint()
    except Exception:
        return "unknown"
    return "MD5:" + ":".join(f"{byte:02x}" for byte in digest)


def _known_hosts_text() -> str:
    if os.name == "nt":
        return r"$env:USERPROFILE\.ssh\known_hosts"
    return "~/.ssh/known_hosts"


def _keyscan_command(location: RemoteLocation) -> str:
    host = f"[{location.host}]" if ":" in location.host else location.host
    if location.port != DEFAULT_PORT:
        return f"ssh-keyscan -p {location.port} {host}"
    return f"ssh-keyscan {host}"


def _remove_command(location: RemoteLocation) -> str:
    if ":" in location.host or location.port != DEFAULT_PORT:
        return f'ssh-keygen -R "[{location.host}]:{location.port}"'
    return f"ssh-keygen -R {location.host}"


def _unknown_host_key_message(location: RemoteLocation, key) -> str:
    name = getattr(key, "get_name", lambda: "unknown")()
    keyscan = _keyscan_command(location)
    return (
        f"the SSH host key for {location.authority} is not in "
        f"{_known_hosts_text()}.\n"
        f"server key: {name} {_fingerprint(key)}\n"
        "trust it by adding the key, then run again:\n"
        f"  {keyscan} >> {_known_hosts_text()}\n"
        f"inspect it first with:  {keyscan}"
    )


def _mismatched_host_key_message(location: RemoteLocation, exc) -> str:
    lines = [
        f"the SSH host key for {location.authority} does not match the entry "
        f"in {_known_hosts_text()}.",
    ]
    expected = getattr(exc, "expected_key", None)
    actual = getattr(exc, "key", None)
    if expected is not None:
        lines.append(
            f"known key:  {expected.get_name()} {_fingerprint(expected)}"
        )
    if actual is not None:
        lines.append(
            f"server key: {actual.get_name()} {_fingerprint(actual)}"
        )
    lines.append("if the server key changed legitimately, update the entry:")
    lines.append(f"  {_remove_command(location)}")
    lines.append(
        f"  {_keyscan_command(location)} >> {_known_hosts_text()}"
    )
    return "\n".join(lines)


def _authentication_message(location: RemoteLocation) -> str:
    if os.name == "nt":
        hint = (
            "load the key into Pageant (right-click its tray icon, then "
            "'Add Key') or into the Windows OpenSSH agent with:  ssh-add"
        )
        key_hint = (
            "or place an unencrypted id_ed25519/id_rsa in "
            "%USERPROFILE%\\.ssh"
        )
        extra = "\n(Pageant and the viewer must run at the same privilege level.)"
    else:
        hint = "load a key with:  ssh-add"
        key_hint = "or place an unencrypted id_ed25519/id_rsa in ~/.ssh"
        extra = ""
    return (
        f"authentication failed for {location.authority}.\n"
        "remote datasets use an SSH agent or key files; passwords are not "
        "supported.\n"
        f"{hint}\n"
        f"{key_hint}, and make sure the matching public key is authorized "
        f"on the server.{extra}"
    )
