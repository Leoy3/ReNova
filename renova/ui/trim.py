"""Editor de recorte con reproductor (video + audio), línea de tiempo con miniaturas o forma
de onda, zoom, paso cuadro a cuadro, marcas I / O, imán al cabezal y loop de la selección.

`TrimEditor` es el diálogo que aparece antes de descargar (un solo tramo: inicio / fin).
La pestaña Recortar usa el editor de línea de tiempo de ui/editor.py."""

from __future__ import annotations

import bisect
import threading
import time
import tkinter as tk

from PIL import Image, ImageEnhance, ImageTk

from .. import core, media, playback
from . import theme as T
from .theme import F, G, S
from .widgets import Button, Modal, TextInput, Tooltip, round_rect

G_PREV, G_NEXT, G_REPEAT = "", "", ""
G_PLAY, G_PAUSE = "", ""
G_LEFT, G_RIGHT = "", ""
G_ZOOM_IN, G_ZOOM_OUT = "", ""
G_UNDO, G_REDO = "", ""
VIEW_BG = "#050404"


def fmt_tc(t: float) -> str:
    """Tiempo con centésimas: 1:02.35 / 1:02:03.35"""
    t = max(0.0, t)
    cs = int(round(t * 100))
    s, cs = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}" if h else f"{m}:{s:02d}.{cs:02d}"


