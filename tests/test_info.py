from __future__ import annotations

from datetime import datetime, timezone

from foggy_vision_loader import ImageRecord

from foggy_vision_viewer.info import (
    MEDIA_ROLES,
    format_detail,
    format_path,
    format_title,
    media_absolute_path,
    media_relative_path,
    vector_text,
)

FRAME_A = "2026-01-01_00-00-00/Frame-2.png"
FRAME_NO_MASK = "2026-01-02_00-00-00/Frame-0.png"


def _record(library, path):
    return library.query().where("path = ?", (path,)).first()


def test_media_roles_are_ordered():
    assert list(MEDIA_ROLES) == [
        "color",
        "ray_distance",
        "ray_normalized_distance",
        "ray_validity",
    ]


def test_media_relative_paths(library):
    record = _record(library, FRAME_A)
    assert media_relative_path(record, "color") == FRAME_A
    assert (
        media_relative_path(record, "ray_distance")
        == "2026-01-01_00-00-00/Frame-2_distMask.tif"
    )
    assert media_relative_path(record, "unknown") is None


def test_media_absolute_paths(library):
    record = _record(library, FRAME_A)
    assert media_absolute_path(library, record, "color").exists()
    assert media_absolute_path(library, record, "ray_distance").exists()
    no_mask = _record(library, FRAME_NO_MASK)
    assert media_absolute_path(library, no_mask, "ray_distance") is None


def test_vector_text():
    assert vector_text('{"x": 1, "y": 2.5, "z": 3}') == "(1.00, 2.50, 3.00)"
    assert vector_text("not json") == "not json"
    assert vector_text(None) == "—"


def test_format_title(library):
    record = _record(library, FRAME_A)
    assert format_title(record, 0, 4) == "terrain_a · real · fog marched · 1 / 4"


def test_format_detail(library):
    text = format_detail(_record(library, FRAME_A))
    assert "Session:    2026-01-01_00-00-00  (2026-01-01 00:00:00 UTC)" in text
    assert "Image:      64 x 48 · color" in text
    assert "Scene:      terrain_a  (real, coverage 18.00 mi)" in text
    assert "Camera:     id 1 · pos (0.00, 15.00, 0.00)" in text
    assert "Fog:        marched · volume vol_a" in text
    assert "Visibility: 1,000.00 m (0.62 mi)  [ray_including_invalid]" in text
    assert "simple:    —" in text
    assert "including: avg 1,000.00 · med 1,100.00 · min 800.00 · rays 900" in text
    assert "excluding: avg — · med — · min — · rays 0" in text
    assert "Masks:      Distance (m), Normalized distance, Validity" in text


def test_format_detail_simple_and_no_masks(library):
    text = format_detail(_record(library, FRAME_NO_MASK))
    assert "Visibility: 500.00 m (0.31 mi)  [simple]" in text
    assert "simple:    500.00" in text
    assert "Masks:      none" in text


def test_format_detail_time_unknown():
    record = ImageRecord(
        path="session/Frame-0.png",
        format="color",
        width=10,
        height=10,
        session=None,
        captured_at=None,
    )
    text = format_detail(record)
    assert "time unknown" in text
    assert "Session:    —" in text
    assert "Visibility: —" in text
    assert "Masks:      none" in text


def test_format_path(library):
    record = _record(library, FRAME_A)
    assert format_path(record, "color") == f"{FRAME_A}  ·  Color  ·  64x48"
    no_mask = _record(library, FRAME_NO_MASK)
    assert format_path(no_mask, "ray_distance") == "Distance (m) unavailable"
