"""Champion icons and the minimap, loaded off the UI thread and cached."""
from __future__ import annotations

import threading
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from .. import static


class IconStore:
    def __init__(self, root):
        self.root = root
        self._pil: dict[str, Image.Image | None] = {}
        self._tk: dict[tuple[str, int], ImageTk.PhotoImage] = {}
        self._pending: dict[str, list[Callable]] = {}
        self._minimap: Image.Image | None = None

    def get(self, champ: str, size: int, on_ready: Callable | None = None):
        """PhotoImage now, or None (and ``on_ready()`` once it arrives)."""
        if champ and champ.startswith("role:"):
            from ..theme.roleicons import role_photo_alpha
            from ..theme.tokens import EDGE, EDGE_HI
            return role_photo_alpha(champ[5:], size, EDGE_HI, EDGE)
        key = (champ or "").lower()
        if (key, size) in self._tk:
            return self._tk[(key, size)]
        if key not in self._pil:
            # already on disk: load now (a few ms), so icons don't pop in
            # after the screen appears; only downloads go to a thread
            p = static.champion_icon_local(champ)
            if p is not None:
                try:
                    self._pil[key] = Image.open(p).convert("RGBA")
                except Exception:
                    pass
        if key in self._pil:
            im = self._pil[key]
            if im is None:
                return None
            ph = ImageTk.PhotoImage(_rounded(im.resize((size, size),
                                                       Image.LANCZOS)))
            self._tk[(key, size)] = ph
            return ph
        if on_ready:
            first = key not in self._pending
            self._pending.setdefault(key, []).append(on_ready)
            if first:
                threading.Thread(target=self._load, args=(champ,),
                                 daemon=True).start()
        return None

    def _load(self, champ):
        key = champ.lower()
        try:
            p = static.champion_icon(champ)
            im = Image.open(p).convert("RGBA") if p else None
        except Exception:
            im = None

        def done():
            self._pil[key] = im
            cbs = self._pending.pop(key, [])
            if im is not None:
                for cb in set(cbs):
                    try:
                        cb()
                    except Exception:
                        pass
        try:
            self.root.after(0, done)
        except RuntimeError:
            pass

    def minimap(self) -> Image.Image | None:
        """The minimap if it is on disk; otherwise None now and a one-off
        background download (never blocks the UI thread)."""
        if self._minimap is None:
            try:
                p = static.minimap(offline=True)
                self._minimap = Image.open(p).convert("RGBA") if p else None
            except Exception:
                self._minimap = None
            if self._minimap is None and not getattr(self, "_mm_busy",
                                                     False):
                self._mm_busy = True
                threading.Thread(target=lambda: static.minimap(),
                                 daemon=True).start()
        return self._minimap


def _rounded(im: Image.Image, r: int = 3) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.size[0] - 1,
                                            im.size[1] - 1), r, fill=255)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out
