"""Fixture database builder for viewer tests.

Creates only the tables the loader validates, so the viewer suite does not
depend on FoggyVision or its ML dependencies.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE scene(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    coverageDistanceMiles REAL NOT NULL,
    upperRightPositionID INTEGER NOT NULL,
    lowerLeftPositionID INTEGER NOT NULL,
    centerPositionID INTEGER NOT NULL,
    terrainRenderingType TEXT NOT NULL,
    terrainShapeCenter TEXT
);
CREATE TABLE camera(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    virtualPosition TEXT NOT NULL,
    sceneID INTEGER NOT NULL,
    lookDir TEXT NOT NULL,
    fov REAL,
    nearClip REAL,
    farClip REAL
);
CREATE TABLE fog_type(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE fog(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    typeID INTEGER NOT NULL,
    sceneID INTEGER NOT NULL,
    expFogDensity REAL,
    marchedDefaultDensity REAL,
    marchedDensityMultiplier REAL,
    volumeName TEXT
);
CREATE TABLE environment(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fogID INTEGER NOT NULL
);
CREATE TABLE image(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fileName TEXT NOT NULL UNIQUE,
    fileType TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    checksum TEXT
);
CREATE TABLE view(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    colorImageID INTEGER NOT NULL,
    cameraID INTEGER NOT NULL,
    sceneID INTEGER NOT NULL,
    environmentID INTEGER NOT NULL
);
CREATE TABLE view_image(
    viewID INTEGER NOT NULL,
    imageID INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (viewID, role)
);
CREATE TABLE visibility_distance(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewID INTEGER NOT NULL,
    distanceType TEXT NOT NULL,
    value REAL,
    average REAL,
    median REAL,
    minimum REAL,
    rayCount INTEGER,
    UNIQUE(viewID, distanceType)
);
"""

MASK_FILES = {
    "ray_distance": "Frame-{n}_distMask.tif",
    "ray_normalized_distance": "Frame-{n}_distNormSmlMask.tif",
    "ray_validity": "Frame-{n}_validMask.png",
}


@dataclass
class Frame:
    session: str
    frame: int
    scene: str = "terrain_a"
    terrain: str = "real"
    coverage: float = 18.0
    fog_type: str = "marched"
    fog_volume: str = "vol_a"
    density: float = 0.6
    multiplier: float = 0.1
    width: int = 64
    height: int = 48
    masks: bool = True
    metrics: dict = field(default_factory=dict)


EMPTY_RAY = {"average": None, "median": None, "minimum": None, "rayCount": 0}


def sample_frames() -> list[Frame]:
    return [
        Frame(
            session="2026-01-01_00-00-00",
            frame=2,
            metrics={
                "ray_including_invalid": {
                    "average": 1000.0,
                    "median": 1100.0,
                    "minimum": 800.0,
                    "rayCount": 900,
                },
                "ray_excluding_invalid": dict(EMPTY_RAY),
            },
        ),
        Frame(
            session="2026-01-01_00-00-00",
            frame=10,
            metrics={
                "ray_including_invalid": {
                    "average": 2000.0,
                    "median": 2100.0,
                    "minimum": 1800.0,
                    "rayCount": 900,
                },
                "ray_excluding_invalid": dict(EMPTY_RAY),
            },
        ),
        Frame(
            session="2026-01-02_00-00-00",
            frame=0,
            masks=False,
            metrics={"simple": {"value": 500.0}},
        ),
        Frame(
            session="2026-01-03_00-00-00",
            frame=1,
            fog_volume="vol_b",
            metrics={
                "ray_including_invalid": dict(EMPTY_RAY),
                "ray_excluding_invalid": {
                    "average": 3000.0,
                    "median": 3100.0,
                    "minimum": 2500.0,
                    "rayCount": 800,
                },
            },
        ),
    ]


def build_db(db_dir: Path, frames: list[Frame]) -> Path:
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    database = db_dir / "database.sqlite3"
    if database.exists():
        database.unlink()

    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA_SQL)
        cursor = connection.cursor()

        cursor.execute(
            """INSERT INTO scene (
                   name, coverageDistanceMiles, upperRightPositionID,
                   lowerLeftPositionID, centerPositionID,
                   terrainRenderingType, terrainShapeCenter
               ) VALUES (?, ?, 0, 0, 0, ?, ?)""",
            (frames[0].scene, frames[0].coverage, frames[0].terrain, "{}"),
        )
        scene_id = cursor.lastrowid

        cursor.execute(
            """INSERT INTO camera (
                   virtualPosition, sceneID, lookDir, fov, nearClip, farClip
               ) VALUES (?, ?, ?, 60.0, 0.1, 20000.0)""",
            ('{"x": 0.0, "y": 15.0, "z": 0.0}', scene_id, '{"x": 0.0, "y": 0.0, "z": 1.0}'),
        )
        camera_id = cursor.lastrowid

        cursor.execute("INSERT INTO fog_type (name) VALUES (?)", (frames[0].fog_type,))
        fog_type_id = cursor.lastrowid

        fog_ids: dict[str, int] = {}
        environment_ids: dict[str, int] = {}
        for frame in frames:
            if frame.fog_volume in fog_ids:
                continue
            cursor.execute(
                """INSERT INTO fog (
                       typeID, sceneID, expFogDensity, marchedDefaultDensity,
                       marchedDensityMultiplier, volumeName
                   ) VALUES (?, ?, ?, 0.0, ?, ?)""",
                (
                    fog_type_id,
                    scene_id,
                    frame.density,
                    frame.multiplier,
                    frame.fog_volume,
                ),
            )
            fog_id = cursor.lastrowid
            fog_ids[frame.fog_volume] = fog_id
            cursor.execute("INSERT INTO environment (fogID) VALUES (?)", (fog_id,))
            environment_ids[frame.fog_volume] = cursor.lastrowid

        for frame in frames:
            images_dir = db_dir / "images" / frame.session
            images_dir.mkdir(parents=True, exist_ok=True)
            color_path = f"{frame.session}/Frame-{frame.frame}.png"
            cursor.execute(
                """INSERT INTO image (fileName, fileType, width, height)
                   VALUES (?, 'color', ?, ?)""",
                (color_path, frame.width, frame.height),
            )
            color_id = cursor.lastrowid
            (db_dir / "images" / color_path).write_bytes(b"")

            cursor.execute(
                """INSERT INTO view (colorImageID, cameraID, sceneID, environmentID)
                   VALUES (?, ?, ?, ?)""",
                (color_id, camera_id, scene_id, environment_ids[frame.fog_volume]),
            )
            view_id = cursor.lastrowid

            if frame.masks:
                for role, template in MASK_FILES.items():
                    mask_path = f"{frame.session}/{template.format(n=frame.frame)}"
                    cursor.execute(
                        """INSERT INTO image (fileName, fileType, width, height)
                           VALUES (?, ?, ?, ?)""",
                        (mask_path, role, frame.width, frame.height),
                    )
                    cursor.execute(
                        "INSERT INTO view_image (viewID, imageID, role) VALUES (?, ?, ?)",
                        (view_id, cursor.lastrowid, role),
                    )
                    (db_dir / "images" / mask_path).write_bytes(b"")

            for distance_type, values in frame.metrics.items():
                cursor.execute(
                    """INSERT INTO visibility_distance (
                           viewID, distanceType, value, average, median, minimum,
                           rayCount
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        view_id,
                        distance_type,
                        values.get("value"),
                        values.get("average"),
                        values.get("median"),
                        values.get("minimum"),
                        values.get("rayCount"),
                    ),
                )

    return database
