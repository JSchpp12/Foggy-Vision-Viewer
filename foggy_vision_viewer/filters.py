"""Filter parsing and query construction for the viewer (no Tk imports)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from foggy_vision_loader import ImageRecord, Selection

ALL = "All"

_FRAME_PATTERN = re.compile(r"Frame-(\d+)")


class ViewerInputError(ValueError):
    """Raised for user-facing filter validation errors."""


@dataclass(frozen=True, slots=True)
class ViewerFilters:
    """Validated filter values read from the viewer's widgets."""

    scene: Optional[str] = None
    fog_type: Optional[str] = None
    fog_volume: Optional[str] = None
    skybox: Optional[str] = None
    session: Optional[str] = None
    min_meters: Optional[float] = None
    max_meters: Optional[float] = None


def parse_optional_float(text: str, label: str) -> Optional[float]:
    """Parse a blank-or-number entry, rejecting non-finite/negative values."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ViewerInputError(f"{label} must be a number") from exc
    if not math.isfinite(value):
        raise ViewerInputError(f"{label} must be a finite number")
    if value < 0:
        raise ViewerInputError(f"{label} must be zero or greater")
    return value


def _combobox_value(text: str) -> Optional[str]:
    cleaned = (text or "").strip()
    if not cleaned or cleaned == ALL:
        return None
    return cleaned


def filters_from_values(
    *,
    scene: str = "",
    fog_type: str = "",
    fog_volume: str = "",
    skybox: str = "",
    session: str = "",
    min_text: str = "",
    max_text: str = "",
) -> ViewerFilters:
    """Validate raw widget values into a :class:`ViewerFilters`."""
    minimum = parse_optional_float(min_text, "Minimum visibility")
    maximum = parse_optional_float(max_text, "Maximum visibility")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ViewerInputError("Min visibility must not exceed max visibility")
    return ViewerFilters(
        scene=_combobox_value(scene),
        fog_type=_combobox_value(fog_type),
        fog_volume=_combobox_value(fog_volume),
        skybox=_combobox_value(skybox),
        session=_combobox_value(session),
        min_meters=minimum,
        max_meters=maximum,
    )


def build_selection(lib, filters: ViewerFilters) -> "Selection":
    """Compose the loader selection for the given filters."""
    query = lib.query()
    if filters.scene is not None:
        query = query.scene_name(filters.scene, exact=True)
    if filters.fog_type is not None:
        query = query.fog_type(filters.fog_type)
    if filters.fog_volume is not None:
        query = query.fog_volume(filters.fog_volume)
    if filters.skybox is not None:
        query = query.skybox(filters.skybox)
    if filters.session is not None:
        query = query.sessions(filters.session)
    if filters.min_meters is not None or filters.max_meters is not None:
        query = query.visibility(
            min_meters=filters.min_meters, max_meters=filters.max_meters
        )
    return query.order_by("path")


def natural_sort(records: list["ImageRecord"]) -> list["ImageRecord"]:
    """Sort by session then numeric frame number (Frame-2 before Frame-10)."""
    def key(record: "ImageRecord"):
        match = _FRAME_PATTERN.search(record.path)
        frame = int(match.group(1)) if match else -1
        return (record.session or "", frame, record.path)

    return sorted(records, key=key)


def combobox_values(lib) -> dict[str, list[str]]:
    """Values for the viewer's filter comboboxes, with ``All`` first."""
    volumes = sorted(
        key for key in lib.query().counts_by("fog_volume") if key is not None
    )
    return {
        "scene": [ALL, *lib.scenes()],
        "fog_type": [ALL, *lib.fog_types()],
        "fog_volume": [ALL, *volumes],
        "skybox": [ALL, *lib.skyboxes()],
        "session": [ALL, *lib.sessions()],
    }
