"""Threaded, LRU image cache with navigation-aware prefetch.

Adapted from ``AirView_Viewer/airview_viewer/image_cache.py`` (same design):
one daemon worker thread decodes images and the UI polls for completions.  Tk
objects are never created here; callers build ``ImageTk.PhotoImage`` on the
main thread.
"""

from __future__ import annotations

import os
import queue
import threading
from collections import OrderedDict
from typing import Callable, Optional

from PIL import Image

from .imaging import decode_for_display

Decoder = Callable[[str], Image.Image]


def key_for(path) -> str:
    return os.fspath(path)


class ImageCache:
    """Bounded LRU of decoded images with a single background worker."""

    def __init__(
        self,
        max_entries: int = 10,
        *,
        decode: Optional[Decoder] = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self.max_entries = max_entries
        self._decode: Decoder = decode or decode_for_display

        self._cond = threading.Condition()
        self._lru: "OrderedDict[str, Image.Image]" = OrderedDict()
        self._pending: set[str] = set()
        self._failed: dict[str, str] = {}
        self._desired: tuple[str, ...] = ()
        self._closed = False
        self._results: "queue.Queue[tuple[str, Optional[Image.Image], Optional[str]]]" = (
            queue.Queue()
        )

        self._thread = threading.Thread(
            target=self._worker, name="foggy-vision-image-cache", daemon=True
        )
        self._thread.start()

    def focus(self, path, neighbors) -> str:
        """Replace the prefetch priority list: current path, then neighbors."""
        key = key_for(path)
        keys = [key]
        for neighbor in neighbors:
            neighbor_key = key_for(neighbor)
            if neighbor_key not in keys:
                keys.append(neighbor_key)
        with self._cond:
            self._desired = tuple(keys)
            self._cond.notify_all()
        return key

    def lookup(self, path) -> Optional[Image.Image]:
        """Return a cached image without blocking, updating LRU recency."""
        key = key_for(path)
        with self._cond:
            image = self._lru.get(key)
            if image is not None:
                self._lru.move_to_end(key)
            return image

    def drain(self):
        """Non-blocking list of ``(key, image, error)`` completions."""
        completed = []
        while True:
            try:
                completed.append(self._results.get_nowait())
            except queue.Empty:
                return completed

    def clear_failures(self) -> None:
        with self._cond:
            self._failed.clear()
            self._cond.notify_all()

    def reset(self) -> None:
        with self._cond:
            self._lru.clear()
            self._failed.clear()
            self._desired = ()
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._thread.join(timeout=2.0)

    def _next_task(self) -> Optional[str]:
        for key in self._desired:
            if (
                key not in self._lru
                and key not in self._pending
                and key not in self._failed
            ):
                return key
        return None

    def _worker(self) -> None:
        while True:
            with self._cond:
                task: Optional[str] = None
                while not self._closed:
                    task = self._next_task()
                    if task is not None:
                        self._pending.add(task)
                        break
                    self._cond.wait()
                if task is None:
                    return

            image = None
            error = None
            try:
                image = self._decode(task)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            with self._cond:
                self._pending.discard(task)
                if image is not None:
                    self._lru[task] = image
                    self._lru.move_to_end(task)
                    while len(self._lru) > self.max_entries:
                        self._lru.popitem(last=False)
                else:
                    self._failed[task] = error or "could not decode"
                    if len(self._failed) > 4096:
                        del self._failed[next(iter(self._failed))]

            self._results.put((task, image, error))
