from __future__ import annotations

import io
import json
import posixpath
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
from PIL import Image

from foggy_vision_loader import FoggyVisionLibrary

from foggy_vision_viewer import remote
from foggy_vision_viewer.image_cache import ImageCache, key_for
from foggy_vision_viewer.remote import (
    ParamikoTransport,
    RemoteDependencyError,
    RemoteError,
    RemoteSource,
    RemoteStat,
    is_remote_target,
    make_remote_decoder,
    parse_sftp_url,
    remote_cache_dir,
)

from .helpers import build_db, sample_frames

DB_URL = "sftp://tester@example.test/srv/foggyvision/database.sqlite3"
DB_PATH = "/srv/foggyvision/database.sqlite3"
IMAGES_PATH = "/srv/foggyvision/images"


class FakeTransport:
    """In-memory stand-in for an SFTP session."""

    def __init__(
        self,
        files: Optional[dict[str, bytes]] = None,
        *,
        directories: Optional[set[str]] = None,
        mtimes: Optional[dict[str, float]] = None,
        home: str = "/home/tester",
    ) -> None:
        self.files = dict(files or {})
        self.directories = set(directories or ())
        self.mtimes = dict(mtimes or {})
        self.home = home
        self.get_calls: list[str] = []
        self.stat_calls: list[str] = []
        self.normalize_calls: list[str] = []
        self.closed = False
        self.get_error: Optional[Exception] = None
        self.get_hook: Optional[Callable[[str, Path], None]] = None

    def normalize(self, path: str) -> str:
        self.normalize_calls.append(path)
        if path == "~":
            return self.home
        if path.startswith("~/"):
            return posixpath.join(self.home, path[2:])
        if not path.startswith("/"):
            return posixpath.join(self.home, path)
        return path

    def stat(self, path: str) -> RemoteStat:
        self.stat_calls.append(path)
        if path in self.directories:
            return RemoteStat(mtime=0.0, size=0, mode=stat.S_IFDIR)
        if path not in self.files:
            raise FileNotFoundError(path)
        return RemoteStat(
            mtime=self.mtimes.get(path, 0.0),
            size=len(self.files[path]),
            mode=stat.S_IFREG,
        )

    def get(self, remote_path: str, destination: Path) -> None:
        self.get_calls.append(remote_path)
        if self.get_hook is not None:
            self.get_hook(remote_path, Path(destination))
            return
        if self.get_error is not None:
            raise self.get_error
        if remote_path not in self.files:
            raise FileNotFoundError(remote_path)
        Path(destination).write_bytes(self.files[remote_path])

    def close(self) -> None:
        self.closed = True


def _open(
    tmp_path: Path,
    transport: FakeTransport,
    *,
    url: str = DB_URL,
    cache_root: Optional[Path] = None,
    **kwargs: Any,
) -> RemoteSource:
    return RemoteSource.open(
        url,
        cache_root=cache_root if cache_root is not None else tmp_path / "cache",
        transport_factory=lambda _location: transport,
        **kwargs,
    )


def _source(
    tmp_path: Path,
    files: Optional[dict[str, bytes]] = None,
    *,
    mtimes: Optional[dict[str, float]] = None,
    directories: Optional[set[str]] = None,
    **kwargs: Any,
) -> tuple[RemoteSource, FakeTransport]:
    if files is None:
        files = {DB_PATH: b"sqlite-placeholder"}
    transport = FakeTransport(files, mtimes=mtimes, directories=directories)
    return _open(tmp_path, transport, **kwargs), transport


