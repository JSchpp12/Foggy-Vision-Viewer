"""Tkinter application for browsing a FoggyVision dataset."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from PIL import Image, ImageTk

from foggy_vision_loader import ImageRecord

from .filters import (
    ALL,
    ViewerInputError,
    build_selection,
    combobox_values,
    filters_from_values,
    natural_sort,
)
from .image_cache import Decoder, ImageCache, key_for
from .info import (
    MEDIA_ROLES,
    ROLE_ORDER,
    format_detail,
    format_path,
    format_title,
    media_absolute_path,
    media_relative_path,
)

CANVAS_BG = "#101114"
MESSAGE_FG = "#c8c8c8"
PATH_FG = "#777777"

PREFETCH_RADIUS = 2
POLL_MS = 60
RESIZE_MS = 120

HINT = "←/→ navigate · Home/End jump · 1–4 media · Enter apply"


class ViewerApp:
    """Single-image browser with filters, navigation, and media roles."""

    def __init__(
        self,
        library,
        root: Optional[tk.Tk] = None,
        *,
        decode: Optional[Decoder] = None,
        prefetch_radius: int = PREFETCH_RADIUS,
        display_label: Optional[str] = None,
    ) -> None:
        self.lib = library
        self.root = root if root is not None else tk.Tk()
        label = display_label if display_label is not None else self.lib.db_dir
        self.root.title(f"Foggy Vision Viewer — {label}")
        self.root.geometry("1200x800")
        self.root.minsize(700, 450)

        self.records: list[ImageRecord] = []
        self.index = -1
        self.role = "color"

        self._current_key: Optional[str] = None
        self._current_image: Optional[Image.Image] = None
        self._canvas_message: Optional[str] = None
        self._photo: Optional[ImageTk.PhotoImage] = None

        self._resize_job: Optional[str] = None
        self._last_canvas_size = (0, 0)
        self._closed = False

        self.cache = ImageCache(decode=decode)
        self._prefetch_radius = prefetch_radius

        self._values = combobox_values(self.lib)
        self.scene_var = tk.StringVar(value=ALL)
        self.fog_type_var = tk.StringVar(value=ALL)
        self.fog_volume_var = tk.StringVar(value=ALL)
        self.skybox_var = tk.StringVar(value=ALL)
        self.session_var = tk.StringVar(value=ALL)
        self.vis_min_var = tk.StringVar()
        self.vis_max_var = tk.StringVar()
        self.role_var = tk.StringVar(value=MEDIA_ROLES[self.role])

        self._build_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._show_message(
            "Choose filters (or leave them on All) and press Apply.\n"
            "Use the View selector to show color frames or ray masks."
        )
        self.root.after(POLL_MS, self._poll)
        self.root.after(150, self.canvas.focus_set)

    # ------------------------------------------------------------------ widgets

    def _build_widgets(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        query = ttk.Frame(self.root, padding=(8, 6))
        query.grid(row=0, column=0, sticky="ew")

        ttk.Label(query, text="Scene:").grid(row=0, column=0, padx=(0, 4))
        self.scene_combo = ttk.Combobox(
            query,
            width=22,
            state="readonly",
            textvariable=self.scene_var,
            values=self._values["scene"],
        )
        self.scene_combo.grid(row=0, column=1, padx=(0, 12))

        ttk.Label(query, text="Fog type:").grid(row=0, column=2, padx=(0, 4))
        self.fog_type_combo = ttk.Combobox(
            query,
            width=12,
            state="readonly",
            textvariable=self.fog_type_var,
            values=self._values["fog_type"],
        )
        self.fog_type_combo.grid(row=0, column=3, padx=(0, 12))

        ttk.Label(query, text="Volume:").grid(row=0, column=4, padx=(0, 4))
        self.fog_volume_combo = ttk.Combobox(
            query,
            width=16,
            state="readonly",
            textvariable=self.fog_volume_var,
            values=self._values["fog_volume"],
        )
        self.fog_volume_combo.grid(row=0, column=5, padx=(0, 12))

        ttk.Label(query, text="Session:").grid(row=0, column=6, padx=(0, 4))
        self.session_combo = ttk.Combobox(
            query,
            width=20,
            state="readonly",
            textvariable=self.session_var,
            values=self._values["session"],
        )
        self.session_combo.grid(row=0, column=7, padx=(0, 12))

        self.apply_button = ttk.Button(query, text="Apply", command=self.apply)
        self.apply_button.grid(row=0, column=8, padx=(4, 0))

        self.status_label = ttk.Label(query, text="No query yet", anchor="e")
        self.status_label.grid(row=0, column=9, sticky="e", padx=(16, 0))
        query.columnconfigure(9, weight=1)

        ttk.Label(query, text="Visibility (m):").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Label(query, text="min").grid(row=1, column=1, sticky="e", padx=(0, 4), pady=(6, 0))
        self.vis_min_entry = ttk.Entry(
            query, width=8, textvariable=self.vis_min_var
        )
        self.vis_min_entry.grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Label(query, text="max").grid(row=1, column=3, sticky="e", padx=(8, 4), pady=(6, 0))
        self.vis_max_entry = ttk.Entry(
            query, width=8, textvariable=self.vis_max_var
        )
        self.vis_max_entry.grid(row=1, column=4, sticky="w", pady=(6, 0))

        ttk.Label(query, text="View:").grid(
            row=1, column=5, sticky="w", padx=(16, 4), pady=(6, 0)
        )
        self.role_combo = ttk.Combobox(
            query,
            width=22,
            state="readonly",
            textvariable=self.role_var,
            values=list(MEDIA_ROLES.values()),
        )
        self.role_combo.grid(row=1, column=6, sticky="w", pady=(6, 0))

        ttk.Label(query, text="Skybox:").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        self.skybox_combo = ttk.Combobox(
            query,
            width=28,
            state="readonly",
            textvariable=self.skybox_var,
            values=self._values["skybox"],
        )
        self.skybox_combo.grid(row=2, column=1, sticky="w", pady=(6, 0))

        ttk.Label(query, text=HINT, foreground=PATH_FG).grid(
            row=2, column=2, columnspan=8, sticky="w", padx=(16, 0), pady=(6, 0)
        )

        self.canvas = tk.Canvas(
            self.root, background=CANVAS_BG, highlightthickness=0, bd=0
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")

        info = ttk.Frame(self.root, padding=(8, 4))
        info.grid(row=2, column=0, sticky="ew")
        info.columnconfigure(0, weight=1)

        self.info_title = ttk.Label(
            info, anchor="w", font=("TkDefaultFont", 10, "bold")
        )
        self.info_title.grid(row=0, column=0, columnspan=2, sticky="w")

        self.info_text = tk.Text(
            info,
            height=10,
            wrap="word",
            font=("TkFixedFont", 9),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            background=self.root.cget("background"),
            state="disabled",
        )
        self.info_text.grid(row=1, column=0, sticky="ew")
        self.info_scroll = ttk.Scrollbar(
            info, orient="vertical", command=self.info_text.yview
        )
        self.info_scroll.grid(row=1, column=1, sticky="ns")
        self.info_text.configure(yscrollcommand=self.info_scroll.set)

        self.info_path = ttk.Label(
            info, anchor="w", foreground=PATH_FG, font=("TkDefaultFont", 8)
        )
        self.info_path.grid(row=2, column=0, columnspan=2, sticky="w")

        for entry in (
            self.vis_min_entry,
            self.vis_max_entry,
            self.scene_combo,
            self.fog_type_combo,
            self.fog_volume_combo,
            self.skybox_combo,
            self.session_combo,
        ):
            entry.bind("<Return>", lambda _event: self.apply())

        self.role_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._on_role_selected()
        )

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", lambda _event: self.canvas.focus_set())

        self.root.bind("<Left>", lambda _event: self._key_step(-1))
        self.root.bind("<Right>", lambda _event: self._key_step(1))
        self.root.bind("<Home>", lambda _event: self._key_jump(first=True))
        self.root.bind("<End>", lambda _event: self._key_jump(first=False))
        for index in range(len(ROLE_ORDER)):
            self.root.bind(
                f"<Key-{index + 1}>",
                lambda _event, i=index: self._set_role_index(i),
            )

    # ------------------------------------------------------------------ filters

    def apply(self) -> None:
        """Validate the form, run the query, and reset to the first image."""
        try:
            filters = filters_from_values(
                scene=self.scene_var.get(),
                fog_type=self.fog_type_var.get(),
                fog_volume=self.fog_volume_var.get(),
                skybox=self.skybox_var.get(),
                session=self.session_var.get(),
                min_text=self.vis_min_var.get(),
                max_text=self.vis_max_var.get(),
            )
        except ViewerInputError as exc:
            self._set_status(str(exc))
            return

        self._set_status("Querying…")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            records = build_selection(self.lib, filters).records()
        except Exception as exc:
            self.records = []
            self.index = -1
            self._current_key = None
            self._set_info("", "", "")
            self._show_message("Query failed.")
            self._set_status(f"Query failed: {exc}")
            return
        finally:
            self.root.config(cursor="")

        self.records = natural_sort(records)
        self.cache.clear_failures()
        self._set_status(f"{len(self.records):,} results")
        self._show_index(0)
        self.root.after_idle(self.canvas.focus_set)

    # -------------------------------------------------------------- navigation

    def _key_step(self, delta: int) -> None:
        if self._editing():
            return
        self._step(delta)

    def _key_jump(self, *, first: bool) -> None:
        if self._editing() or not self.records:
            return
        self._show_index(0 if first else len(self.records) - 1)

    def _editing(self) -> bool:
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            return False
        return isinstance(focused, (tk.Entry, ttk.Entry, ttk.Combobox))

    def _step(self, delta: int) -> None:
        if not self.records:
            return
        target = max(0, min(self.index + delta, len(self.records) - 1))
        if target != self.index:
            self._show_index(target)

    def _on_role_selected(self) -> None:
        label = self.role_var.get()
        for key, value in MEDIA_ROLES.items():
            if value == label:
                self.role = key
                break
        if self.records and 0 <= self.index < len(self.records):
            self._show_index(self.index)

    def _set_role_index(self, index: int) -> None:
        if self._editing() or not 0 <= index < len(ROLE_ORDER):
            return
        self.role = ROLE_ORDER[index]
        self.role_var.set(MEDIA_ROLES[self.role])
        if self.records and 0 <= self.index < len(self.records):
            self._show_index(self.index)

    def _show_index(self, index: int) -> None:
        total = len(self.records)
        if total == 0:
            self.index = -1
            self._current_key = None
            self._set_info("", "", "")
            self._show_message("No images match")
            return

        index = max(0, min(index, total - 1))
        self.index = index
        record = self.records[index]
        label = MEDIA_ROLES.get(self.role, self.role)
        path = media_absolute_path(self.lib, record, self.role)
        self._current_key = key_for(path) if path is not None else None

        self._update_info(record, index, total)

        if path is None:
            self._show_message(f"No '{label}' image for this frame")
            return

        neighbors = []
        for step in range(1, self._prefetch_radius + 1):
            for offset in (step, -step):
                other = index + offset
                if 0 <= other < total:
                    other_path = media_absolute_path(
                        self.lib, self.records[other], self.role
                    )
                    if other_path is not None:
                        neighbors.append(other_path)
        self.cache.focus(path, neighbors)

        image = self.cache.lookup(path)
        if image is not None:
            self._render(image)
        else:
            self._show_message("Loading…")

    # ------------------------------------------------------------------ polling

    def _poll(self) -> None:
        if self._closed:
            return
        for key, image, error in self.cache.drain():
            if key != self._current_key:
                continue
            if image is not None:
                if image is not self._current_image:
                    self._render(image)
                continue
            record = (
                self.records[self.index]
                if 0 <= self.index < len(self.records)
                else None
            )
            label = MEDIA_ROLES.get(self.role, self.role)
            relative = (
                media_relative_path(record, self.role)
                if record is not None
                else None
            )
            text = f"Failed to load '{label}' image: {relative or key}"
            if error:
                text += f"\n{error}"
            self._show_message(text)
        self.root.after(POLL_MS, self._poll)

    # ----------------------------------------------------------------- rendering

    def _render(self, image: Image.Image) -> None:
        self._current_image = image
        self._canvas_message = None
        self._redraw()

    def _show_message(self, text: str) -> None:
        self._current_image = None
        self._canvas_message = text
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            width, height = 800, 600

        if self._current_image is not None:
            scaled = self._fit(self._current_image, width, height)
            # Keep a reference: tkinter does not, and an unreferenced
            # PhotoImage is garbage-collected, blanking the canvas.
            self._photo = ImageTk.PhotoImage(scaled, master=self.root)
            self.canvas.create_image(
                width // 2, height // 2, image=self._photo, anchor="center"
            )
        elif self._canvas_message is not None:
            self.canvas.create_text(
                width // 2,
                height // 2,
                text=self._canvas_message,
                fill=MESSAGE_FG,
                font=("TkDefaultFont", 12),
                width=max(200, width - 60),
                justify="center",
            )

    @staticmethod
    def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
        """Scale to fit while preserving aspect ratio; never upscale."""
        image_width, image_height = image.size
        if image_width <= 0 or image_height <= 0:
            return image
        scale = min(width / image_width, height / image_height)
        if scale >= 1.0:
            return image
        size = (
            max(1, round(image_width * scale)),
            max(1, round(image_height * scale)),
        )
        return image.resize(size, Image.Resampling.LANCZOS)

    def _on_canvas_configure(self, event) -> None:
        size = (event.width, event.height)
        if size == self._last_canvas_size:
            return
        self._last_canvas_size = size
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(RESIZE_MS, self._finish_resize)

    def _finish_resize(self) -> None:
        self._resize_job = None
        self._redraw()

    # --------------------------------------------------------------------- info

    def _set_detail(self, text: str) -> None:
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def _update_info(self, record: ImageRecord, index: int, total: int) -> None:
        self.info_title.configure(text=format_title(record, index, total))
        self._set_detail(format_detail(record))
        self.info_path.configure(text=format_path(record, self.role))

    def _set_info(self, title: str, detail: str, path: str) -> None:
        self.info_title.configure(text=title)
        self._set_detail(detail)
        self.info_path.configure(text=path)

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    # ---------------------------------------------------------------- lifecycle

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self.cache.close()
        try:
            self.lib.close()
        except Exception:
            pass
        self.root.destroy()
