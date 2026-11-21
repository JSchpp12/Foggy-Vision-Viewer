"""Human-readable formatting and media-role helpers (no Tk imports)."""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import Optional

from foggy_vision_loader import ImageRecord

#: Media role key -> viewer label, in selector order.
MEDIA_ROLES = {
    "color": "Color",
    "ray_distance": "Distance (m)",
    "ray_normalized_distance": "Normalized distance",
    "ray_validity": "Validity",
}

ROLE_ORDER = tuple(MEDIA_ROLES)

_DASH = "—"


def media_relative_path(record: ImageRecord, role: str) -> Optional[str]:
    """Relative path for a media role, or ``None`` when it is unavailable."""
    if role == "color":
        return record.path
    return record.mask_paths.get(role)


def media_absolute_path(lib, record: ImageRecord, role: str) -> Optional[Path]:
    relative = media_relative_path(record, role)
    return None if relative is None else lib.absolute_path(relative)


def vector_text(raw_json: Optional[str]) -> str:
    """Format a JSON ``{x, y, z}`` string as ``(x, y, z)``."""
    if not raw_json:
        return _DASH
    try:
        data = json.loads(raw_json)
        return (
            f"({float(data['x']):.2f}, "
            f"{float(data['y']):.2f}, "
            f"{float(data['z']):.2f})"
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return str(raw_json)


def _number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return _DASH
    return f"{value:,.{digits}f}"


def _count(value: Optional[int]) -> str:
    if value is None:
        return _DASH
    return f"{int(value):,}"


def _utc_text(record: ImageRecord) -> str:
    moment = record.captured_at
    if moment is None:
        return "time unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_title(record: ImageRecord, index: int, total: int) -> str:
    parts = [
        record.scene_name or "Scene ?",
        record.terrain_rendering_type or "?",
    ]
    if record.fog_type:
        parts.append(f"fog {record.fog_type}")
    parts.append(f"{index + 1:,} / {total:,}")
    return " · ".join(parts)


def format_detail(record: ImageRecord) -> str:
    scene = record.scene_name or _DASH
    if record.terrain_rendering_type or record.coverage_distance_miles is not None:
        scene += (
            f"  ({record.terrain_rendering_type or '?'}"
            f", coverage {_number(record.coverage_distance_miles)} mi)"
        )

    camera_parts = []
    if record.camera_id is not None:
        camera_parts.append(f"id {record.camera_id}")
    if record.camera_position:
        camera_parts.append(f"pos {vector_text(record.camera_position)}")
    if record.camera_look_dir:
        camera_parts.append(f"look {vector_text(record.camera_look_dir)}")
    camera = " · ".join(camera_parts) if camera_parts else _DASH

    fog_parts = []
    if record.fog_type:
        fog_parts.append(record.fog_type)
    if record.fog_volume_name:
        fog_parts.append(f"volume {record.fog_volume_name}")
    if record.exp_fog_density is not None:
        fog_parts.append(f"density {_number(record.exp_fog_density, 3)}")
    if record.marched_density_multiplier is not None:
        fog_parts.append(
            f"multiplier {_number(record.marched_density_multiplier, 3)}"
        )
    fog = " · ".join(fog_parts) if fog_parts else _DASH

    if record.visibility_value is None:
        visibility = _DASH
    else:
        visibility = (
            f"{_number(record.visibility_value)} m "
            f"({_number(record.visibility_miles)} mi)"
        )
        if record.visibility_metric:
            visibility += f"  [{record.visibility_metric}]"

    masks = [
        MEDIA_ROLES[role]
        for role in ROLE_ORDER
        if role != "color" and role in record.mask_paths
    ]

    lines = [
        f"Session:    {record.session or _DASH}  ({_utc_text(record)})",
        f"Image:      {record.width or '?'} x {record.height or '?'}"
        f" · {record.format}",
        f"Scene:      {scene}",
        f"Camera:     {camera}",
        f"Fog:        {fog}",
        f"Visibility: {visibility}",
        f"  simple:    {_number(record.simple_value)}",
        "  including: "
        f"avg {_number(record.ray_including_average)} · "
        f"med {_number(record.ray_including_median)} · "
        f"min {_number(record.ray_including_minimum)} · "
        f"rays {_count(record.ray_including_ray_count)}",
        "  excluding: "
        f"avg {_number(record.ray_excluding_average)} · "
        f"med {_number(record.ray_excluding_median)} · "
        f"min {_number(record.ray_excluding_minimum)} · "
        f"rays {_count(record.ray_excluding_ray_count)}",
        f"Masks:      {', '.join(masks) if masks else 'none'}",
    ]
    return "\n".join(lines)


def format_path(record: ImageRecord, role: str) -> str:
    relative = media_relative_path(record, role)
    label = MEDIA_ROLES.get(role, role)
    if relative is None:
        return f"{label} unavailable"
    dimensions = ""
    if record.width and record.height:
        dimensions = f"{record.width}x{record.height}"
    return "  ·  ".join(part for part in (relative, label, dimensions) if part)
