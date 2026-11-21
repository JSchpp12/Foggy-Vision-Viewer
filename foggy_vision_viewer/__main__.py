"""Command-line entry point for the FoggyVision viewer."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from . import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foggy-vision-viewer",
        description="Browse images and ray masks in a FoggyVision database.",
    )
    parser.add_argument(
        "--db",
        metavar="FILE|URL",
        help=(
            "FoggyVision database.sqlite3 file or sftp:// URL "
            "(or set FOGGY_VISION_DATABASE)"
        ),
    )
    parser.add_argument(
        "--images-dir",
        metavar="DIR",
        help=(
            "override the images directory (defaults to <db_dir>/images); "
            "with an sftp:// database this is a remote POSIX path on the same host"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def _log(message: str) -> None:
    print(f"foggy-vision-viewer: {message}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)

    try:
        from foggy_vision_loader import (
            DATABASE_ENV,
            FoggyVisionLibrary,
            FoggyVisionSchemaError,
        )
    except ImportError:
        _log(
            "the foggy_vision_loader package is not installed.\n"
            "Install it with:  pip install -e F:\\repos\\Foggy_Vision_Loader"
        )
        return 1

    from .remote import (
        RemoteDependencyError,
        RemoteError,
        RemoteSource,
        is_remote_target,
        make_remote_decoder,
    )

    # The environment variable is resolved here only to decide whether the
    # target is remote.  The local branch still passes ``args.db`` straight
    # through so the loader keeps its own environment handling.
    db_value = args.db
    if db_value is None:
        db_value = os.environ.get(DATABASE_ENV)

    remote = None
    viewer_kwargs: dict = {}

    if is_remote_target(db_value):
        try:
            remote = RemoteSource.open(
                db_value, images_dir=args.images_dir, log=_log
            )
        except RemoteDependencyError as exc:
            _log(str(exc))
            return 1
        except (RemoteError, OSError, ValueError) as exc:
            _log(str(exc))
            return 2

        try:
            library = FoggyVisionLibrary(
                remote.db_path, images_dir=remote.images_dir
            )
        except FoggyVisionSchemaError as exc:
            _log(str(exc))
            remote.close()
            return 2
        except (FileNotFoundError, ValueError) as exc:
            _log(str(exc))
            remote.close()
            return 2

        viewer_kwargs = {
            "decode": make_remote_decoder(remote, remote.images_dir),
            "prefetch_radius": 1,
            "display_label": remote.display_label,
        }
    else:
        try:
            library = FoggyVisionLibrary(args.db, images_dir=args.images_dir)
        except FoggyVisionSchemaError as exc:
            _log(str(exc))
            return 2
        except (FileNotFoundError, ValueError) as exc:
            _log(str(exc))
            return 2

    import tkinter as tk

    from .app import ViewerApp

    try:
        app = ViewerApp(library, **viewer_kwargs)
    except tk.TclError as exc:
        _log(f"could not open the window: {exc}")
        library.close()
        if remote is not None:
            remote.close()
        return 1

    try:
        app.run()
    finally:
        library.close()
        if remote is not None:
            remote.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