# ── Visor ──────────────────────────────────────────────────────────────────────
class VideoView(tk.Canvas):
    def __init__(self, master, on_click=None, on_resize=None):
        super().__init__(master, bg=VIEW_BG, highlightthickness=0, bd=0, takefocus=0)
        self.aspect = 16 / 9
        self.photo = None
        self._img = None
        self._photo_size = None
        self._overlay = None
        self._audio_title = None
        self.on_resize = on_resize
        self._rjob = None
        self.bind("<Configure>", self._configured)
        if on_click:
            self.bind("<ButtonRelease-1>", lambda e: on_click())

    def _configured(self, _e):
        self._place()
        if self._rjob:
            self.after_cancel(self._rjob)
        self._rjob = self.after(180, lambda: self.on_resize and self.on_resize())

    def frame_size(self):
        cw, ch = max(64, self.winfo_width()), max(36, self.winfo_height())
        w = min(cw, ch * self.aspect)
        h = w / self.aspect
        return max(64, int(w) // 2 * 2), max(36, int(h) // 2 * 2)

    def set_audio_mode(self, title: str | None):
        self._audio_title = title
        self._place()

    def show_rgb(self, w, h, data):
        img = Image.frombuffer("RGB", (w, h), data, "raw", "RGB", 0, 1)
        if self._photo_size != (w, h):
            self.photo = ImageTk.PhotoImage(img)
            self._photo_size = (w, h)
        else:
            self.photo.paste(img)
        self._show()

    def show_pil(self, img):
        w, h = self.frame_size()
        self.photo = ImageTk.PhotoImage(img.resize((w, h), Image.BILINEAR))
        self._photo_size = (w, h)
        self._show()

    def _show(self):
        if self._img is None:
            self._img = self.create_image(0, 0, image=self.photo, anchor="center")
        else:
            self.itemconfigure(self._img, image=self.photo)
        self._place()

    def _place(self):
        if self._img is not None:
            self.coords(self._img, self.winfo_width() / 2, self.winfo_height() / 2)
        self.delete("audio")
        if self._audio_title is not None:
            w, h = self.winfo_width(), self.winfo_height()
            r = S(44)
            round_rect(self, w / 2 - r, h / 2 - r - S(16), w / 2 + r, h / 2 + r - S(16), r, T.SURFACE, T.BORDER_SOFT,
                       VIEW_BG, tags=("audio",))
            self.create_text(w / 2, h / 2 - S(14), text=G.MUSIC, font=F.icon_xl, fill=T.ACCENT, tags=("audio",))
            self.create_text(w / 2, h / 2 + r + S(8), text=T.ellipsize(F.body_b, self._audio_title, w - S(40)),
                             font=F.body_b, fill=T.TEXT_2, tags=("audio",))
        self._draw_overlay()

    def set_overlay(self, text: str | None, progress: float | None = None, kind: str = "info"):
        self._overlay = (text, progress, kind) if text else None
        self._draw_overlay()

    def _draw_overlay(self):
        self.delete("ov")
        if not self._overlay:
            return
        text, progress, kind = self._overlay
        w, h = self.winfo_width(), self.winfo_height()
        color = T.WARN if kind == "warn" else T.TEXT
        bw = max(F.small_b.measure(text) + S(28), S(220))
        x1, y2 = (w - bw) / 2, h - S(16)
        y1 = y2 - (S(40) if progress is not None else S(30))
        round_rect(self, x1, y1, x1 + bw, y2, S(8), "#151111", T.BORDER, VIEW_BG, tags=("ov",))
        self.create_text(w / 2, y1 + S(15), text=text, font=F.small_b, fill=color, tags=("ov",))
        if progress is not None:
            px1, px2 = x1 + S(14), x1 + bw - S(14)
            self.create_rectangle(px1, y2 - S(10), px2, y2 - S(7), fill=T.SURFACE_HI, width=0, tags=("ov",))
            self.create_rectangle(px1, y2 - S(10), px1 + (px2 - px1) * max(0.02, progress), y2 - S(7),
                                  fill=T.ACCENT, width=0, tags=("ov",))


# ── Línea de tiempo ────────────────────────────────────────────────────────────
class TrimTimeline(tk.Canvas):
    """Alt + rueda = zoom en el cursor · rueda (o Ctrl + rueda) y la barra de abajo = desplazar.
    Las miniaturas se repiten a su tamaño real (no se estiran) y al hacer zoom se piden más."""

    SY1, SH = 18, 52
    SB_Y, SB_H = 97, 12  # barra de desplazamiento (sólo con zoom)

    def __init__(self, master, duration, start, end, on_seek, on_handle, on_view=None, bg=T.BG):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, height=S(114), takefocus=0)
        self.duration = max(0.1, duration)
        self.start, self.end, self.pos = start, end, 0.0
        self.v0, self.v1 = 0.0, self.duration
        self.on_seek, self.on_handle, self.on_view = on_seek, on_handle, on_view
        self.snap_t = None         # punto donde se pegó la manija o el cabezal (se dibuja en amarillo)
        self.loop = False
        self._full = None          # forma de onda de todo el archivo (sólo audio)
        self._thumbs: list = []    # [(segundo, PIL)] ordenadas, a la altura de la tira
        self._thumb_ver = 0
        self.aspect = 16 / 9
        self._bright = None
        self._dark_photo = None
        self._sel_photo = None
        self._drag = None
        self._hover = None
        self._built = None
        self._view_job = None
        self._wheel_target = self._wheel
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))

    # geometría
    @property
    def x0(self):
        return S(20)

    @property
    def x1(self):
        return self.winfo_width() - S(20)

    @property
    def zoomed(self):
        return self.v1 - self.v0 < self.duration - 1e-6

    def t2x(self, t):
        return self.x0 + (self.x1 - self.x0) * ((t - self.v0) / max(1e-6, self.v1 - self.v0))

    def x2t(self, x):
        t = self.v0 + (x - self.x0) / max(1, self.x1 - self.x0) * (self.v1 - self.v0)
        return max(0.0, min(self.duration, t))

    # API
    def set_duration(self, d):
        self.duration = max(0.1, d)
        self.v0, self.v1 = 0.0, self.duration
        self._full = None
        self._thumbs = []
        self._draw()

    def set_strip(self, img):
        """Forma de onda (archivos de sólo audio)."""
        self._full = img
        self._draw()

    def add_thumbs(self, items):
        """Suma miniaturas (segundo, imagen) a las que ya hay."""
        have = {round(t, 2) for t, _ in self._thumbs}
        new = [(t, im) for t, im in items if round(t, 2) not in have]
        if not new:
            return
        self._thumbs = sorted(self._thumbs + new, key=lambda x: x[0])
        self._thumb_ver += 1
        self._draw()

    def tile_width(self):
        return max(8, int(round(S(self.SH) * self.aspect)))

    def missing_times(self):
        """Segundos de las miniaturas que faltan para la vista actual (una por casillero)."""
        tw = self.tile_width()
        w = self.x1 - self.x0
        span = (self.v1 - self.v0) / max(1, w) * tw  # segundos que cubre un casillero
        times = [t for t, _ in self._thumbs]
        out = []
        x = 0
        while x < w:
            tc = self.x2t(self.x0 + x + tw / 2)
            i = bisect.bisect_left(times, tc)
            near = min((abs(times[j] - tc) for j in (i - 1, i) if 0 <= j < len(times)), default=1e9)
            if near > span * 0.6:
                out.append(round(tc, 3))
            x += tw
        return out

    def set_range(self, s, e):
        self.start, self.end = s, e
        self._draw()

    def redraw(self):
        self._draw()

    def set_pos(self, t):
        self.pos = t
        self._draw_playhead()

    def set_view(self, v0, v1, notify=True):
        span = max(min(2.0, self.duration), min(self.duration, v1 - v0))
        v0 = max(0.0, min(self.duration - span, v0))
        if abs(v0 - self.v0) < 1e-6 and abs(v0 + span - self.v1) < 1e-6:
            return
        self.v0, self.v1 = v0, v0 + span
        self._draw()
        if notify and self.on_view:
            if self._view_job:
                self.after_cancel(self._view_job)
            self._view_job = self.after(350, lambda: self.on_view(self.v0, self.v1))

    def zoom_to(self, t0, t1):
        pad = max(0.4, (t1 - t0) * 0.2)
        self.set_view(t0 - pad, t1 + pad)

    def reset_view(self):
        self.set_view(0.0, self.duration)

    def follow(self, t):
        if self.zoomed and not (self.v0 <= t <= self.v1 - (self.v1 - self.v0) * 0.03):
            span = self.v1 - self.v0
            self.set_view(t - span * 0.1, t + span * 0.9)

    def _wheel(self, e):
        if not e.state & 0x20000:  # sin Alt (rueda sola, Ctrl o Shift): desplazar hacia los costados
            span = self.v1 - self.v0
            d = span * 0.15 * (e.delta / 120)
            self.set_view(self.v0 - d, self.v1 - d)
            return
        x = self.winfo_pointerx() - self.winfo_rootx()  # Alt + rueda: zoom en el cursor (como Premiere)
        anchor = self.x2t(x)
        frac = (anchor - self.v0) / max(1e-6, self.v1 - self.v0)
        span = (self.v1 - self.v0) * (0.7 if e.delta > 0 else 1 / 0.7)
        self.set_view(anchor - span * frac, anchor - span * frac + span)

    # interacción
    def _scroll_geom(self):
        """Barra: (inicio, fin, espacio libre, segundos recorribles). El agarre tiene un ancho mínimo
        para poder tomarlo aunque el video sea muy largo."""
        w = max(1, self.x1 - self.x0)
        span = self.v1 - self.v0
        tw = min(w, max(S(64), w * span / self.duration))
        free = max(1.0, w - tw)
        rng = max(1e-6, self.duration - span)
        a = self.x0 + free * min(1.0, self.v0 / rng)
        return a, a + tw, free, rng

    def _scroll_thumb(self):
        a, b, _free, _rng = self._scroll_geom()
        return a, b

    def _zone(self, x, y):
        if self.zoomed and S(self.SB_Y) - S(7) <= y <= S(self.SB_Y + self.SB_H) + S(4):
            a, b = self._scroll_thumb()
            return "scroll" if a <= x <= b else "scroll_track"
        sy1, sy2 = S(self.SY1) - S(4), S(self.SY1 + self.SH) + S(4)
        if sy1 <= y <= sy2:
            xs, xe = self.t2x(self.start), self.t2x(self.end)
            hw = S(14)
            in_s = xs - hw - S(3) <= x <= xs + S(4)
            in_e = xe - S(4) <= x <= xe + hw + S(3)
            if in_s and in_e:
                return "start" if x < (xs + xe) / 2 else "end"
            if in_s:
                return "start"
            if in_e:
                return "end"
        return "seek"

    def _motion(self, e):
        self._set_hover(self._zone(e.x, e.y))

    def _set_hover(self, z):
        if z != self._hover:
            self._hover = z
            resize = z in ("start", "end", "scroll")
            self.configure(cursor="sb_h_double_arrow" if resize else ("hand2" if z else "arrow"))
            self._draw()

    def _press(self, e):
        self._drag = self._zone(e.x, e.y)
        if self._drag == "scroll_track":  # clic en la barra: lleva el agarre ahí y sigue arrastrando
            a, b, free, rng = self._scroll_geom()
            span = self.v1 - self.v0
            nv = (e.x - (b - a) / 2 - self.x0) / free * rng
            self.set_view(nv, nv + span)
            self._drag = "scroll"
        if self._drag == "scroll":
            self._scroll_from = (e.x, self.v0)
            return
        self._apply(e.x, final=False)

    def _move(self, e):
        if self._drag:
            self._apply(e.x, final=False)

    def _release(self, e):
        if self._drag:
            self._apply(e.x, final=True)
            self._drag = None
            self.snap_t = None
            self._draw()

    def _apply(self, x, final):
        if self._drag == "scroll":
            x0, v0 = self._scroll_from
            _a, _b, free, rng = self._scroll_geom()
            span = self.v1 - self.v0
            nv = v0 + (x - x0) / free * rng
            self.set_view(nv, nv + span)
            return
        t = self.x2t(x)
        if self._drag == "seek":
            self.pos = t
            self._draw_playhead()
            self.on_seek(t, final)
        elif self._drag in ("start", "end"):
            self.on_handle(self._drag, t, final)

    # dibujo
    def _build(self):
        w = self.x1 - self.x0
        key = (w, self.v0, self.v1, id(self._full), self._thumb_ver)
        if key == self._built:
            return
        self._built = key
        h = S(self.SH)
        img = None
        if self._thumbs and w >= 20:
            # casilleros del tamaño real de la miniatura; cada uno muestra la más cercana a su tiempo
            tw = self.tile_width()
            img = Image.new("RGB", (w, h), (20, 16, 16))
            times = [t for t, _ in self._thumbs]
            x = 0
            while x < w:
                tc = self.x2t(self.x0 + x + tw / 2)
                i = bisect.bisect_left(times, tc)
                j = min((k for k in (i - 1, i) if 0 <= k < len(times)), key=lambda k: abs(times[k] - tc))
                th = self._thumbs[j][1]
                if th.size != (tw, h):
                    th = th.resize((tw, h), Image.BILINEAR)
                img.paste(th, (x, 0))
                x += tw
        elif self._full is not None and w >= 20:
            fw = self._full.width
            a = int(fw * self.v0 / self.duration)
            b = max(a + 1, int(fw * self.v1 / self.duration))
            img = self._full.crop((a, 0, b, self._full.height)).resize((w, h), Image.BILINEAR)
        if img is None:
            self._bright = self._dark_photo = None
            return
        self._bright = img
        self._dark_photo = ImageTk.PhotoImage(ImageEnhance.Brightness(img).enhance(0.3))

    def _clip(self, x):
        return max(self.x0, min(self.x1, x))

    def _draw(self):
        if self.winfo_width() < 60:
            return
        self._build()
        self.delete("all")
        bg = self["bg"]
        x0, x1 = self.x0, self.x1
        sy1, sy2 = S(self.SY1), S(self.SY1 + self.SH)
        xs, xe = self.t2x(self.start), self.t2x(self.end)
        cxs, cxe = self._clip(xs), self._clip(xe)
        # tira (oscurecida fuera del rango que se conserva)
        if self._dark_photo:
            self.create_image(x0, sy1, image=self._dark_photo, anchor="nw")
            if cxe - cxs >= 1:
                self._sel_photo = ImageTk.PhotoImage(self._bright.crop((int(cxs - x0), 0, int(cxe - x0),
                                                                        self._bright.height)))
                self.create_image(cxs, sy1, image=self._sel_photo, anchor="nw")
        else:
            self.create_rectangle(x0, sy1, x1, sy2, fill=T.SURFACE, width=0)
            if cxe > cxs:
                self.create_rectangle(cxs, sy1, cxe, sy2, fill=T.SURFACE_HI, width=0)
        # marco del rango que se conserva
        bw = S(3)
        if cxe > cxs:
            self.create_rectangle(cxs, sy1 - bw, cxe, sy1, fill=T.ACCENT, width=0)
            self.create_rectangle(cxs, sy2, cxe, sy2 + bw, fill=T.ACCENT, width=0)
        hw = S(14)
        for name, hx1, hx2 in (("start", xs - hw, xs), ("end", xe, xe + hw)):
            if hx2 < x0 - hw or hx1 > x1 + hw:
                continue
            active = self._hover == name or self._drag == name
            fill = T.ACCENT_HI if active else T.ACCENT
            round_rect(self, hx1, sy1 - bw, hx2, sy2 + bw, S(5), fill, None, bg)
            ix = hx2 - S(5) if name == "start" else hx1
            self.create_rectangle(ix, sy1 - bw, ix + S(5), sy2 + bw, fill=fill, width=0)
            gx = (hx1 + hx2) / 2
            for dx in (-S(2), S(2)):
                self.create_line(gx + dx, sy1 + S(14), gx + dx, sy2 - S(14), fill=T.ACCENT_INK, width=1)
        # escala de tiempo
        width = x1 - x0
        span = self.v1 - self.v0
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
                     if width / (span / s) >= S(84)), 7200)
        lab = fmt_tc if step < 1 else core.fmt_duration
        ly = sy2 + S(22)
        t = (int(self.v0 / step) + (0 if self.v0 % step < 1e-9 else 1)) * step
        while t <= self.v1 + 1e-6:
            x = self.t2x(t)
            self.create_line(x, sy2 + S(7), x, sy2 + S(12), fill=T.TEXT_4)
            if x - x0 > S(52) and x1 - x > S(60):
                self.create_text(x, ly, text=lab(t), font=F.mono_sm, fill=T.TEXT_3)
            t = round(t + step, 6)
        self.create_text(x0, ly, text=lab(self.v0), font=F.mono_sm, fill=T.TEXT_3, anchor="w")
        self.create_text(x1, ly, text=lab(self.v1), font=F.mono_sm, fill=T.TEXT_3, anchor="e")
        if self.loop and cxe > cxs:  # el loop repite sólo lo elegido
            self.create_rectangle(cxs, sy2 + S(3), cxe, sy2 + S(6), fill=T.OK, width=0)
        if self.zoomed:  # barra para desplazarse por el archivo con zoom
            my, mh = S(self.SB_Y), S(self.SB_H)
            round_rect(self, x0, my, x1, my + mh, mh // 2, T.SURFACE_HI, None, bg)
            a, b = self._scroll_thumb()
            hov = self._hover in ("scroll", "scroll_track") or self._drag == "scroll"
            round_rect(self, a, my, b, my + mh, mh // 2, T.TEXT_2 if hov else T.TEXT_3, None, T.SURFACE_HI)
            cx = (a + b) / 2
            for dx in (-S(4), 0, S(4)):  # agarre
                self.create_line(cx + dx, my + S(3), cx + dx, my + mh - S(3), fill=T.SURFACE_HI)
        self._draw_playhead()

    def _draw_playhead(self):
        self.delete("ph")
        if self.winfo_width() < 60:
            return
        sy1, sy2 = S(self.SY1), S(self.SY1 + self.SH)
        x = self.t2x(self.pos)
        if self.x0 - S(2) <= x <= self.x1 + S(2):
            self.create_line(x, sy1 - S(8), x, sy2 + S(6), fill=T.TEXT, width=S(2), tags=("ph",))
            k = S(6)
            self.create_polygon(x - k, sy1 - S(16), x + k, sy1 - S(16), x + k, sy1 - S(10), x, sy1 - S(5),
                                x - k, sy1 - S(10), fill=T.TEXT, outline="", tags=("ph",))
        if self.snap_t is not None:  # imán: línea amarilla con flechas (encima del cabezal)
            sx = self.t2x(self.snap_t)
            if self.x0 - S(2) <= sx <= self.x1 + S(2):
                k = S(5)
                self.create_line(sx, sy1 - S(4), sx, sy2 + S(4), fill=T.WARN, width=S(2), tags=("ph",))
                self.create_polygon(sx - k, sy2 + S(11), sx + k, sy2 + S(11), sx, sy2 + S(4), fill=T.WARN,
                                    outline="", tags=("ph",))


# ── Editor ─────────────────────────────────────────────────────────────────────
class TrimEditor(tk.Frame):
    def __init__(self, master, app, duration: float, start: float = 0.0, end: float | None = None,
                 mode: str = "range", on_change=None, bg=T.BG, padx: int = 24):
        super().__init__(master, bg=bg)
        self.app, self.mode, self.on_change = app, mode, on_change
        self.duration = max(0.1, float(duration or 0) or 1.0)
        self.start = start
        self.end = self.duration if end is None else end
        self.pos = start
        self.fps = 30.0
        self.snap = bool(app.settings.get("snap"))
        self.loop = bool(app.settings.get("loop"))
        self.path = None
        self.has_audio = False
        self.audio_only = False
        self.playing = False
        self.audio = self.video = None
        self.until = None
        self.play_queue: list = []
        self._wall0 = self._t0 = 0.0
        self._waiting = False
        self._tick_job = None
        self.grabber = None
        self._remote_src = None
        self._remote_job = None
        self._strip_token = None
        self._strip_cancel = threading.Event()
        self._padx = S(padx)
        self._build()
        top = self.winfo_toplevel()
        self._bound = []
        for seq, fn in self._shortcuts():
            top.bind(seq, lambda e, fn=fn: self._key(e, fn), add="+")
            self._bound.append(seq)

    def destroy(self):
        self.stop()
        try:
            top = self.winfo_toplevel()
            for seq in self._bound:  # sólo este editor usa estos atajos en la ventana
                top.unbind(seq)
        except tk.TclError:
            pass
        super().destroy()

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build(self):
        px = self._padx

        marks = tk.Frame(self, bg=self["bg"], padx=px)
        marks.pack(side="bottom", fill="x", pady=(S(4), S(10)))
        self.inputs = {}
        for key, label, mark_txt in (("start", "Inicio", "Marcar inicio  I"), ("end", "Fin", "Marcar fin  O")):
            box = tk.Frame(marks, bg=self["bg"])
            box.pack(side="left", padx=(0, S(20)))
            tk.Label(box, text=label, font=F.small_b, fg=T.TEXT_2, bg=self["bg"], anchor="w").pack(fill="x")
            row = tk.Frame(box, bg=self["bg"])
            row.pack(fill="x", pady=(S(4), 0))
            Button(row, icon=G_LEFT, variant="outline", height=34, icon_font=F.icon_sm,
                   command=lambda k=key: self._nudge_mark(k, -1), tooltip="Un cuadro antes").pack(side="left")
            ti = TextInput(row, height=34, font=F.mono, bg=self["bg"], on_submit=lambda k=key: self._typed(k, True))
            ti.configure(width=S(112))
            ti.entry.configure(justify="center")
            ti.entry.bind("<FocusOut>", lambda e, k=key: self._typed(k, False), add="+")
            ti.pack(side="left", padx=S(4))
            Button(row, icon=G_RIGHT, variant="outline", height=34, icon_font=F.icon_sm,
                   command=lambda k=key: self._nudge_mark(k, 1), tooltip="Un cuadro después").pack(side="left")
            Button(row, text=mark_txt, variant="secondary", height=34, padx=12, font=F.small_b,
                   command=lambda k=key: self.mark(k),
                   tooltip="Usar el cuadro que se está viendo").pack(side="left", padx=(S(8), 0))
            self.inputs[key] = ti
        from .editor import ToolToggle, VolumeControl, draw_magnet
        sbox = tk.Frame(marks, bg=self["bg"])
        sbox.pack(side="left")
        tk.Label(sbox, text=" ", font=F.small_b, bg=self["bg"]).pack(fill="x")
        self.snap_btn = ToolToggle(sbox, "Imán", draw_icon=draw_magnet, value=self.snap, command=self.set_snap,
                                   height=34, tooltip="Las manijas de inicio y fin se pegan al cabezal (S)")
        self.snap_btn.pack(side="left", pady=(S(4), 0))
        dbox = tk.Frame(marks, bg=self["bg"])
        tk.Label(dbox, text="Duración del recorte", font=F.small_b, fg=T.TEXT_2,
                 bg=self["bg"], anchor="e").pack(fill="x")
        self.dur_lbl = tk.Label(dbox, text="", font=F.h2, fg=T.ACCENT, bg=self["bg"], anchor="e")
        self.dur_lbl.pack(fill="x", pady=(S(2), 0))
        dbox.pack(side="right")

        tr = tk.Frame(self, bg=self["bg"], padx=px)
        tr.pack(side="bottom", fill="x", pady=(S(2), S(6)))
        tr.columnconfigure(0, weight=1, uniform="tr")
        tr.columnconfigure(2, weight=1, uniform="tr")
        left = tk.Frame(tr, bg=self["bg"])
        left.grid(row=0, column=0, sticky="w")
        self.time_lbl = tk.Label(left, text="", font=F.mono, fg=T.TEXT, bg=self["bg"], anchor="w",
                                 width=23)  # ancho fijo: el contador no mueve el resto al cambiar
        self.time_lbl.pack(side="left")
        self.vol = VolumeControl(left, self.app.settings, self._volume_changed)
        self.vol.pack(side="left", padx=(S(12), 0))
        mid = tk.Frame(tr, bg=self["bg"])
        mid.grid(row=0, column=1)
        Button(mid, icon=G_PREV, variant="ghost", height=36, command=lambda: self.seek(self.start),
               tooltip="Ir al inicio (Inicio)").pack(side="left")
        Button(mid, icon=G_LEFT, variant="ghost", height=36, command=lambda: self.step(-1),
               tooltip="Un cuadro atrás (←)").pack(side="left", padx=(S(4), 0))
        self.play_btn = Button(mid, icon=G_PLAY, variant="primary", height=42, width=54, radius=21,
                               icon_font=F.icon_md, command=self.toggle_play, tooltip="Reproducir / pausa (Espacio)")
        self.play_btn.pack(side="left", padx=S(10))
        Button(mid, icon=G_RIGHT, variant="ghost", height=36, command=lambda: self.step(1),
               tooltip="Un cuadro adelante (→)").pack(side="left", padx=(0, S(4)))
        Button(mid, icon=G_NEXT, variant="ghost", height=36, command=lambda: self.seek(self.end, end_frame=True),
               tooltip="Ir al final (Fin)").pack(side="left")
        right = tk.Frame(tr, bg=self["bg"])
        right.grid(row=0, column=2, sticky="e")
        self.zoom_btn = Button(right, icon=G_ZOOM_IN, variant="ghost", height=34, icon_font=F.icon_sm,
                               command=self.toggle_zoom,
                               tooltip="Zoom a la selección / ver todo (también con Alt + rueda del mouse "
                                       "sobre la línea de tiempo; la rueda o la barra de abajo para "
                                       "desplazarte)")
        self.zoom_btn.pack(side="right", padx=(S(4), 0))
        Button(right, text="Reproducir selección", icon=G_REPEAT, variant="secondary", height=34, padx=12,
               font=F.small_b, icon_font=F.icon_sm, command=self.play_result,
               tooltip="Reproduce sólo el tramo elegido").pack(side="right")
        self.loop_btn = ToolToggle(right, "Loop", icon=G_REPEAT, value=self.loop, command=self.set_loop,
                                   tooltip="Repetir sólo la selección al reproducir")
        self.loop_btn.pack(side="right", padx=(0, S(8)))

        self.timeline = TrimTimeline(self, self.duration, self.start, self.end, self._on_seek, self._on_handle,
                                     on_view=self._view_changed, bg=self["bg"])
        self.timeline.loop = self.loop
        self.timeline.pack(side="bottom", fill="x", padx=max(0, px - S(20)), pady=(S(2), S(2)))

        self.view = VideoView(self, on_click=self.toggle_play, on_resize=self._view_resized)
        self.view.pack(fill="both", expand=True, padx=px)
        self._sync_fields()
        self._on_pos()

    def _volume_changed(self, v):
        if self.audio is not None:
            self.audio.set_volume(v)

    def set_snap(self, v):
        self.snap = bool(v)
        self.snap_btn.set(self.snap)
        self.app.settings.set("snap", self.snap)

    def set_loop(self, v):
        self.loop = bool(v)
        self.loop_btn.set(self.loop)
        self.timeline.loop = self.loop
        self.timeline.redraw()
        self.app.settings.set("loop", self.loop)

    def _snap_to(self, t, points):
        """Con el imán prendido, pega t al punto más cercano si está a menos de 8 px."""
        if not self.snap:
            return t, None
        tl = self.timeline
        best = min(points, key=lambda p: abs(p - t))
        if abs(tl.t2x(best) - tl.t2x(t)) <= S(8):
            return best, best
        return t, None

    def _shortcuts(self):
        keys = [("<space>", self.toggle_play), ("<Key-i>", lambda: self.mark("start")),
                ("<Key-I>", lambda: self.mark("start")), ("<Key-o>", lambda: self.mark("end")),
                ("<Key-O>", lambda: self.mark("end")), ("<Left>", lambda: self.step(-1)),
                ("<Right>", lambda: self.step(1)), ("<Shift-Left>", lambda: self.seek(self.pos - 1)),
                ("<Shift-Right>", lambda: self.seek(self.pos + 1)), ("<Home>", lambda: self.seek(self.start)),
                ("<End>", lambda: self.seek(self.end, end_frame=True))]
        keys += [("<Key-s>", lambda: self.set_snap(not self.snap)),
                 ("<Key-S>", lambda: self.set_snap(not self.snap))]
        return keys

    def _key(self, e, fn):
        try:
            if not self.winfo_ismapped() or isinstance(self.focus_get(), tk.Entry):
                return None
            if e.widget.winfo_toplevel() is not self.winfo_toplevel():
                return None
        except (KeyError, tk.TclError):
            return None
        fn()
        return "break"

    # ── Fuente ─────────────────────────────────────────────────────────────
    def set_overlay(self, text, progress=None, kind="info"):
        self.view.set_overlay(text, progress, kind)

    def set_remote_source(self, url, headers, aspect=None):
        self._remote_src = (url, headers)
        if aspect:
            self.view.aspect = aspect
        if not self.path:
            self._remote_frame(self.pos)

    def set_duration(self, d, reset_range=True):
        self.duration = max(0.1, float(d))
        if reset_range:
            self.start, self.end = 0.0, self.duration
        self.end = min(self.end, self.duration)
        self.timeline.set_duration(self.duration)
        self.timeline.set_range(self.start, self.end)
        self._sync_fields()

    def set_source(self, path, fps, aspect, has_audio, audio_only=False, title=""):
        self.path = path
        self.fps = fps or 30.0
        self.has_audio = has_audio
        self.audio_only = audio_only
        self.view.aspect = aspect or 16 / 9
        self.view.set_overlay(None)
        self.view.set_audio_mode(title if audio_only else None)
        if not audio_only:
            self.grabber = playback.FrameGrabber(path, lambda t, w, h, d: self.app.bus.call(self._grabbed, t, w, h, d))
        self.seek(self.pos)
        self._make_strip(None)
        if self.timeline.zoomed:
            self._view_changed(self.timeline.v0, self.timeline.v1)

    def _make_strip(self, window):
        path, dur = self.path, self.duration
        tl = self.timeline
        token = object()
        self._strip_token = token
        if self.audio_only:
            width = max(400, tl.winfo_width())

            def wave():
                img = media.waveform(path, None, None, width, 100, color=T.TEXT_2, bg=T.SURFACE)
                if img is not None:
                    self.app.bus.call(lambda: self.winfo_exists() and tl.set_strip(img))
            if window is None:
                threading.Thread(target=wave, daemon=True, name="strip").start()
            return
        tl.aspect = self.view.aspect or 16 / 9
        h, aspect = S(tl.SH), tl.aspect
        cancel = self._strip_cancel

        if window is None:  # primera pasada: todo el archivo, en un solo proceso
            def full():
                playback.source_thumbs(
                    path, dur, h, aspect,
                    lambda iv, imgs: self.app.bus.call(
                        lambda: self.winfo_exists() and tl.add_thumbs([(i * iv, im) for i, im in enumerate(imgs)])),
                    cancel)
            threading.Thread(target=full, daemon=True, name="strip").start()
            return
        times = tl.missing_times()[:40]  # con zoom: sólo las que faltan en la vista
        if not times:
            return

        def part():
            items = playback.thumbs_at(path, times, h, aspect, cancel)
            if items and self._strip_token is token:
                self.app.bus.call(lambda: self.winfo_exists() and tl.add_thumbs(items))
        threading.Thread(target=part, daemon=True, name="strip").start()

    def _grabbed(self, t, w, h, data):
        if self.winfo_exists() and not self.playing:
            self.view.show_rgb(w, h, data)

    def _remote_frame(self, t):
        if self.path or not self._remote_src:
            return
        if self._remote_job:
            self.after_cancel(self._remote_job)

        def go():
            self._remote_job = None
            url, headers = self._remote_src
            w, _h = self.view.frame_size()

            def run():
                img = media.grab_frame(url, t, width=w, headers=headers, timeout=20)
                if img is not None:
                    self.app.bus.call(lambda: self.winfo_exists() and not self.path and self.view.show_pil(img))
            threading.Thread(target=run, daemon=True).start()
        self._remote_job = self.after(250, go)

    # ── Zoom ───────────────────────────────────────────────────────────────
    def toggle_zoom(self):
        if self.timeline.zoomed:
            self.timeline.reset_view()
        else:
            self.timeline.zoom_to(self.start, self.end)
        self._update_zoom_btn()

    def _update_zoom_btn(self):
        self.zoom_btn.configure_button(icon=G_ZOOM_OUT if self.timeline.zoomed else G_ZOOM_IN)

    def _view_changed(self, v0, v1):
        self._update_zoom_btn()
        if self.path and self.timeline.zoomed:
            self._make_strip((v0, v1))

    # ── Reproducción ───────────────────────────────────────────────────────
    def _clock(self):
        if self.audio is not None and not self.audio.eof:
            c = self.audio.clock()
            if c is not None:
                return c
        return self._t0 + (time.monotonic() - self._wall0)

    def toggle_play(self):
        if self.playing:
            self.pause()
        elif self.loop:
            t = self.pos if self.start <= self.pos < self.end - 0.05 else self.start
            self.play(t, until=self.end)
        else:
            self.play()

    def play_result(self):
        self.play(self.start, until=self.end)

    def play(self, from_t=None, until=None, queue=None):
        if not self.path:
            self.app.toast.show("El reproductor todavía se está preparando…", "info", ms=1800)
            return
        self._stop_streams()
        self.play_queue = list(queue or [])
        t = self.pos if from_t is None else from_t
        if t >= self.duration - 0.05:
            t = 0.0
        self.video = None
        if not self.audio_only:
            w, h = self.view.frame_size()
            self.video = playback.VideoStream(self.path, t, w, h, min(self.fps, 30.0))
        self.audio = None
        if self.has_audio:
            a = playback.AudioStream(self.path, t, volume=self.vol.effective)
            self.audio = a if a.ok else None
        self._t0 = t
        self.until = until
        self.playing = True
        self._waiting = True
        self._wait_since = time.monotonic()
        self.play_btn.configure_button(icon=G_PAUSE)
        self._tick()

    def _tick(self):
        self._tick_job = None
        if not self.playing or not self.winfo_exists():
            return
        v = self.video
        if self._waiting:
            ready_v = v is None or bool(v.frames) or v.eof
            ready_a = self.audio is None or self.audio._primed.is_set()
            if (ready_v and ready_a) or time.monotonic() - self._wait_since > 1.5:
                self._waiting = False
                if self.audio is not None:
                    self.audio.resume()
                self._wall0 = time.monotonic()
            else:
                self._tick_job = self.after(8, self._tick)
                return
        clock = self._clock()
        if v is not None:
            frame = None
            while v.frames and v.frames[0][0] <= clock + 0.5 / v.fps:
                frame = v.frames.popleft()
            if frame is not None:
                self.view.show_rgb(v.w, v.h, frame[1])
        self.pos = min(clock, self.duration)
        self._on_pos()
        until = self.end if (self.loop and self.until is not None) else self.until
        if until is not None and clock >= until:
            if self.play_queue:
                nxt = self.play_queue.pop(0)
                self.play(nxt[0], until=nxt[1], queue=self.play_queue)
            elif self.loop:
                self.play(self.start, until=self.end)  # vuelve a empezar la selección
            else:
                self.pause(at=until)
            return
        v_done = v is None or (v.eof and not v.frames)
        a_done = self.audio is None or self.audio.eof
        if clock >= self.duration or (v_done and a_done and (v is not None or self.audio is not None)):
            self.pause(at=min(clock, self.duration))
            return
        self._tick_job = self.after(8, self._tick)

    def pause(self, at=None):
        if self.playing:
            pos = self._clock() if at is None else at
            self._stop_streams()
            self.playing = False
            self.play_queue = []
            self.play_btn.configure_button(icon=G_PLAY)
            self.pos = max(0.0, min(self.duration, pos))
            self._on_pos()
            self._request_frame(self.pos)

    def _stop_streams(self):
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        for s in (self.video, self.audio):
            if s is not None:
                s.stop()
        self.video = self.audio = None

    def _view_resized(self):
        if self.playing:
            self.play(self._clock(), self.until, self.play_queue)
        else:
            self._request_frame(self.pos)

    def stop(self):
        self.playing = False
        self._stop_streams()
        self._strip_cancel.set()
        if self.grabber:
            self.grabber.close()
            self.grabber = None
        Tooltip.hide()

    # ── Navegación ─────────────────────────────────────────────────────────
    def _request_frame(self, t, end_frame=False):
        if end_frame:
            t = max(self.start, t - 1.0 / self.fps)
        if self.grabber:
            w, h = self.view.frame_size()
            self.grabber.request(min(t, max(0.0, self.duration - 0.5 / self.fps)), w, h)
        elif not self.path:
            self._remote_frame(t)

    def seek(self, t, end_frame=False):
        if self.playing:
            self.pause()
        self.pos = max(0.0, min(self.duration, t))
        self._on_pos()
        self._request_frame(self.pos, end_frame)

    def step(self, frames):
        f = 1.0 / self.fps
        self.seek(round(self.pos / f) * f + frames * f)

    def _on_seek(self, t, final):
        if self.playing:
            self.pause()
        t, self.timeline.snap_t = self._snap_to(t, [self.start, self.end])
        if final:
            self.timeline.snap_t = None
        self.pos = t
        self._on_pos()
        if self.grabber or final:
            self._request_frame(t)

    def _on_handle(self, which, t, final):
        """Arrastre de una manija: el cabezal queda quieto (para poder pegarse a él) y el visor
        muestra el cuadro de la manija; al soltar vuelve al cuadro del cabezal."""
        if self.playing:
            self.pause()
        t, self.timeline.snap_t = self._snap_to(t, [self.pos])
        self._set_mark(which, t)
        if final:
            self.timeline.snap_t = None
            self.timeline.redraw()
            self._request_frame(self.pos)
        elif self.grabber:
            target = self.start if which == "start" else self.end
            self._request_frame(target, end_frame=(which == "end"))

    # ── Marcas ─────────────────────────────────────────────────────────────
    def _set_mark(self, which, t):
        gap = min(0.2, self.duration / 4)
        t = max(0.0, min(self.duration, t))
        if which == "start":
            self.start = min(t, self.end - gap)
        else:
            self.end = max(t, self.start + gap)
        self.timeline.set_range(self.start, self.end)
        self._sync_fields()
        self._changed()

    def mark(self, which):
        if self.playing:
            self.pause()
        t = self.pos + (1.0 / self.fps if which == "end" else 0.0)  # el cuadro visible queda incluido
        if which == "start" and t >= self.end - 0.1:
            self.end = self.duration
        if which == "end" and t <= self.start + 0.1:
            self.start = 0.0
        self._set_mark(which, t)

    def _nudge_mark(self, which, frames):
        cur = self.start if which == "start" else self.end
        self._set_mark(which, cur + frames / self.fps)
        self.seek(self.start if which == "start" else self.end, end_frame=(which == "end"))

    def _typed(self, key, submitted):
        v = core.parse_time(self.inputs[key].get())
        cur = self.start if key == "start" else self.end
        if v is not None and abs(v - cur) > 0.004:
            self._set_mark(key, v)
            self.seek(self.start if key == "start" else self.end, end_frame=(key == "end"))
        else:
            self._sync_fields()
        if submitted:
            self.winfo_toplevel().focus_set()

    def _sync_fields(self):
        self.inputs["start"].set(fmt_tc(self.start))
        self.inputs["end"].set(fmt_tc(self.end))
        self.dur_lbl.configure(text=fmt_tc(self.end - self.start))

    def _on_pos(self):
        if not self.timeline._drag:
            self.timeline.follow(self.pos)
        self.timeline.set_pos(self.pos)
        self.time_lbl.configure(text=f"{fmt_tc(self.pos)}  /  {fmt_tc(self.duration)}")

    def result_range(self):
        return self.start, self.end

    def _changed(self):
        self._sync_fields()
        if self.on_change:
            self.on_change()


# ── Diálogo (recorte antes de descargar) ───────────────────────────────────────
class TrimDialog(Modal):
    def __init__(self, app, item, on_apply):
        root = app.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(1000, int((sw - 60) / T.SCALE))
        h = min(800, int((sh - 90) / T.SCALE))
        super().__init__(root, "Recortar fragmento", w, h, resizable=True)
        self.minsize(min(S(780), sw - 40), min(S(600), sh - 80))
        self.app, self.item, self.on_apply = app, item, on_apply
        duration = float(item.duration or 0) or 1.0
        s, e = item.trim if item.trim else (0.0, duration)
        self.cancel = threading.Event()
        self.proxy = None

        head = tk.Frame(self, bg=T.BG, padx=S(24), pady=S(14))
        head.pack(fill="x")
        tk.Label(head, text="RECORTAR", font=F.label, fg=T.ACCENT, bg=T.BG, anchor="w").pack(fill="x")
        tk.Label(head, text=T.ellipsize(F.h2, item.title or item.url, S(900)), font=F.h2,
                 fg=T.TEXT, bg=T.BG, anchor="w").pack(fill="x", pady=(S(2), 0))
        foot = tk.Frame(self, bg=T.BG_2, padx=S(24), pady=S(12))
        foot.pack(side="bottom", fill="x")
        Button(foot, text="Aplicar recorte", icon=G.CUT, variant="primary", command=self._apply).pack(side="right")
        Button(foot, text="Cancelar", variant="ghost", command=self.close).pack(side="right", padx=(0, S(8)))
        if item.trim:
            Button(foot, text="Quitar recorte", icon=G.DELETE, variant="danger", command=self._remove).pack(side="left")
        tk.Label(foot, text="Espacio reproducir · I / O marcar · S imán · ← → un cuadro · Alt + rueda: zoom · "
                            "rueda: desplazar", font=F.small, fg=T.TEXT_3, bg=T.BG_2).pack(
            side="left", padx=S(12))

        self.editor = TrimEditor(self, app, duration, s, e, mode="range")
        self.editor.pack(fill="both", expand=True)
        self.editor.set_overlay("Preparando reproductor…", 0.0)
        self.bind("<Return>", lambda ev: None if isinstance(self.focus_get(), tk.Entry) else self._apply())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show()
        self.after(60, self._start_preview)

    def _start_preview(self):
        bus = self.app.bus
        mgr = self.app.downloads.mgr
        mgr.fetch_preview(
            self.item, self.cancel,
            on_info=lambda: bus.call(self._info_ready),
            on_progress=lambda p: bus.call(lambda: self.winfo_exists() and not self.proxy and
                                           self.editor.set_overlay(f"Preparando reproductor · {p * 100:.0f}%", p)),
            on_done=lambda path, err: bus.call(self._proxy_ready, path, err))
        if self.item.info:
            self._info_ready()

    def _info_ready(self):
        if not self.winfo_exists():
            return
        ed = self.editor
        if self.item.duration and abs(self.item.duration - ed.duration) > 0.5 and not self.item.trim:
            ed.set_duration(self.item.duration)
        info = self.item.info or {}
        fmts = [f for f in info.get("formats") or [] if f.get("vcodec") not in (None, "none") and f.get("url")]
        hls = [f for f in fmts if "m3u8" in (f.get("protocol") or "") and (f.get("height") or 0) <= 480]
        http = [f for f in fmts if (f.get("protocol") or "").startswith("http") and (f.get("height") or 0) <= 480]
        for group in (hls, http):
            if group:
                pick = sorted(group, key=lambda f: abs((f.get("height") or 360) - 360))[0]
                aspect = pick["width"] / pick["height"] if pick.get("width") and pick.get("height") else None
                ed.set_remote_source(pick["url"], pick.get("http_headers") or info.get("http_headers"), aspect)
                break

    def _proxy_ready(self, path, err):
        if not self.winfo_exists():
            return
        if not path:
            if err:
                self.editor.set_overlay("Reproductor no disponible · se muestran cuadros sueltos", kind="warn")
            return

        def inspect():
            try:
                pr = media.probe(path)
                vs = media.streams(pr, "video")
                v = vs[0] if vs else {}
                self.app.bus.call(self._proxy_inspected, path, media.stream_fps(v) or 30.0,
                                  media.display_aspect(v) if v else 16 / 9, media.duration_of(pr),
                                  bool(media.streams(pr, "audio")))
            except Exception as e:  # noqa: BLE001
                self.app.bus.call(self._proxy_ready, None, str(e))
        threading.Thread(target=inspect, daemon=True).start()

    def _proxy_inspected(self, path, fps, aspect, dur, has_audio):
        if not self.winfo_exists():
            return
        self.proxy = path
        ed = self.editor
        if dur and (not self.item.duration or abs(dur - ed.duration) > 2) and not self.item.trim:
            ed.set_duration(dur)
        ed.set_source(path, fps, aspect, has_audio)

    def _apply(self):
        s, e = self.editor.result_range()
        if e - s < 0.1:
            return
        full = s <= 0.02 and e >= self.editor.duration - 0.02
        self.on_apply(None if full else (round(s, 3), round(e, 3)))
        self.close()

    def _remove(self):
        self.on_apply(None)
        self.close()

    def close(self, result=None):
        self.cancel.set()
        self.editor.stop()
        super().close(result)
