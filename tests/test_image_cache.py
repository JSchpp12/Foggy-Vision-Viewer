from __future__ import annotations

import time

import pytest
from PIL import Image

from foggy_vision_viewer.image_cache import ImageCache, key_for


def _png(path, color=(255, 0, 0), size=(4, 3)):
    Image.new("RGB", size, color).save(path)
    return path


def _wait_for_image(cache, path, timeout=2.0):
    key = key_for(path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for result_key, image, _error in cache.drain():
            if result_key == key and image is not None:
                return image
        image = cache.lookup(path)
        if image is not None:
            return image
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def _wait_for_result(cache, key, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for result_key, image, error in cache.drain():
            if result_key == key:
                return image, error
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {key}")


def test_focus_lookup_and_prefetch(tmp_path):
    first = _png(tmp_path / "first.png", (255, 0, 0))
    second = _png(tmp_path / "second.png", (0, 255, 0), (3, 4))
    cache = ImageCache()
    try:
        key = cache.focus(first, [second])
        assert key == key_for(first)
        image = _wait_for_image(cache, first)
        assert image.size == (4, 3)
        prefetched = _wait_for_image(cache, second)
        assert prefetched.size == (3, 4)
    finally:
        cache.close()


def test_decode_callback_is_applied(tmp_path):
    path = _png(tmp_path / "image.png")
    cache = ImageCache(decode=lambda source: Image.open(source).convert("L"))
    try:
        cache.focus(path, [])
        image = _wait_for_image(cache, path)
        assert image.mode == "L"
    finally:
        cache.close()


def test_lru_eviction(tmp_path):
    first = _png(tmp_path / "first.png")
    second = _png(tmp_path / "second.png", (0, 0, 255))
    cache = ImageCache(max_entries=1)
    try:
        cache.focus(first, [])
        _wait_for_image(cache, first)
        cache.focus(second, [])
        _wait_for_image(cache, second)
        assert cache.lookup(first) is None
    finally:
        cache.close()


def test_missing_file_reports_error(tmp_path):
    missing = tmp_path / "missing.png"
    cache = ImageCache()
    try:
        cache.focus(missing, [])
        image, error = _wait_for_result(cache, key_for(missing))
        assert image is None
        assert error is not None
        assert "FileNotFoundError" in error
    finally:
        cache.close()


def test_invalid_max_entries():
    with pytest.raises(ValueError):
        ImageCache(max_entries=0)


def test_close_is_idempotent(tmp_path):
    cache = ImageCache()
    cache.close()
    cache.close()
