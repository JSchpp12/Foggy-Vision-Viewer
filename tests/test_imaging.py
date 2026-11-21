from __future__ import annotations

import numpy as np
import pytest
import tifffile
from PIL import Image

from foggy_vision_viewer.imaging import (
    decode_for_display,
    prepare_array,
    prepare_image,
)


def _float_image(values) -> Image.Image:
    return Image.fromarray(np.asarray(values, dtype=np.float32))


def test_float_mask_normalized():
    image = _float_image([[0.0, 5.0], [10.0, float("nan")]])
    prepared = prepare_image(image)
    assert prepared.mode == "L"
    assert np.asarray(prepared).tolist() == [[0, 128], [255, 0]]


def test_float_mask_ignores_non_finite():
    image = _float_image([[0.0, float("inf")], [10.0, float("-inf")]])
    prepared = prepare_image(image)
    assert prepared.mode == "L"
    assert np.asarray(prepared).tolist() == [[0, 0], [255, 0]]


def test_constant_float_mask_is_black():
    prepared = prepare_image(_float_image([[3.0, 3.0], [3.0, 3.0]]))
    assert prepared.mode == "L"
    assert np.asarray(prepared).tolist() == [[0, 0], [0, 0]]


def test_all_nan_float_mask_is_black():
    prepared = prepare_image(
        _float_image([[float("nan"), float("nan")], [float("nan"), float("nan")]])
    )
    assert prepared.mode == "L"
    assert np.asarray(prepared).tolist() == [[0, 0], [0, 0]]


def test_rgba_converted_to_rgb():
    image = Image.new("RGBA", (2, 2), (10, 20, 30, 128))
    prepared = prepare_image(image)
    assert prepared.mode == "RGB"
    assert prepared.getpixel((0, 0)) == (10, 20, 30)


def test_grayscale_passthrough():
    image = Image.new("L", (2, 2), 7)
    prepared = prepare_image(image)
    assert prepared is image
    assert prepared.mode == "L"


def test_prepare_array_rgb():
    array = np.zeros((2, 3, 3), dtype=np.uint8)
    array[..., 0] = 255
    prepared = prepare_array(array)
    assert prepared.mode == "RGB"
    assert prepared.size == (3, 2)
    assert prepared.getpixel((0, 0)) == (255, 0, 0)


def test_prepare_array_uint16_normalized():
    array = np.array([[0, 1000], [2000, 4000]], dtype=np.uint16)
    prepared = prepare_array(array)
    assert prepared.mode == "L"
    assert np.asarray(prepared).tolist() == [[0, 64], [128, 255]]


def test_prepare_array_rejects_unsupported_shape():
    with pytest.raises(ValueError):
        prepare_array(np.zeros((2, 2, 2), dtype=np.uint8))


def test_decode_float_tiff(tmp_path):
    path = tmp_path / "distance.tif"
    tifffile.imwrite(
        path, np.array([[0.0, 5.0], [10.0, float("nan")]], dtype=np.float32)
    )
    prepared = decode_for_display(path)
    assert prepared.mode == "L"
    assert np.asarray(prepared).tolist() == [[0, 128], [255, 0]]


def test_decode_lzw_tiff(tmp_path):
    """Regression: Pillow's LZW decoder crashes; tifffile must be used."""
    path = tmp_path / "normalized.tif"
    array = np.arange(16, dtype=np.uint8).reshape(4, 4)
    tifffile.imwrite(path, array, compression="lzw")
    prepared = decode_for_display(path)
    assert prepared.mode == "L"
    assert np.array_equal(np.asarray(prepared), array)


def test_decode_rgba_png(tmp_path):
    path = tmp_path / "color.png"
    Image.new("RGBA", (2, 2), (1, 2, 3, 128)).save(path)
    prepared = decode_for_display(path)
    assert prepared.mode == "RGB"
    assert prepared.getpixel((0, 0)) == (1, 2, 3)
