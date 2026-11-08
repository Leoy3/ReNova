"""Tokens de diseño ReNova: colores, tipografías, escala DPI e íconos (Segoe MDL2)."""

from __future__ import annotations

import tkinter.font as tkfont

# ── Colores ────────────────────────────────────────────────────────────────────
BG = "#0F0C0C"
BG_2 = "#191514"
SURFACE = "#1C1818"
SURFACE_2 = "#241F1E"
SURFACE_HI = "#2D2727"
BORDER_SOFT = "#2A2524"
BORDER = "#3A3433"
TEXT = "#EEEAE8"
TEXT_2 = "#B0A9A7"
TEXT_3 = "#857E7C"
TEXT_4 = "#5A5452"
ACCENT = "#F44F62"
ACCENT_HI = "#FF6476"
ACCENT_LO = "#CF3F50"
ACCENT_INK = "#160A0B"
ACCENT_SOFT = "#3B1D22"
OK = "#51C672"
OK_SOFT = "#17291D"
WARN = "#E9AB2B"
WARN_SOFT = "#30260F"
ERR = "#FC4447"
ERR_SOFT = "#3A1718"
SELECT = "#5A2630"

# ── Escala ─────────────────────────────────────────────────────────────────────
SCALE = 1.0


def S(v: float) -> int:
    return int(round(v * SCALE))


# ── Íconos (Segoe MDL2 Assets, presente en Windows 10 y 11) ───────────────────
class G:
    LINK = ""
    PASTE = ""
    DOWNLOAD = ""
    CUT = ""
    FOLDER_OPEN = ""
    FOLDER = ""
    OPEN_LOCAL = ""
    PLAY = ""
    CANCEL = ""
    CLOSE = ""
    DELETE = ""
    REFRESH = ""
    SETTINGS = ""
    CHECK = ""
    COMPLETED = ""
    WARNING = ""
    INFO = ""
    VIDEO = ""
    AUDIO = ""
    MUTE = ""
    SEARCH = ""
    MORE = ""
    COPY = ""
    ADD = ""
    CHEVRON_DOWN = ""
    NEW_WINDOW = ""
    GLOBE = ""
    LIST = ""
    SELECT_ALL = ""
    SYNC = ""
    ERROR = ""
    MOVIE = ""
    PHOTO = ""
    MUSIC = ""
    UPLOAD = ""
    REMOVE = ""
    LIGHTNING = ""
    STOPWATCH = ""
    HISTORY = ""
    CUT_ALT = ""
    EDIT = ""


# ── Tipografías ────────────────────────────────────────────────────────────────
class F:
    body = small = small_b = body_b = title = h1 = h2 = h3 = mono = mono_sm = None
    input = label = icon = icon_sm = icon_md = icon_lg = icon_xl = brand = brand_i = None
    tiny_b = tc = None


def init(root) -> None:
    global SCALE
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        SCALE = 1.0
    families = set(tkfont.families(root))
    ui = "Segoe UI"
    semi = "Segoe UI Semibold" if "Segoe UI Semibold" in families else ui
    mono = "Cascadia Mono" if "Cascadia Mono" in families else "Consolas"
    icon = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"
    mk = lambda fam, size, **kw: tkfont.Font(root=root, family=fam, size=size, **kw)  # noqa: E731
    F.body = mk(ui, 10)
    F.body_b = mk(semi, 10)
    F.small = mk(ui, 9)
    F.small_b = mk(semi, 9)
    F.tiny_b = mk(semi, 8)
    F.title = mk(semi, 11)
    F.h1 = mk(semi, 17)
    F.h2 = mk(semi, 13)
    F.h3 = mk(semi, 11)
    F.mono = mk(mono, 9)
    F.mono_sm = mk(mono, 8)
    F.tc = mk(mono, 12, weight="bold")  # contador de tiempo: dígitos de ancho fijo (no hace saltar la UI)
    F.input = mk(ui, 11)
    F.label = mk(semi, 8)
    F.icon = mk(icon, 11)
    F.icon_sm = mk(icon, 9)
    F.icon_md = mk(icon, 13)
    F.icon_lg = mk(icon, 18)
    F.icon_xl = mk(icon, 34)
    F.brand = mk("Georgia", 18)
    F.brand_i = mk("Georgia", 18, slant="italic")


def ellipsize(font, text: str, max_w: int) -> str:
    if not text:
        return ""
    if max_w <= 0:
        return ""
    if font.measure(text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…" if lo > 0 else "…"


def ellipsize_middle(font, text: str, max_w: int) -> str:
    if font.measure(text) <= max_w or len(text) < 8:
        return ellipsize(font, text, max_w)
    keep_end = max(10, len(text) // 3)
    tail = text[-keep_end:]
    head = text[:-keep_end]
    while head and font.measure(head + "…" + tail) > max_w:
        head = head[:-1]
    if not head:
        return ellipsize(font, tail, max_w)
    return head + "…" + tail
