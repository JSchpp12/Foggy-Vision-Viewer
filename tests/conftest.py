from __future__ import annotations

import pytest

from foggy_vision_loader import FoggyVisionLibrary

from .helpers import build_db, sample_frames


@pytest.fixture
def sample_db(tmp_path):
    return build_db(tmp_path / "db", sample_frames())


@pytest.fixture
def library(sample_db):
    lib = FoggyVisionLibrary(sample_db)
    yield lib
    lib.close()
