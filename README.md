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

`--db` accepts the SQLite database file or an `sftp://` URL (see
[Remote datasets](#remote-datasets-sftp)). A directory is rejected. With no
argument, the viewer reads `FOGGY_VISION_DATABASE`, which may also be either
form. The image tree defaults to the sibling `images\` directory; use
`--images-dir DIR` if it lives elsewhere.

## Remote datasets (SFTP)

Install the optional transport:

```powershell
python -m pip install -e ".[sftp]"
```

Point `--db` at an `sftp://` URL, or put one in `FOGGY_VISION_DATABASE`:

```powershell
foggy-vision-viewer --db sftp://user@host:2222/srv/foggyvision/database.sqlite3
```

Only an explicit `sftp://` prefix is remote. Local paths — including Windows
paths such as `C:\data\db.sqlite3` — behave exactly as before, and no extra
command-line options are needed. Local use does not require `paramiko`.

- **Authentication** uses your SSH agent or the default key files
  (`~/.ssh/id_ed25519`, `~/.ssh/id_rsa`). Passwords are not accepted in the URL
  or on the command line. On Windows, Pageant is used automatically when it is
  running with a key loaded; Pageant must load a `.ppk` key and run at the same
  privilege level as the viewer.
- **Host keys** are checked strictly against `~/.ssh/known_hosts`. If the key is
  unknown or does not match, the viewer exits with the SHA-256 fingerprint and
  the command needed to trust it, for example:

  ```powershell
  ssh-keyscan -p 2222 host >> $env:USERPROFILE\.ssh\known_hosts
  ```

- **Cache:** the database is mirrored at startup to
  `%LOCALAPPDATA%\foggy-vision-viewer\remote\` (`~/Library/Caches/foggy-vision-viewer/remote/`
  on macOS, `${XDG_CACHE_HOME:-~/.cache}/foggy-vision-viewer/remote/` elsewhere),
  under a folder keyed by host and remote paths. Images and masks are downloaded on demand into the same folder
  and reused on later visits. Restart the viewer to pick up a new remote
  import; the cache is not evicted automatically, so delete the dataset folder
  to reclaim space.
- **`--images-dir`** with an `sftp://` database is a remote POSIX path on the
  same host (absolute, or starting with `~/`). It defaults to `<db_dir>/images`.
  The local mirror is managed automatically; do not pass a local path.
- Queries run locally against the mirrored database; images are fetched on the
  background decode thread. Remote mode prefetches one neighbor per step
  (local mode keeps two).

## Filters

- **Scene** — exact terrain/scene name from the database.
- **Fog type** — e.g. `marched`.
- **Volume** — fog volume name.
- **Skybox** — the skybox name recorded for the view. Databases imported before
  skybox metadata existed show only `All` here.
- **Session** — the `YYYY-MM-DD_HH-MM-SS` import session.
- **Visibility (m)** — inclusive min/max on the resolved visibility value.

`All` (or a blank visibility field) disables that filter. Filters use native
meters; the info panel also shows the mile equivalent.

## Media roles

The **View** selector switches between:

| Role | File |
| --- | --- |
| Color | `Frame-N.png` |
| Distance (m) | `Frame-N_distMask.tif` (32-bit float, auto-normalized for display) |
| Normalized distance | `Frame-N_distNormSmlMask.tif` |
| Validity | `Frame-N_validMask.png` |

If a frame has no such mask, the canvas shows a message and navigation
continues.

## Controls

| Input | Action |
| --- | --- |
| `Left` / `Right` | Previous / next image |
| `Home` / `End` | First / last image |
| `1` – `4` | Color / Distance / Normalized distance / Validity |
| `Enter` in a filter field | Apply the query |
| `Apply` button | Run the query and reset to the first result |

Arrow keys are ignored while a filter widget or dropdown has focus.

## Info panel

The panel shows the relative media path and role, session folder and UTC
capture time, image dimensions, scene/terrain and coverage, skybox name, camera
position and look direction, fog type/volume/density/multiplier, the resolved
visibility in
meters and miles with the metric used, all three metrics' raw
average/median/min/ray-count values, and which masks exist for the frame.

## Notes

- Skybox filtering and the info-panel line require loader 0.3.0 or newer.
  Legacy databases without skybox metadata still open; the Skybox dropdown
  contains only `All` and the panel shows `—`.
- Visibility uses the loader's auto-fallback: `simple`, else
  `ray_including_invalid`, else `ray_excluding_invalid`.
- Distance-mask TIFFs are normalized per image to 8-bit grayscale for display;
  the original files are never modified. Both TIFF roles are decoded with
  tifffile (which handles the LZW-compressed normalized mask).
- The viewer queries SQLite on the UI thread and decodes images on a
  background thread with a bounded LRU cache and neighbor prefetch.

## AI Disclosure

Portions of this project's code were written with the assistance of an AI
coding model, **DeepSeek V4.1 Flash** (`deepseek-v4.1-flash`), operating through
Claude Code. All generated and modified code was reviewed by the repository
authors and approved for inclusion before being committed.