def _png_bytes(size: tuple[int, int] = (5, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _wait_for_result(cache: ImageCache, key: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for result_key, image, error in cache.drain():
            if result_key == key:
                return image, error
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {key}")


# --------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "value",
    [
        r"C:\data\db.sqlite3",
        "C:/data/db.sqlite3",
        r"\\server\share\db.sqlite3",
        "data/db.sqlite3",
        "sftp:/host/db.sqlite3",
        "sftp:host/db.sqlite3",
        "",
        None,
    ],
)
def test_non_sftp_targets_are_local(value):
    assert not is_remote_target(value)


def test_parse_uppercase_scheme_is_remote():
    location = parse_sftp_url("SFTP://Host/Data/db.sqlite3")
    assert location.host == "host"
    assert location.path == "/Data/db.sqlite3"


def test_parse_user_port_and_percent_encoding():
    location = parse_sftp_url(
        "sftp://a%20b@example.test:2222/My%20Data/db.sqlite3"
    )
    assert location.username == "a b"
    assert location.host == "example.test"
    assert location.port == 2222
    assert location.path == "/My Data/db.sqlite3"


def test_parse_defaults_user_and_port(monkeypatch):
    monkeypatch.setattr(remote.getpass, "getuser", lambda: "tester")
    location = parse_sftp_url("sftp://example.test/db.sqlite3")
    assert location.username == "tester"
    assert location.port == 22


def test_parse_tilde_path_is_preserved():
    location = parse_sftp_url("sftp://u@example.test/~/data/db.sqlite3")
    assert location.path == "~/data/db.sqlite3"


def test_parse_ipv6_host():
    location = parse_sftp_url("sftp://u@[::1]:2222/db.sqlite3")
    assert location.host == "::1"
    assert location.authority == "u@[::1]:2222"


@pytest.mark.parametrize("value", ["sftp://host", "sftp://host/"])
def test_parse_rejects_missing_or_directory_path(value):
    with pytest.raises(RemoteError, match="database|directory"):
        parse_sftp_url(value)


def test_parse_rejects_password_without_echoing_it():
    with pytest.raises(RemoteError) as info:
        parse_sftp_url("sftp://user:secret@example.test/db.sqlite3")
    assert "secret" not in str(info.value)


@pytest.mark.parametrize(
    "value",
    [
        "sftp://host/db.sqlite3?x=1",
        "sftp://host/db.sqlite3#fragment",
    ],
)
def test_parse_rejects_query_and_fragment(value):
    with pytest.raises(RemoteError, match="query|fragment"):
        parse_sftp_url(value)


@pytest.mark.parametrize(
    "value", ["sftp://host:abc/db.sqlite3", "sftp://host:99999/db.sqlite3"]
)
def test_parse_rejects_invalid_port(value):
    with pytest.raises(RemoteError, match="port"):
        parse_sftp_url(value)


def test_parse_rejects_missing_host():
    with pytest.raises(RemoteError, match="host"):
        parse_sftp_url("sftp:///db.sqlite3")


# ----------------------------------------------------------------------- cache


def test_cache_dir_is_stable_and_windows_safe():
    location = parse_sftp_url("sftp://user@example.test:2222/db.sqlite3")
    root = Path("C:/cache")
    first = remote_cache_dir(
        location, "/data/db.sqlite3", "/data/images", root=root
    )
    second = remote_cache_dir(
        location, "/data/db.sqlite3", "/data/images", root=root
    )
    assert first == second
    assert first.name.startswith("sftp_example.test_2222_")
    assert not any(character in first.name for character in '<>:"/\\|?*')


def test_cache_dir_changes_with_images_root():
    location = parse_sftp_url("sftp://user@example.test/db.sqlite3")
    first = remote_cache_dir(
        location, "/data/db.sqlite3", "/data/images", root=Path("/cache")
    )
    second = remote_cache_dir(
        location, "/data/db.sqlite3", "/data/other_images", root=Path("/cache")
    )
    assert first != second


# ------------------------------------------------------------------ db mirror


def test_open_downloads_database_and_writes_state(tmp_path):
    source, transport = _source(tmp_path)
    try:
        assert source.db_path.read_bytes() == b"sqlite-placeholder"
        assert source.db_path.parent == source.images_dir.parent
        assert transport.get_calls == [DB_PATH]
        state = json.loads(
            (source.db_path.parent / "database.json").read_text(encoding="utf-8")
        )
        assert state["size"] == len(b"sqlite-placeholder")
        assert state["remote_db"] == DB_PATH
        assert state["remote_images"] == IMAGES_PATH
    finally:
        source.close()


def test_open_skips_unchanged_database(tmp_path):
    first, _ = _source(tmp_path)
    first.close()

    second, transport = _source(tmp_path)
    try:
        assert transport.get_calls == []
    finally:
        second.close()


def test_open_refreshes_changed_database(tmp_path):
    first, _ = _source(tmp_path)
    first.close()

    new_content = b"sqlite-placeholder-v2"
    second, transport = _source(
        tmp_path, files={DB_PATH: new_content}, mtimes={DB_PATH: 123.0}
    )
    try:
        assert second.db_path.read_bytes() == new_content
        assert transport.get_calls == [DB_PATH]
    finally:
        second.close()


def test_open_uses_cached_database_when_refresh_fails(tmp_path):
    first, _ = _source(tmp_path)
    first.close()

    messages: list[str] = []
    transport = FakeTransport({DB_PATH: b"changed"}, mtimes={DB_PATH: 999.0})
    transport.get_error = OSError("offline")
    source = _open(tmp_path, transport, log=messages.append)
    try:
        assert source.db_path.read_bytes() == b"sqlite-placeholder"
        assert any("cached" in message for message in messages)
        assert any("offline" in message for message in messages)
        assert not any(
            path.suffix == ".tmp" for path in source.db_path.parent.iterdir()
        )
    finally:
        source.close()


def test_open_fails_without_cache(tmp_path):
    transport = FakeTransport({})
    with pytest.raises(RemoteError, match="not found"):
        _open(tmp_path, transport)
    assert transport.closed


def test_open_failure_leaves_no_partial_database(tmp_path):
    transport = FakeTransport({DB_PATH: b"sqlite"})
    transport.get_error = OSError("offline")
    with pytest.raises(RemoteError, match="offline"):
        _open(tmp_path, transport)

    cache_dirs = list((tmp_path / "cache").iterdir())
    assert len(cache_dirs) == 1
    assert not (cache_dirs[0] / "database.sqlite3").exists()
    assert not any(path.suffix == ".tmp" for path in cache_dirs[0].iterdir())


def test_open_rejects_incomplete_database(tmp_path):
    transport = FakeTransport({DB_PATH: b"sqlite"})
    transport.get_hook = lambda _remote, destination: destination.write_bytes(
        b"sq"
    )
    with pytest.raises(RemoteError, match="incomplete"):
        _open(tmp_path, transport)

    cache_dirs = list((tmp_path / "cache").iterdir())
    assert len(cache_dirs) == 1
    assert not (cache_dirs[0] / "database.sqlite3").exists()


def test_open_rejects_remote_directory(tmp_path):
    transport = FakeTransport({}, directories={DB_PATH})
    with pytest.raises(RemoteError, match="directory"):
        _open(tmp_path, transport)
    assert transport.closed


def test_open_keeps_transport_open_on_success(tmp_path):
    source, transport = _source(tmp_path)
    try:
        assert not transport.closed
    finally:
        source.close()
    assert transport.closed


# ------------------------------------------------------------ images/decoder


def test_ensure_image_defaults_to_db_sibling_images(tmp_path):
    relative = "2026-01-01_00-00-00/Frame-0.png"
    remote_path = posixpath.join(IMAGES_PATH, relative)
    source, transport = _source(
        tmp_path, files={DB_PATH: b"sqlite", remote_path: b"png"}
    )
    transport.get_calls.clear()
    try:
        local = source.ensure_image(relative)
        assert local == source.images_dir.joinpath(
            "2026-01-01_00-00-00", "Frame-0.png"
        )
        assert local.read_bytes() == b"png"
        assert transport.get_calls == [remote_path]
    finally:
        source.close()


def test_ensure_image_fetches_once(tmp_path):
    relative = "session/Frame-0.png"
    remote_path = posixpath.join(IMAGES_PATH, relative)
    source, transport = _source(
        tmp_path, files={DB_PATH: b"sqlite", remote_path: b"png"}
    )
    transport.get_calls.clear()
    try:
        first = source.ensure_image(relative)
        second = source.ensure_image(relative)
        assert first == second
        assert transport.get_calls == [remote_path]
    finally:
        source.close()


def test_ensure_image_uses_images_override(tmp_path):
    relative = "session/Frame-0.png"
    remote_path = posixpath.join("/srv/alt_images", relative)
    source, transport = _source(
        tmp_path,
        files={DB_PATH: b"sqlite", remote_path: b"png"},
        images_dir="/srv/alt_images",
    )
    transport.get_calls.clear()
    try:
        assert source.remote_images_path == "/srv/alt_images"
        source.ensure_image(relative)
        assert transport.get_calls == [remote_path]
    finally:
        source.close()


def test_ensure_image_expands_tilde_images_override(tmp_path):
    relative = "session/Frame-0.png"
    remote_path = posixpath.join("/home/tester/alt_images", relative)
    source, _ = _source(
        tmp_path,
        files={DB_PATH: b"sqlite", remote_path: b"png"},
        images_dir="~/alt_images",
    )
    try:
        assert source.remote_images_path == "/home/tester/alt_images"
    finally:
        source.close()


@pytest.mark.parametrize(
    "value", [r"C:\images", "images", "sftp://host/images", "~user/images"]
)
def test_remote_images_dir_rejects_invalid_values(tmp_path, value):
    transport = FakeTransport({DB_PATH: b"sqlite"})
    with pytest.raises(RemoteError):
        _open(tmp_path, transport, images_dir=value)
    assert transport.closed


@pytest.mark.parametrize("relative", ["../secret", "/etc/passwd", "a\\b"])
def test_ensure_image_rejects_unsafe_paths(tmp_path, relative):
    source, transport = _source(tmp_path)
    transport.get_calls.clear()
    try:
        with pytest.raises(RemoteError):
            source.ensure_image(relative)
        assert transport.get_calls == []
    finally:
        source.close()


def test_decoder_fetches_then_decodes_png(tmp_path):
    relative = "2026-01-01_00-00-00/Frame-0.png"
    remote_path = posixpath.join(IMAGES_PATH, relative)
    source, transport = _source(
        tmp_path, files={DB_PATH: b"sqlite", remote_path: _png_bytes()}
    )
    transport.get_calls.clear()
    decoder = make_remote_decoder(source, source.images_dir)
    try:
        local = source.images_dir.joinpath("2026-01-01_00-00-00", "Frame-0.png")
        image = decoder(str(local))
        assert image.size == (5, 4)
        assert transport.get_calls == [remote_path]
        decoder(str(local))
        assert transport.get_calls == [remote_path]
    finally:
        source.close()


def test_decoder_rejects_paths_outside_the_mirror(tmp_path):
    source, _ = _source(tmp_path)
    decoder = make_remote_decoder(source, source.images_dir)
    try:
        with pytest.raises(RemoteError, match="outside"):
            decoder(str(tmp_path / "elsewhere.png"))
    finally:
        source.close()


def test_image_cache_surfaces_remote_error(tmp_path):
    relative = "session/Frame-0.png"
    source, transport = _source(tmp_path, files={DB_PATH: b"sqlite"})
    transport.get_error = OSError("network down")
    decoder = make_remote_decoder(source, source.images_dir)
    cache = ImageCache(decode=decoder)
    try:
        local = source.images_dir.joinpath("session", "Frame-0.png")
        cache.focus(local, [])
        image, error = _wait_for_result(cache, key_for(local))
        assert image is None
        assert error is not None
        assert "RemoteError" in error
        assert "network down" in error
    finally:
        cache.close()
        source.close()


def test_close_is_idempotent_and_blocks_new_fetches(tmp_path):
    source, _ = _source(tmp_path)
    source.close()
    source.close()
    with pytest.raises(RemoteError, match="closed"):
        source.ensure_image("session/Frame-0.png")


# ------------------------------------------------------------ dependency/wiring


def test_paramiko_missing_raises_dependency_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "paramiko", None)
    location = parse_sftp_url("sftp://user@example.test/db.sqlite3")
    with pytest.raises(RemoteDependencyError, match=r"\[sftp\]"):
        ParamikoTransport.connect(location)


def test_mirrored_database_opens_through_library(tmp_path):
    database = build_db(tmp_path / "dataset", sample_frames())
    transport = FakeTransport({DB_PATH: database.read_bytes()})
    source = _open(tmp_path, transport)
    library = FoggyVisionLibrary(source.db_path, images_dir=source.images_dir)
    try:
        record = library.query().first()
        assert record is not None
        path = library.absolute_path(record)
        assert path.is_relative_to(source.images_dir)
        assert path == source.images_dir / record.path
    finally:
        library.close()
        source.close()
