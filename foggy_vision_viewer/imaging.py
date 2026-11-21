"""Decode and prepare images for display in Tk.

TIFF masks are read with ``tifffile`` rather than Pillow: Pillow's LZW TIFF
decoder can hard-crash on the normalized-distance masks used by FoggyVision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from PIL import Image

_TIFF_SUFFIXES = {".tif", ".tiff"}


def decode_for_display(path: Any) -> Image.Image:
    """Decode a file into a Tk-displayable image without touching Tk."""
    if Path(path).suffix.lower() in _TIFF_SUFFIXES:
        return prepare_array(tifffile.imread(str(path)))
    with Image.open(path) as source:
        source.load()
        return prepare_image(source.copy())


def prepare_image(image: Image.Image) -> Image.Image:
    """Return a Pillow image suitable for ``ImageTk.PhotoImage``."""
    if image.mode == "F":
        return _float_array_to_l(np.asarray(image))
    if image.mode in ("RGBA", "LA", "P"):
        return image.convert("RGB")
    return image


def prepare_array(array: np.ndarray) -> Image.Image:
    """Convert an array (from tifffile) into a display-ready Pillow image."""
    data = np.asarray(array)
    if data.ndim == 3:
        if data.shape[2] == 1:
            data = data[..., 0]
        elif data.shape[2] in (3, 4):
            return Image.fromarray(_to_uint8(data[..., :3]), mode="RGB")
        else:
            raise ValueError(f"unsupported TIFF channel count: {data.shape[2]}")
    if data.ndim != 2:
        raise ValueError(f"unsupported TIFF shape: {data.shape}")

    if data.dtype.kind == "f":
        return _float_array_to_l(data)
    if data.dtype == np.uint8:
        return Image.fromarray(data, mode="L")
    if data.dtype == np.bool_:
        return Image.fromarray((data.astype(np.uint8) * 255), mode="L")
    return _integer_array_to_l(data)


def _to_uint8(data: np.ndarray) -> np.ndarray:
    if data.dtype == np.uint8:
        return data
    return _scale_to_uint8(data.astype(np.float64))


def _scale_to_uint8(data: np.ndarray) -> np.ndarray:
    finite = np.isfinite(data)
    if not finite.any():
        return np.zeros(data.shape, dtype=np.uint8)
    values = data[finite]
    low = float(values.min())
    high = float(values.max())
    if high <= low:
        return np.zeros(data.shape, dtype=np.uint8)
    scaled = (data - low) * (255.0 / (high - low))
    scaled = np.where(finite, scaled, 0.0)
    return np.clip(np.rint(scaled), 0, 255).astype(np.uint8)


def _float_array_to_l(array: np.ndarray) -> Image.Image:
    return Image.fromarray(_scale_to_uint8(np.asarray(array, dtype=np.float64)))


def _integer_array_to_l(array: np.ndarray) -> Image.Image:
    return Image.fromarray(_scale_to_uint8(array.astype(np.float64)))
