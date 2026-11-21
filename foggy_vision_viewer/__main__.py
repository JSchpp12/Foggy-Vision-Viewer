"""Command-line entry point for the FoggyVision viewer."""

from __future__ import annotations

import argparse
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
        metavar="FILE",
        help=(
            "FoggyVision database.sqlite3 file "
            "(or set FOGGY_VISION_DATABASE)"
        ),
    )
    parser.add_argument(
        "--images-dir",
        metavar="DIR",
        help="override the images directory (defaults to <db_dir>/images)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)

    try:
        from foggy_vision_loader import FoggyVisionLibrary, FoggyVisionSchemaError
    except ImportError:
        print(
            "foggy-vision-viewer: the foggy_vision_loader package is not installed.\n"
            "Install it with:  pip install -e F:\\repos\\Foggy_Vision_Loader",
            file=sys.stderr,
        )
        return 1

    try:
        library = FoggyVisionLibrary(args.db, images_dir=args.images_dir)
    except FoggyVisionSchemaError as exc:
        print(f"foggy-vision-viewer: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"foggy-vision-viewer: {exc}", file=sys.stderr)
        return 2

    import tkinter as tk

    from .app import ViewerApp

    try:
        app = ViewerApp(library)
    except tk.TclError as exc:
        print(f"foggy-vision-viewer: could not open the window: {exc}", file=sys.stderr)
        library.close()
        return 1

    try:
        app.run()
    finally:
        library.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
