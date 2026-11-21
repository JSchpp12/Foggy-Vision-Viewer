# Foggy Vision Viewer

Desktop browser for FoggyVision synthetic fog datasets. It reads a database
built by the FoggyVision project's `fogvis-cli import` command and shows the
color frame plus all three ray masks, with filters and renderer metadata.

The viewer is read-only: it never scans, imports, or modifies a dataset.

## Install

The query layer lives in the sibling `Foggy_Vision_Loader` project and must be
installed first:

```powershell
python -m pip install -e F:\repos\Foggy_Vision_Loader
python -m pip install -e F:\repos\Foggy_Vision_Viewer
```

Python 3.10+, Pillow, numpy, tifffile, and imagecodecs are required. TIFF
masks are read with tifffile because Pillow's LZW TIFF decoder is unreliable
on the normalized-distance masks.

## Build a dataset

Use FoggyVision's importer (from its own checkout):

```powershell
python -m fogvis.cli import --database media\db --images media\test
```

A database directory contains `database.sqlite3` and an `images\` tree.

## Run

```powershell
foggy-vision-viewer --db F:\data\foggyvision\database.sqlite3
```

or:

```powershell
python -m foggy_vision_viewer --db F:\data\foggyvision\database.sqlite3
```

`--db` requires the SQLite database file (a directory is rejected). With no
argument, the viewer reads `FOGGY_VISION_DATABASE`, which must also point at
the `.sqlite3` file. The image tree defaults to the sibling `images\`
directory; use `--images-dir DIR` if it lives elsewhere.

## Filters

- **Scene** — exact terrain/scene name from the database.
- **Fog type** — e.g. `marched`.
- **Volume** — fog volume name.
- **Session** — the `YYYY-MM-DD_HH-MM-SS` import session.
- **Visibility (m)** — inclusive min/max on the resolved visibility value.

`All` (or a blank visibility field) disables that filter. Filters use native
meters; the info panel also shows the mile equivalent.

## Media roles

The **View** selector switches between:

| Role | File |
|---|---|
| Color | `Frame-N.png` |
| Distance (m) | `Frame-N_distMask.tif` (32-bit float, auto-normalized for display) |
| Normalized distance | `Frame-N_distNormSmlMask.tif` |
| Validity | `Frame-N_validMask.png` |

If a frame has no such mask, the canvas shows a message and navigation
continues.

## Controls

| Input | Action |
|---|---|
| `Left` / `Right` | Previous / next image |
| `Home` / `End` | First / last image |
| `1` – `4` | Color / Distance / Normalized distance / Validity |
| `Enter` in a filter field | Apply the query |
| `Apply` button | Run the query and reset to the first result |

Arrow keys are ignored while a filter widget or dropdown has focus.

## Info panel

The panel shows the relative media path and role, session folder and UTC
capture time, image dimensions, scene/terrain and coverage, camera position and
look direction, fog type/volume/density/multiplier, the resolved visibility in
meters and miles with the metric used, all three metrics' raw
average/median/min/ray-count values, and which masks exist for the frame.

## Notes

- Visibility uses the loader's auto-fallback: `simple`, else
  `ray_including_invalid`, else `ray_excluding_invalid`.
- Distance-mask TIFFs are normalized per image to 8-bit grayscale for display;
  the original files are never modified. Both TIFF roles are decoded with
  tifffile (which handles the LZW-compressed normalized mask).
- The viewer queries SQLite on the UI thread and decodes images on a
  background thread with a bounded LRU cache and neighbor prefetch.
