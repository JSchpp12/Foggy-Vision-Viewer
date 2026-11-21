"""Tkinter viewer for FoggyVision synthetic fog datasets."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("foggy-vision-viewer")
except PackageNotFoundError:  # running from an uninstalled source checkout
    __version__ = "0.0.0+unknown"
