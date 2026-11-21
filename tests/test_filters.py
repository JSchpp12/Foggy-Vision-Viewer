from __future__ import annotations

import pytest

from foggy_vision_viewer.filters import (
    ALL,
    ViewerFilters,
    ViewerInputError,
    build_selection,
    combobox_values,
    filters_from_values,
    natural_sort,
    parse_optional_float,
)

SESSION_A = "2026-01-01_00-00-00"


def test_parse_optional_float():
    assert parse_optional_float("", "Vis") is None
    assert parse_optional_float("   ", "Vis") is None
    assert parse_optional_float("12.5", "Vis") == 12.5
    assert parse_optional_float("0", "Vis") == 0.0
    for bad in ("abc", "-1", "nan", "inf"):
        with pytest.raises(ViewerInputError):
            parse_optional_float(bad, "Vis")


def test_filters_from_values_defaults():
    filters = filters_from_values()
    assert filters == ViewerFilters()
    assert filters.scene is None
    assert filters.min_meters is None


def test_filters_from_values_all_and_blank():
    filters = filters_from_values(
        scene=ALL, fog_type="", fog_volume=ALL, session="", min_text="", max_text=""
    )
    assert filters == ViewerFilters()


def test_filters_from_values_values():
    filters = filters_from_values(
        scene="terrain_a",
        fog_type="marched",
        fog_volume="vol_b",
        session=SESSION_A,
        min_text="1.5",
        max_text="25",
    )
    assert filters.scene == "terrain_a"
    assert filters.fog_type == "marched"
    assert filters.fog_volume == "vol_b"
    assert filters.session == SESSION_A
    assert filters.min_meters == 1.5
    assert filters.max_meters == 25.0


def test_filters_from_values_rejects_inverted_range():
    with pytest.raises(ViewerInputError, match="must not exceed"):
        filters_from_values(min_text="10", max_text="5")


def test_build_selection_unfiltered(library):
    assert build_selection(library, ViewerFilters()).count() == 4


def test_build_selection_scene(library):
    assert build_selection(library, ViewerFilters(scene="terrain_a")).count() == 4
    assert build_selection(library, ViewerFilters(scene="missing")).count() == 0


def test_build_selection_fog(library):
    assert build_selection(library, ViewerFilters(fog_type="marched")).count() == 4
    assert build_selection(library, ViewerFilters(fog_type="linear")).count() == 0
    assert build_selection(library, ViewerFilters(fog_volume="vol_b")).count() == 1
    assert build_selection(library, ViewerFilters(fog_volume="vol_a")).count() == 3


def test_build_selection_session(library):
    assert build_selection(library, ViewerFilters(session=SESSION_A)).count() == 2
    assert build_selection(library, ViewerFilters(session="nope")).count() == 0


def test_build_selection_visibility(library):
    assert build_selection(library, ViewerFilters(min_meters=1500)).count() == 2
    assert build_selection(library, ViewerFilters(max_meters=1000)).count() == 2
    assert (
        build_selection(
            library, ViewerFilters(min_meters=1000, max_meters=2000)
        ).count()
        == 2
    )
    assert build_selection(library, ViewerFilters(max_meters=499)).count() == 0


def test_build_selection_composition(library):
    filters = ViewerFilters(scene="terrain_a", fog_volume="vol_b", min_meters=2000)
    assert build_selection(library, filters).count() == 1


def test_natural_sort_orders_frame_numbers(library):
    records = natural_sort(library.query().records())
    paths = [record.path for record in records]
    assert paths[0].endswith("Frame-2.png")
    assert paths[1].endswith("Frame-10.png")
    assert [record.visibility_value for record in records] == pytest.approx(
        [1000.0, 2000.0, 500.0, 3000.0]
    )


def test_combobox_values(library):
    values = combobox_values(library)
    assert values["scene"] == [ALL, "terrain_a"]
    assert values["fog_type"] == [ALL, "marched"]
    assert values["fog_volume"] == [ALL, "vol_a", "vol_b"]
    assert values["session"] == [
        ALL,
        "2026-01-01_00-00-00",
        "2026-01-02_00-00-00",
        "2026-01-03_00-00-00",
    ]
