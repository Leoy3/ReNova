"""Kit de widgets propio sobre tk.Canvas: bordes redondeados con antialiasing,
botones, segmentados, menús oscuros, scroll fino, toasts y diálogos modales."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import tkinter as tk

from ctypes import wintypes

from PIL import Image, ImageDraw, ImageTk

from . import theme as T
from .theme import F, G, S

# ── Rectángulos redondeados (esquinas con antialiasing) ───────────────────────
_corner_cache: dict = {}


def _corners(r: int, fill: str, outline: str | None, bg: str, width: int = 1):
    key = (r, fill, outline, bg, width)
    c = _corner_cache.get(key)
    if c:
        return c
    ss = 4
    big = r * 2 * ss
    img = Image.new("RGB", (big, big), bg)
    d = ImageDraw.Draw(img)
    if outline:
        d.ellipse([0, 0, big - 1, big - 1], fill=outline)
        w = width * ss
        if big - 1 - w > w:
            d.ellipse([w, w, big - 1 - w, big - 1 - w], fill=fill)
    else:
        d.ellipse([0, 0, big - 1, big - 1], fill=fill)
    img = img.resize((r * 2, r * 2), Image.LANCZOS)
    c = {
        "nw": ImageTk.PhotoImage(img.crop((0, 0, r, r))),
        "ne": ImageTk.PhotoImage(img.crop((r, 0, 2 * r, r))),
        "sw": ImageTk.PhotoImage(img.crop((0, r, r, 2 * r))),
        "se": ImageTk.PhotoImage(img.crop((r, r, 2 * r, 2 * r))),
    }
    _corner_cache[key] = c
    return c


def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, fill, outline=None, bg=None, tags=(), width=1):
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    if x2 - x1 < 6 or y2 - y1 < 6:
        if x2 > x1 and y2 > y1:
            cv.create_rectangle(x1, y1, x2, y2, fill=fill, width=0, tags=tags)
        return
    r = int(max(2, min(r, (x2 - x1) // 2, (y2 - y1) // 2)))
    bg = bg or cv["bg"]
    c = _corners(r, fill, outline, bg, width)
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, width=0, tags=tags)
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, width=0, tags=tags)
    cv.create_image(x1, y1, image=c["nw"], anchor="nw", tags=tags)
    cv.create_image(x2, y1, image=c["ne"], anchor="ne", tags=tags)
    cv.create_image(x1, y2, image=c["sw"], anchor="sw", tags=tags)
    cv.create_image(x2, y2, image=c["se"], anchor="se", tags=tags)
    if outline:
        cv.create_rectangle(x1 + r, y1, x2 - r, y1 + width, fill=outline, width=0, tags=tags)
        cv.create_rectangle(x1 + r, y2 - width, x2 - r, y2, fill=outline, width=0, tags=tags)
        cv.create_rectangle(x1, y1 + r, x1 + width, y2 - r, fill=outline, width=0, tags=tags)
        cv.create_rectangle(x2 - width, y1 + r, x2, y2 - r, fill=outline, width=0, tags=tags)


def rounded_photo(img: Image.Image, w: int, h: int, radius: int, bg: str) -> ImageTk.PhotoImage:
    """Recorta tipo 'cover' a w×h y redondea esquinas sobre el color de fondo."""
    src_ratio = img.width / max(1, img.height)
    dst_ratio = w / max(1, h)
    if src_ratio > dst_ratio:
        nw = int(img.height * dst_ratio)
        x = (img.width - nw) // 2
        img = img.crop((x, 0, x + nw, img.height))
    else:
        nh = int(img.width / dst_ratio)
        y = (img.height - nh) // 2
        img = img.crop((0, y, img.width, y + nh))
    img = img.resize((w, h), Image.LANCZOS).convert("RGB")
    ss = 4
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius * ss, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    base = Image.new("RGB", (w, h), bg)
    base.paste(img, (0, 0), mask)
    return ImageTk.PhotoImage(base)


def circle_photo(d: int, fill: str, bg: str) -> ImageTk.PhotoImage:
    key = ("circle", d, fill, bg)
    c = _corner_cache.get(key)
    if c:
        return c
    ss = 4
    img = Image.new("RGB", (d * ss, d * ss), bg)
    ImageDraw.Draw(img).ellipse([0, 0, d * ss - 1, d * ss - 1], fill=fill)
    c = ImageTk.PhotoImage(img.resize((d, d), Image.LANCZOS))
    _corner_cache[key] = c
    return c


# ── Win32 ──────────────────────────────────────────────────────────────────────
def _hwnd(win) -> int:
    try:
        win.update_idletasks()
        return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    except Exception:
        return 0


_ALT_PROCS: dict = {}   # hwnd → (proc nuevo, proc original)
_ALT_KEEP: list = []    # los procs no se liberan nunca (Windows podría llamarlos tarde)


def block_alt_menu(win):
    """Al soltar Alt, Windows abre el menú de la ventana (Restaurar, Mover, Tamaño…) y la app queda
    frenada mientras está abierto. Como el zoom es Alt + rueda, se descarta ese comando (SC_KEYMENU sin
    letra). Alt+Espacio y Alt+F4 siguen funcionando."""
    if os.name != "nt":
        return
    hwnd = _hwnd(win)
    if not hwnd or hwnd in _ALT_PROCS:
        return
    try:
        u32 = ctypes.windll.user32
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)
        u32.SetWindowLongPtrW.restype = ctypes.c_void_p
        u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        u32.CallWindowProcW.restype = ctypes.c_ssize_t
        u32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM]
        WM_SYSCOMMAND, SC_KEYMENU, WM_NCDESTROY = 0x0112, 0xF100, 0x0082
        state = {}

        def proc(h, msg, wp, lp):
            if msg == WM_SYSCOMMAND and (wp & 0xFFF0) == SC_KEYMENU and lp == 0:
                return 0
            res = u32.CallWindowProcW(state["old"], h, msg, wp, lp)
            if msg == WM_NCDESTROY:
                _ALT_PROCS.pop(h, None)
            return res

        new = WNDPROC(proc)
        state["old"] = u32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(new, ctypes.c_void_p))  # GWLP_WNDPROC
        _ALT_PROCS[hwnd] = (new, state["old"])
        _ALT_KEEP.append(new)
    except Exception:
        pass


def _colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def dark_titlebar(win, caption: str = T.BG_2):
    try:
        hwnd = _hwnd(win)
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(on), 4)
        color = ctypes.c_int(_colorref(caption))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), 4)   # Win11: color de barra
        text = ctypes.c_int(_colorref(T.TEXT_2))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), 4)
    except Exception:
        pass


def flash_window(win):
    try:
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), _hwnd(win), 0x3 | 0xC, 3, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def reveal_in_explorer(path: str):
    """Abre el Explorador con el archivo seleccionado."""
    path = os.path.normpath(path)

    def run():
        if not os.path.exists(path):
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                os.startfile(folder)
            return
        try:
            shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
            ole32.CoInitialize(None)
            shell32.ILCreateFromPathW.restype = ctypes.c_void_p
            shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
            pidl = shell32.ILCreateFromPathW(path)
            if pidl:
                shell32.SHOpenFolderAndSelectItems.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
                hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
                shell32.ILFree.argtypes = [ctypes.c_void_p]
                shell32.ILFree(pidl)
                if hr == 0:
                    return
        except Exception:
            pass
        subprocess.Popen(f'explorer /select,"{path}"')
    threading.Thread(target=run, daemon=True).start()


def open_path(path: str):
    try:
        os.startfile(path)
    except OSError:
        reveal_in_explorer(path)


# ── Tooltip ────────────────────────────────────────────────────────────────────
class Tooltip:
    _win = None

    @classmethod
    def show(cls, widget, text: str, x: int, y: int, above: bool = False):
        cls.hide()
        if not text:
            return
        root = widget.winfo_toplevel()
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=T.BORDER)
        lbl = tk.Label(w, text=text, bg=T.SURFACE_HI, fg=T.TEXT, font=F.small,
                       padx=S(9), pady=S(5), justify="left", wraplength=S(320))
        lbl.pack(padx=1, pady=1)
        w.update_idletasks()
        tw, th = w.winfo_reqwidth(), w.winfo_reqheight()
        sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
        px = min(max(4, x - tw // 2), sx - tw - 4)
        py = y - th - S(6) if above else y + S(8)
        if py + th > sy - 4:
            py = y - th - S(10)
        w.geometry(f"+{px}+{max(4, py)}")
        cls._win = w

    @classmethod
    def hide(cls):
        if cls._win is not None:
            try:
                cls._win.destroy()
            except tk.TclError:
                pass
            cls._win = None


def attach_tooltip(widget, text_or_fn, delay: int = 450):
    state = {"job": None}

    def enter(_e):
        state["job"] = widget.after(delay, show)

    def show():
        text = text_or_fn() if callable(text_or_fn) else text_or_fn
        x = widget.winfo_rootx() + widget.winfo_width() // 2
        y = widget.winfo_rooty() + widget.winfo_height()
        Tooltip.show(widget, text, x, y)

    def leave(_e):
        if state["job"]:
            widget.after_cancel(state["job"])
            state["job"] = None
        Tooltip.hide()

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", leave, add="+")


# ── Botón ──────────────────────────────────────────────────────────────────────
VARIANTS = {
    "primary": dict(fill=T.ACCENT, hover=T.ACCENT_HI, press=T.ACCENT_LO, fg=T.ACCENT_INK,
                    fg_hover=T.ACCENT_INK, outline=None),
    "secondary": dict(fill=T.SURFACE_2, hover=T.SURFACE_HI, press=T.BORDER, fg=T.TEXT,
                      fg_hover=T.TEXT, outline=T.BORDER),
    "ghost": dict(fill=None, hover=T.SURFACE_HI, press=T.BORDER, fg=T.TEXT_2,
                  fg_hover=T.TEXT, outline=None),
    "danger": dict(fill=None, hover=T.ERR_SOFT, press=T.ERR_SOFT, fg=T.TEXT_2,
                   fg_hover=T.ERR, outline=None),
    "outline": dict(fill=None, hover=T.SURFACE_2, press=T.SURFACE_HI, fg=T.TEXT_2,
                    fg_hover=T.TEXT, outline=T.BORDER),
}


class Button(tk.Canvas):
    def __init__(self, master, text: str = "", icon: str | None = None, command=None,
                 variant: str = "secondary", height: int = 34, width: int | None = None,
                 padx: int = 14, radius: int = 8, font=None, icon_font=None, tooltip=None,
                 trailing: str | None = None, bg: str | None = None, fg: str | None = None):
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0, height=S(height),
                         cursor="hand2", takefocus=0)
        self.text, self.icon, self.trailing = text, icon, trailing
        self.command = command
        self.variant = variant
        self.font = font or F.body_b
        self.icon_font = icon_font or F.icon
        self.radius = S(radius)
        self.padx = S(padx)
        self._fixed_w = S(width) if width else None
        self._fg_override = fg
        self.enabled = True
        self._hover = self._press = False
        self.configure(width=self._natural_width())
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        if tooltip:
            attach_tooltip(self, tooltip)

    def _natural_width(self) -> int:
        if self._fixed_w:
            return self._fixed_w
        w = 0
        if self.icon:
            w += self.icon_font.measure(self.icon)
        if self.text:
            w += self.font.measure(self.text) + (S(8) if self.icon else 0)
        if self.trailing:
            w += S(8) + F.icon_sm.measure(self.trailing)
        if not self.text:
            return max(int(self["height"]), w + S(16))
        return w + self.padx * 2

    def configure_button(self, **kw):
        for k in ("text", "icon", "variant", "command", "trailing"):
            if k in kw:
                setattr(self, k, kw[k])
        if "enabled" in kw:
            self.enabled = kw["enabled"]
            self.configure(cursor="hand2" if self.enabled else "arrow")
        self.configure(width=self._natural_width())
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = self._press = False
        self._draw()

    def _on_press(self, _e):
        if self.enabled:
            self._press = True
            self._draw()

    def _on_release(self, e):
        was = self._press
        self._press = False
        self._draw()
        if was and self.enabled and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            if self.command:
                self.command()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(self["width"]), int(self["height"])
        v = VARIANTS[self.variant]
        if not self.enabled:
            fill = T.SURFACE_2 if v["fill"] else None
            fg, outline = T.TEXT_4, (T.BORDER_SOFT if v["outline"] or v["fill"] else None)
        else:
            fill = v["press"] if self._press else v["hover"] if self._hover else v["fill"]
            fg = v["fg_hover"] if self._hover else (self._fg_override or v["fg"])
            outline = v["outline"]
        if fill or outline:
            round_rect(self, 0, 0, w, h, self.radius, fill or self._bg, outline, self._bg)
        parts = []
        if self.icon:
            parts.append((self.icon, self.icon_font))
        if self.text:
            parts.append((self.text, self.font))
        if self.trailing:
            parts.append((self.trailing, F.icon_sm))
        gap = S(8)
        total = sum(f.measure(t) for t, f in parts) + gap * (len(parts) - 1)
        x = (w - total) / 2
        cy = h / 2
        for t, f in parts:
            self.create_text(x, cy + (S(1) if f in (F.icon, F.icon_sm, F.icon_md) else 0),
                             text=t, font=f, fill=fg, anchor="w")
            x += f.measure(t) + gap


# ── Segmentado ─────────────────────────────────────────────────────────────────
class Segmented(tk.Canvas):
    def __init__(self, master, options: list, value, command=None, height: int = 36, bg=None):
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0, height=S(height), takefocus=0)
        self.options = options  # [(value, label, icon)]
        self.value = value
        self.command = command
        self._hover = None
        self._rects = []
        self._pad = S(4)
        widths = [self._seg_w(o) for o in options]
        self.configure(width=sum(widths) + self._pad * 2)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._click)

    def _seg_w(self, o):
        w = F.body_b.measure(o[1]) + S(28)
        if len(o) > 2 and o[2]:
            w += F.icon_sm.measure(o[2]) + S(8)
        return w

    def set(self, value):
        self.value = value
        self._draw()

    def _hit(self, x, y):
        for (x1, x2, val) in self._rects:
            if x1 <= x < x2:
                return val
        return None

    def _motion(self, e):
        self._set_hover(self._hit(e.x, e.y))

    def _set_hover(self, val):
        if val != self._hover:
            self._hover = val
            self.configure(cursor="hand2" if val is not None and val != self.value else "arrow")
            self._draw()

    def _click(self, e):
        val = self._hit(e.x, e.y)
        if val is not None and val != self.value:
            self.value = val
            self._draw()
            if self.command:
                self.command(val)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        round_rect(self, 0, 0, w, h, S(9), T.SURFACE, T.BORDER_SOFT, self._bg)
        x = self._pad
        self._rects = []
        for o in self.options:
            sw = self._seg_w(o)
            active = o[0] == self.value
            hov = o[0] == self._hover and not active
            if active:
                round_rect(self, x, self._pad, x + sw, h - self._pad, S(6), T.SURFACE_HI, None, T.SURFACE)
            elif hov:
                round_rect(self, x, self._pad, x + sw, h - self._pad, S(6), T.SURFACE_2, None, T.SURFACE)
            fg = T.TEXT if active else (T.TEXT_2 if hov else T.TEXT_3)
            cx = x + sw / 2
            if len(o) > 2 and o[2]:
                iw = F.icon_sm.measure(o[2])
                tw = F.body_b.measure(o[1])
                sx = cx - (iw + S(8) + tw) / 2
                self.create_text(sx, h / 2 + S(1), text=o[2], font=F.icon_sm,
                                 fill=T.ACCENT if active else fg, anchor="w")
                self.create_text(sx + iw + S(8), h / 2, text=o[1], font=F.body_b, fill=fg, anchor="w")
            else:
                self.create_text(cx, h / 2, text=o[1], font=F.body_b, fill=fg)
            self._rects.append((x, x + sw, o[0]))
            x += sw


# ── Toggle ─────────────────────────────────────────────────────────────────────
class Toggle(tk.Canvas):
    def __init__(self, master, value: bool, command=None, label: str = "", bg=None, tooltip=None):
        self._bg = bg or master.cget("bg")
        self.label = label
        lw = (F.body.measure(label) + S(10)) if label else 0
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0,
                         width=S(36) + lw, height=S(22), cursor="hand2", takefocus=0)
        self.value = value
        self.command = command
        self._lw = lw
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonRelease-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        if tooltip:
            attach_tooltip(self, tooltip)

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set(self, value: bool):
        self.value = value
        self._draw()

    def _toggle(self, _e=None):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)

    def _draw(self):
        self.delete("all")
        h = S(20)
        y = (int(self["height"]) - h) // 2
        x = 0
        track = T.ACCENT if self.value else (T.BORDER if self._hover else T.SURFACE_HI)
        round_rect(self, x, y, x + S(36), y + h, h // 2, track, None, self._bg)
        d = h - S(6)
        kx = x + S(36) - d - S(3) if self.value else x + S(3)
        self.create_image(kx, y + S(3), image=circle_photo(d, T.TEXT if not self.value else "#FFFFFF", track),
                          anchor="nw")
        if self.label:
            self.create_text(S(36) + S(10), int(self["height"]) / 2, text=self.label, font=F.body,
                             fill=T.TEXT if self._hover else T.TEXT_2, anchor="w")


def draw_checkbox(cv: tk.Canvas, x, y, state, bg, size=None, hover=False):
    """state: True / False / 'mixed'."""
    size = size or S(18)
    if state:
        round_rect(cv, x, y, x + size, y + size, S(5), T.ACCENT, None, bg)
        glyph = G.REMOVE if state == "mixed" else G.CHECK
        cv.create_text(x + size / 2, y + size / 2 + S(1), text=glyph, font=F.icon_sm, fill=T.ACCENT_INK)
    else:
        round_rect(cv, x, y, x + size, y + size, S(5), T.SURFACE_2 if hover else T.SURFACE,
                   T.TEXT_3 if hover else T.BORDER, bg)


# ── Menú emergente oscuro ──────────────────────────────────────────────────────
class PopupMenu(tk.Toplevel):
    """items: dicts con label, icon, command, checked, enabled, hint, keep_open;
    o {'separator': True} / {'header': 'Texto'}."""
    _current = None

    def __init__(self, master, items: list, x: int, y: int, min_width: int = 200,
                 on_close=None, align: str = "left", above: bool = False):
        if PopupMenu._current is not None:
            PopupMenu._current.close()
        root = master.winfo_toplevel()
        super().__init__(root)
        PopupMenu._current = self
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=T.BORDER)
        self.on_close = on_close
        self._closed = False
        self._rows = []
        self._focus_idx = None
        inner = tk.Frame(self, bg=T.SURFACE_2, padx=S(4), pady=S(4))
        inner.pack(padx=1, pady=1)

        has_icon = any(i.get("icon") for i in items)
        label_w = max([F.body.measure(i.get("label", "")) for i in items if i.get("label")] + [0])
        hint_w = max([F.small.measure(i.get("hint", "")) for i in items if i.get("hint")] + [0])
        width = max(S(min_width), (S(36) if has_icon else S(14)) + label_w + S(24) +
                    (hint_w + S(16) if hint_w else 0) + S(22))
        for it in items:
            if it.get("separator"):
                tk.Frame(inner, height=1, bg=T.BORDER, width=width).pack(fill="x", pady=S(4))
            elif it.get("header"):
                tk.Label(inner, text=it["header"].upper(), font=F.label, fg=T.TEXT_3,
                         bg=T.SURFACE_2, anchor="w", padx=S(10)).pack(fill="x", pady=(S(6), S(2)))
            else:
                row = _MenuRow(inner, self, it, width, has_icon)
                row.pack(fill="x")
                self._rows.append(row)

        self.update_idletasks()
        mw, mh = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if align == "right":
            x -= mw
        x = min(max(4, x), sw - mw - 4)
        if above:
            y = max(4, y - mh)
        elif y + mh > sh - 40:
            y = max(4, y - mh - S(40))
        self.geometry(f"+{int(x)}+{int(y)}")
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.bind("<ButtonPress>", self._outside_click)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Up>", lambda e: self._move(-1))
        self.bind("<Down>", lambda e: self._move(1))
        self.bind("<Return>", lambda e: self._activate_focused())
        self.bind("<FocusOut>", self._focus_out)

    def _outside_click(self, e):
        wx, wy = self.winfo_rootx(), self.winfo_rooty()
        if not (wx <= e.x_root < wx + self.winfo_width() and wy <= e.y_root < wy + self.winfo_height()):
            self.close()

    def _focus_out(self, _e):
        self.after(60, self._check_focus)

    def _check_focus(self):
        if self._closed:
            return
        f = self.focus_get()
        if f is None or not str(f).startswith(str(self)):
            self.close()

    def _move(self, d):
        enabled = [i for i, r in enumerate(self._rows) if r.item.get("enabled", True)]
        if not enabled:
            return
        if self._focus_idx not in enabled:
            self._focus_idx = enabled[0] if d > 0 else enabled[-1]
        else:
            k = enabled.index(self._focus_idx) + d
            self._focus_idx = enabled[k % len(enabled)]
        for i, r in enumerate(self._rows):
            r.set_hover(i == self._focus_idx)

    def _activate_focused(self):
        if self._focus_idx is not None:
            self._rows[self._focus_idx].activate()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if PopupMenu._current is self:
            PopupMenu._current = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close()


class _MenuRow(tk.Canvas):
    def __init__(self, master, menu: PopupMenu, item: dict, width: int, has_icon: bool):
        super().__init__(master, bg=T.SURFACE_2, highlightthickness=0, bd=0,
                         width=width, height=S(32), takefocus=0)
        self.menu, self.item, self.has_icon = menu, item, has_icon
        self._hover = False
        enabled = item.get("enabled", True)
        self.configure(cursor="hand2" if enabled else "arrow")
        self.bind("<Enter>", lambda e: self.set_hover(True))
        self.bind("<Leave>", lambda e: self.set_hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.activate())
        self._draw()

    def set_hover(self, v):
        if not self.item.get("enabled", True):
            v = False
        if v != self._hover:
            self._hover = v
            self._draw()

    def activate(self):
        if not self.item.get("enabled", True):
            return
        cmd = self.item.get("command")
        if not self.item.get("keep_open"):
            self.menu.close()
        if cmd:
            cmd()

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        enabled = self.item.get("enabled", True)
        if self._hover:
            round_rect(self, 0, S(1), w, h - S(1), S(6), T.SURFACE_HI, None, T.SURFACE_2)
        info = self.item.get("info")
        fg = T.TEXT if enabled else (T.TEXT_2 if info else T.TEXT_4)
        x = S(10)
        if self.has_icon:
            if self.item.get("icon"):
                color = self.item.get("icon_color") or (T.TEXT_2 if enabled or info else T.TEXT_4)
                self.create_text(x + S(8), h / 2 + S(1), text=self.item["icon"], font=F.icon_sm, fill=color)
            x += S(26)
        self.create_text(x, h / 2, text=self.item.get("label", ""), font=F.body, fill=fg, anchor="w")
        right = w - S(12)
        if self.item.get("checked"):
            self.create_text(right, h / 2 + S(1), text=G.CHECK, font=F.icon_sm, fill=T.ACCENT, anchor="e")
            right -= S(22)
        if self.item.get("hint"):
            self.create_text(right, h / 2, text=self.item["hint"], font=F.small, fill=T.TEXT_3, anchor="e")


# ── Campo de texto ─────────────────────────────────────────────────────────────
class TextInput(tk.Canvas):
    def __init__(self, master, placeholder: str = "", icon: str | None = None, height: int = 44,
                 font=None, bg=None, fill=T.SURFACE, on_submit=None, on_paste=None, mono=False):
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0, height=S(height), takefocus=0)
        self.fill = fill
        self.icon = icon
        self.placeholder = placeholder
        self.on_submit = on_submit
        self.on_paste = on_paste
        self._focus = self._hover = False
        self.var = tk.StringVar()
        self._font = font or F.input
        self.entry = tk.Entry(self, textvariable=self.var, bd=0, relief="flat", bg=fill, fg=T.TEXT,
                              insertbackground=T.TEXT, selectbackground=T.SELECT,
                              selectforeground=T.TEXT, font=font or F.input,
                              highlightthickness=0, disabledbackground=fill)
        self._entry_win = self.create_window(0, 0, window=self.entry, anchor="w")
        self._ph = tk.Label(self, text=placeholder, bg=fill, fg=T.TEXT_3, font=font or F.input,
                            anchor="w", padx=0, pady=0, bd=0, cursor="xterm")
        self._ph.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._ph.bind("<Button-3>", self._context_menu)
        self._right = []
        self.bind("<Configure>", lambda e: self._layout())
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        for w in (self, self.entry):
            w.bind("<Enter>", lambda e: self._set_hover(True), add="+")
            w.bind("<Leave>", lambda e: self._set_hover(False), add="+")
        self.entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.entry.bind("<Return>", lambda e: self.on_submit and self.on_submit())
        self.entry.bind("<Button-3>", self._context_menu)
        self.entry.bind("<<Paste>>", self._handle_paste)
        self.entry.bind("<Control-a>", lambda e: (self.entry.select_range(0, "end"),
                                                  self.entry.icursor("end"), "break")[-1])
        self.var.trace_add("write", lambda *a: self._draw_bg())

    def add_right(self, widget):
        self._right.append((widget, self.create_window(0, 0, window=widget, anchor="e")))
        self._layout()

    def get(self) -> str:
        return self.var.get()

    def set(self, text: str):
        self.var.set(text)
        self.entry.icursor("end")

    def focus(self):
        self.entry.focus_set()

    def _set_hover(self, v):
        self._hover = v
        self._draw_bg()

    def _set_focus(self, v):
        self._focus = v
        self._draw_bg()

    def _layout(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        left = S(16) + (F.icon.measure(self.icon) + S(12) if self.icon else 0)
        right = w - S(6)
        for widget, wid in reversed(self._right):
            self.coords(wid, right, h / 2)
            right -= widget.winfo_reqwidth() + S(4)
        self.coords(self._entry_win, left, h / 2)
        self.itemconfigure(self._entry_win, width=max(20, right - left - S(6)))
        self._draw_bg()

    def _draw_bg(self):
        self.delete("bg")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        outline = T.ACCENT if self._focus else (T.BORDER if self._hover else T.BORDER_SOFT)
        round_rect(self, 0, 0, w, h, S(10), self.fill, outline, self._bg, tags=("bg",),
                   width=2 if self._focus else 1)
        if self.icon:
            self.create_text(S(16), h / 2 + S(1), text=self.icon, font=F.icon,
                             fill=T.ACCENT if self._focus else T.TEXT_3, anchor="w", tags=("bg",))
        self.tag_lower("bg")
        if not self.var.get() and self.placeholder:
            ew = int(self.itemcget(self._entry_win, "width") or 0)
            self._ph.configure(text=T.ellipsize(self._font, self.placeholder, ew - S(4)))
            self._ph.place(in_=self.entry, x=S(1), rely=0.5, anchor="w", width=max(10, ew - S(2)))
            self._ph.lift()
        else:
            self._ph.place_forget()

    def _clipboard(self) -> str:
        try:
            return self.clipboard_get()
        except tk.TclError:
            return ""

    def paste(self):
        text = self._clipboard()
        if not text:
            return
        if self.on_paste and self.on_paste(text, self.var.get()):
            return
        try:
            self.entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.entry.insert("insert", text.replace("\r", " ").replace("\n", " ").strip())

    def _handle_paste(self, _e):
        self.paste()
        return "break"

    def _context_menu(self, e):
        self.entry.focus_set()
        try:
            has_sel = bool(self.entry.selection_present())
        except tk.TclError:
            has_sel = False
        has_text = bool(self.var.get())
        has_clip = bool(self._clipboard().strip())

        def copy():
            text = self.entry.selection_get() if has_sel else self.var.get()
            self.clipboard_clear()
            self.clipboard_append(text)

        def cut():
            copy()
            if has_sel:
                self.entry.delete("sel.first", "sel.last")
            else:
                self.var.set("")

        items = [
            {"label": "Pegar", "icon": G.PASTE, "command": self.paste, "enabled": has_clip, "hint": "Ctrl+V"},
            {"label": "Copiar", "icon": G.COPY, "command": copy, "enabled": has_text, "hint": "Ctrl+C"},
            {"label": "Cortar", "icon": G.CUT, "command": cut, "enabled": has_text, "hint": "Ctrl+X"},
            {"separator": True},
            {"label": "Seleccionar todo", "icon": G.SELECT_ALL, "enabled": has_text,
             "command": lambda: self.entry.select_range(0, "end"), "hint": "Ctrl+A"},
            {"label": "Borrar", "icon": G.DELETE, "enabled": has_text, "command": lambda: self.var.set("")},
        ]
        PopupMenu(self, items, e.x_root, e.y_root, min_width=210)


# ── Scroll ─────────────────────────────────────────────────────────────────────
class ThinScrollbar(tk.Canvas):
    def __init__(self, master, command, bg=None):
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0, width=S(10), takefocus=0)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self._drag = None
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _draw(self):
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        h = self.winfo_height()
        w = int(self["width"])
        y1 = self.first * h
        y2 = max(y1 + S(28), self.last * h)
        tw = S(6) if self._hover or self._drag else S(4)
        x = (w - tw) / 2
        round_rect(self, x, y1 + S(2), x + tw, y2 - S(2), tw // 2,
                   T.BORDER if (self._hover or self._drag) else T.SURFACE_HI, None, self._bg)

    def _press(self, e):
        h = self.winfo_height()
        if self.first * h <= e.y <= self.last * h:
            self._drag = (e.y, self.first)
        else:
            self.command("moveto", max(0.0, e.y / h - (self.last - self.first) / 2))

    def _motion(self, e):
        if self._drag:
            y0, f0 = self._drag
            self.command("moveto", f0 + (e.y - y0) / max(1, self.winfo_height()))


class ScrollArea(tk.Frame):
    def __init__(self, master, bg=None):
        bg = bg or master.cget("bg")
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=S(24), takefocus=0)
        self.bar = ThinScrollbar(self, self.canvas.yview, bg=bg)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas._wheel_target = self.wheel  # type: ignore[attr-defined]

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def wheel(self, e):
        if self.canvas.yview() == (0.0, 1.0):
            return
        self.canvas.yview_scroll(int(-e.delta / 120 * 3), "units")

    def scroll_to_widget(self, widget):
        self.update_idletasks()
        total = max(1, self.inner.winfo_height())
        y = widget.winfo_y()
        top, bottom = self.canvas.yview()
        view_h = self.canvas.winfo_height()
        if y / total < top or (y + widget.winfo_height()) / total > bottom:
            self.canvas.yview_moveto(max(0.0, (y - view_h / 3) / total))


def install_wheel_router(root):
    def on_wheel(e):
        w = root.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            tgt = getattr(w, "_wheel_target", None)
            if tgt:
                tgt(e)
                return
            w = getattr(w, "master", None)
    root.bind_all("<MouseWheel>", on_wheel)


# ── Tarjeta contenedora ────────────────────────────────────────────────────────
class Card(tk.Frame):
    """Contenedor con fondo redondeado. El contenido va en `.body` (se adapta al alto)."""

    def __init__(self, master, padding: int = 16, radius: int = 14, fill=T.SURFACE,
                 outline=T.BORDER_SOFT, bg=None, padx=None, pady=None):
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)
        self.fill, self.outline = fill, outline
        self.radius = S(radius)
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0, takefocus=0)
        self.cv.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True,
                       padx=S(padx if padx is not None else padding),
                       pady=S(pady if pady is not None else padding))
        self.tk.call("lower", self.cv._w)
        self.cv.bind("<Configure>", lambda e: self._draw())

    def set_outline(self, color):
        self.outline = color
        self._draw()

    def _draw(self):
        cv = self.cv
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 10 or h < 10:
            return
        round_rect(cv, 0, 0, w, h, self.radius, self.fill, self.outline, self._bg)


# ── Toast ──────────────────────────────────────────────────────────────────────
class Toast:
    def __init__(self, root):
        self.root = root
        self.frame = None
        self._job = None

    def show(self, text: str, kind: str = "info", action: tuple | None = None, ms: int = 4500):
        self.hide()
        icon, color = {"ok": (G.COMPLETED, T.OK), "warn": (G.WARNING, T.WARN),
                       "error": (G.ERROR, T.ERR)}.get(kind, (G.INFO, T.TEXT_2))
        cv = tk.Canvas(self.root, bg=T.BG, highlightthickness=0, bd=0, takefocus=0)
        text_w = min(F.body.measure(text), S(520))
        btn = None
        if action:
            btn = Button(cv, text=action[0], variant="ghost", height=28, padx=10,
                         command=lambda: (self.hide(), action[1]()), bg=T.SURFACE_HI, fg=T.ACCENT)
        bw = btn.winfo_reqwidth() + S(8) if btn else 0
        w = S(16) + S(26) + text_w + S(16) + bw + S(28)
        h = S(46)
        cv.configure(width=w, height=h)
        round_rect(cv, 0, 0, w, h, S(10), T.SURFACE_HI, T.BORDER, T.BG)
        cv.create_text(S(16), h / 2 + S(1), text=icon, font=F.icon, fill=color, anchor="w")
        cv.create_text(S(42), h / 2, text=T.ellipsize(F.body, text, text_w), font=F.body, fill=T.TEXT, anchor="w")
        close = cv.create_text(w - S(16), h / 2 + S(1), text=G.CLOSE, font=F.icon_sm, fill=T.TEXT_3)
        cv.tag_bind(close, "<Button-1>", lambda e: self.hide())
        if btn:
            cv.create_window(w - S(34), h / 2, window=btn, anchor="e")
        cv.place(relx=0.5, rely=1.0, y=-S(62), anchor="s")
        self.frame = cv
        self._job = self.root.after(ms, self.hide)

    def hide(self):
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self.frame is not None:
            try:
                self.frame.destroy()
            except tk.TclError:
                pass
            self.frame = None


# ── Diálogo modal ──────────────────────────────────────────────────────────────
class Modal(tk.Toplevel):
    def __init__(self, master, title: str, width: int, height: int, resizable: bool = False):
        root = master.winfo_toplevel()
        super().__init__(root)
        self.withdraw()
        self.title(title)
        self.configure(bg=T.BG)
        self.transient(root)
        self.resizable(resizable, resizable)
        self.result = None
        w, h = S(width), S(height)
        root.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3
        x = max(0, min(x, self.winfo_screenwidth() - w))
        y = max(0, min(y, self.winfo_screenheight() - h - 40))
        self.geometry(f"{w}x{h}+{x}+{y}")
        if resizable:
            self.minsize(S(width * 0.8), S(height * 0.7))
        dark_titlebar(self)
        block_alt_menu(self)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        return self

    def close(self, result=None):
        self.result = result
        Tooltip.hide()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def confirm(master, title: str, message: str, ok: str = "Aceptar", cancel: str = "Cancelar",
            danger: bool = False) -> bool:
    dlg = Modal(master, title, 440, 200)
    body = tk.Frame(dlg, bg=T.BG, padx=S(24), pady=S(20))
    body.pack(fill="both", expand=True)
    tk.Label(body, text=title, font=F.h2, fg=T.TEXT, bg=T.BG, anchor="w").pack(fill="x")
    tk.Label(body, text=message, font=F.body, fg=T.TEXT_2, bg=T.BG, anchor="w", justify="left",
             wraplength=S(390)).pack(fill="x", pady=(S(8), 0))
    row = tk.Frame(body, bg=T.BG)
    row.pack(side="bottom", fill="x")
    Button(row, text=ok, variant="primary", command=lambda: dlg.close(True)).pack(side="right")
    Button(row, text=cancel, variant="ghost", command=lambda: dlg.close(False)).pack(side="right", padx=(0, S(8)))
    dlg.show()
    dlg.wait_window()
    return bool(dlg.result)
