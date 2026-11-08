"""Línea de tiempo de la pestaña Recortar, con el manejo de Premiere:

· Selección (V): clic elige un clip (Shift / Ctrl suma), arrastrar en vacío selecciona varios.
  Arrastrar un clip lo mueve sobrescribiendo; Ctrl inserta, Ctrl+Alt reordena, Alt duplica.
  Arrastrar un borde recorta; con Ctrl el recorte corre lo que sigue.
· Cuchilla (C): clic corta el clip en ese punto.
· Imán (S): los movimientos se pegan al cabezal, a los cortes y a las marcas.
· Rueda = zoom, Shift + rueda = desplazar, barra inferior = desplazar / zoom por los extremos."""

from __future__ import annotations

import threading
import tkinter as tk

from PIL import Image, ImageDraw, ImageEnhance, ImageTk

from .. import playback
from . import theme as T
from .theme import F, G, S
from .widgets import round_rect

SHIFT, CTRL, ALT = 0x0001, 0x0004, 0x20000

V_FILL = (42, 32, 39)
A_FILL = (20, 35, 26)
A_WAVE = (84, 182, 112)
A_MID = (40, 70, 50)
CLIP_EDGE = "#080606"
GAP_FILL = "#2A1E21"


def tc(t: float, fps: float) -> str:
    """Código de tiempo de Premiere: HH:MM:SS:FF."""
    f = max(1, int(round(fps)))
    n = int(round(max(0.0, t) * fps))
    ff = n % f
    s = n // f
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


def _ruler_label(t: float, fps: float, step: float) -> str:
    f = max(1, int(round(fps)))
    n = int(round(max(0.0, t) * fps))
    s = n // f
    base = f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
    return f"{base}:{n % f:02d}" if step < 1 - 1e-9 else base


# ── Miniaturas y formas de onda de cada archivo ────────────────────────────────
class MediaCache:
    def __init__(self, bus, on_update):
        self.bus, self.on_update = bus, on_update
        self.thumbs: dict = {}   # src.id → (intervalo, [PIL])
        self.peaks: dict = {}    # src.id → (picos por segundo, array)
        self.version: dict = {}
        self._started: set = set()
        self.cancel = threading.Event()

    def ensure(self, src, thumb_h: int):
        if src.id in self._started:
            return
        self._started.add(src.id)
        cancel = self.cancel
        if src.has_video:
            def thumbs():
                try:
                    playback.source_thumbs(src.path, src.duration, thumb_h, src.aspect,
                                           lambda iv, imgs: self.bus.call(self._got_thumbs, src.id, iv, imgs),
                                           cancel)
                except Exception:
                    pass
            threading.Thread(target=thumbs, daemon=True, name="tl-thumbs").start()
        if src.has_audio:
            def peaks():
                try:
                    res = playback.audio_peaks(src.path, src.duration, cancel)
                    if not cancel.is_set():
                        self.bus.call(self._got_peaks, src.id, res)
                except Exception:
                    pass
            threading.Thread(target=peaks, daemon=True, name="tl-peaks").start()

    def _got_thumbs(self, sid, interval, imgs):
        if self.cancel.is_set():
            return
        self.thumbs[sid] = (interval, imgs)
        self.version[sid] = self.version.get(sid, 0) + 1
        self.on_update()

    def _got_peaks(self, sid, res):
        if self.cancel.is_set():
            return
        self.peaks[sid] = res
        self.version[sid] = self.version.get(sid, 0) + 1
        self.on_update()

    def reset(self):
        self.cancel.set()
        self.cancel = threading.Event()
        self.thumbs.clear()
        self.peaks.clear()
        self.version.clear()
        self._started.clear()


# ── Línea de tiempo ────────────────────────────────────────────────────────────
class SeqTimeline(tk.Canvas):
    def __init__(self, master, ed):
        self.LW = S(50)
        self.RH = S(28)
        self.VH = S(58)
        self.AH = S(42)
        self.v_top = self.RH + S(6)
        self.v_bot = self.v_top + self.VH
        self.a_top = self.v_bot + S(4)
        self.a_bot = self.a_top + self.AH
        self.sb_top = self.a_bot + S(10)
        self.sb_bot = self.sb_top + S(8)
        super().__init__(master, bg=T.BG, highlightthickness=0, bd=0, height=self.sb_bot + S(6), takefocus=0)
        self.ed = ed
        self.v0 = 0.0
        self.pps = 40.0
        self._photos: dict = {}
        self._drag = None
        self._hover = None
        self._guide = None      # x de la guía de la cuchilla
        self._snap_t = None     # punto donde se pegó el último movimiento
        self._info = None       # (x, y, texto, pista) durante un arrastre
        self._marquee = None
        self._fitted = False
        self._fast = False
        self._gesture_job = None
        self._redraw_job = None
        self.bind("<Configure>", self._configured)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._b1_motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._leave())
        self.bind("<Button-3>", self._context)
        self._wheel_target = self._wheel

    # ── Geometría ──────────────────────────────────────────────────────────
    @property
    def x0(self):
        return self.LW + S(10)

    @property
    def x1(self):
        return max(self.x0 + 50, self.winfo_width() - S(14))

    @property
    def seq(self):
        return self.ed.seq

    def t2x(self, t):
        return self.x0 + (t - self.v0) * self.pps

    def x2t(self, x):
        return self.v0 + (x - self.x0) / self.pps

    def visible_span(self):
        return (self.x1 - self.x0) / self.pps

    def _limits(self):
        span = max(self.seq.duration, 2.0)
        lo = (self.x1 - self.x0) / (span * 1.6)
        hi = self.seq.fps * S(28)
        return lo, max(lo, hi)

    def thumb_height(self):
        return self.VH - S(4)

    def _configured(self, _e):
        if not self._fitted and self.winfo_width() > 200 and self.seq.clips:
            self.fit()
        else:
            self.set_view(self.v0)

    # ── Vista ──────────────────────────────────────────────────────────────
    def fit(self):
        if self.winfo_width() < 200:
            return
        self._fitted = True
        span = max(self.seq.duration, 2.0) * 1.03
        self.pps = (self.x1 - self.x0) / span
        self.v0 = 0.0
        self.redraw()

    def set_view(self, v0, pps=None):
        if pps is not None:
            lo, hi = self._limits()
            self.pps = max(lo, min(hi, pps))
        vis = self.visible_span()
        limit = max(0.0, self.seq.duration + vis * 0.5 - vis)
        self.v0 = max(0.0, min(limit, v0))
        self.redraw()

    def zoom(self, factor, anchor=None):
        if anchor is None:
            anchor = self.ed.pos if self.v0 <= self.ed.pos <= self.v0 + self.visible_span() else \
                self.v0 + self.visible_span() / 2
        frac = (anchor - self.v0) / max(1e-9, self.visible_span())
        lo, hi = self._limits()
        pps = max(lo, min(hi, self.pps * factor))
        self.pps = pps
        self.set_view(anchor - frac * self.visible_span())

    def follow(self, t):
        vis = self.visible_span()
        if t > self.v0 + vis * 0.98 or t < self.v0:
            self.set_view(t - vis * 0.05)
            return True
        return False

    def _wheel(self, e):
        if self._drag:
            return
        self._gesture()
        if e.state & (SHIFT | CTRL):  # Shift o Ctrl + rueda: desplazar hacia los costados
            self.set_view(self.v0 - self.visible_span() * 0.12 * (e.delta / 120))
            return
        x = self.winfo_pointerx() - self.winfo_rootx()
        self.zoom(1.25 if e.delta > 0 else 0.8, anchor=self.x2t(max(self.x0, min(self.x1, x))))

    def _gesture(self):
        """Durante un zoom o desplazamiento con la rueda se dibuja sin miniaturas nuevas (rápido)
        y 180 ms después del último movimiento se completa el dibujo."""
        self._fast = True
        if self._gesture_job:
            self.after_cancel(self._gesture_job)

        def done():
            self._gesture_job = None
            self._fast = False
            self.redraw()
        self._gesture_job = self.after(180, done)

    # ── Imán ───────────────────────────────────────────────────────────────
    def _targets(self, exclude=(), include_playhead=True):
        seq = self.seq
        pts = [0.0]
        if include_playhead:
            pts.append(self.ed.pos)
        if seq.mark_in is not None:
            pts.append(seq.mark_in)
        if seq.mark_out is not None:
            pts.append(seq.mark_out)
        for c in seq.clips:
            if c.id not in exclude:
                pts += [c.start, c.end]
        return pts

    def snap(self, t, pts, force=False):
        """Pega t al punto más cercano si está a menos de 8 px (con el imán prendido)."""
        if not (self.ed.snap or force) or not pts:
            return t, None
        best = min(pts, key=lambda p: abs(p - t))
        if abs(best - t) * self.pps <= S(8):
            return best, best
        return t, None

    # ── Hit test ───────────────────────────────────────────────────────────
    def _lane(self, y):
        if self.v_top <= y < self.v_bot:
            return "v"
        if self.a_top <= y < self.a_bot:
            return "a"
        return None

    def _hit(self, x, y):
        if y < self.RH:
            return ("ruler",)
        if self.sb_top - S(4) <= y <= self.sb_bot + S(4):
            return ("scroll", self._scroll_part(x))
        lane = self._lane(y)
        if lane is None or x < self.x0 - S(4):
            return ("none",)
        for c in self.seq.clips:
            cx1, cx2 = self.t2x(c.start), self.t2x(c.end)
            if cx1 <= x < cx2:
                edge = min(S(7), (cx2 - cx1) / 3)
                if x - cx1 < edge:
                    return ("edge", c, "in")
                if cx2 - x <= edge:
                    return ("edge", c, "out")
                return ("clip", c)
        t = self.x2t(x)
        gap = self.seq.gap_at(t)
        if gap:
            return ("gap", gap)
        return ("empty",)

    def _scroll_part(self, x):
        tx1, tx2 = self._thumb_px()
        if tx1 - S(4) <= x <= tx1 + S(6):
            return "left"
        if tx2 - S(6) <= x <= tx2 + S(4):
            return "right"
        if tx1 < x < tx2:
            return "thumb"
        return "track"

    def _thumb_px(self):
        total = max(self.seq.duration + self.visible_span() * 0.5, self.visible_span())
        w = self.x1 - self.x0
        a = self.x0 + w * self.v0 / total
        b = self.x0 + w * min(1.0, (self.v0 + self.visible_span()) / total)
        if b - a < S(28):
            b = a + S(28)
        return a, b

    # ── Interacción ────────────────────────────────────────────────────────
    def _leave(self):
        if self._drag is None:
            self._hover = None
            self._guide = None
            self.configure(cursor="arrow")
            self._draw_overlay()

    def _motion(self, e):
        if self._drag is not None:
            return
        hit = self._hit(e.x, e.y)
        cursor = "arrow"
        guide = None
        if hit[0] == "ruler":
            cursor = "hand2"
        elif hit[0] == "scroll":
            cursor = "sb_h_double_arrow" if hit[1] in ("left", "right") else "hand2"
        elif hit[0] in ("clip", "edge") and self.ed.tool == "razor":
            cursor = "tcross"
            t, _ = self.snap(self.x2t(e.x), self._targets())
            guide = self.t2x(t)
        elif hit[0] == "edge":
            cursor = "sb_h_double_arrow"
        key = (hit[0], getattr(hit[1], "id", hit[1]) if len(hit) > 1 else None, hit[2] if len(hit) > 2 else None)
        changed = key != self._hover or guide != self._guide
        self._hover = key
        self._guide = guide
        self.configure(cursor=cursor)
        if changed:
            self._draw_overlay()

    def _press(self, e):
        self.ed.focus_editor()
        hit = self._hit(e.x, e.y)
        kind = hit[0]
        t_press = self.x2t(e.x)
        self._snap_t = None
        if kind == "ruler":
            self._drag = {"kind": "scrub"}
            self._scrub(e.x, final=False)
            return
        if kind == "scroll":
            part = hit[1]
            if part == "track":
                self.set_view(self.x2t_scroll(e.x) - self.visible_span() / 2)
                part = "thumb"
            self._drag = {"kind": "scroll", "part": part, "x": e.x, "v0": self.v0, "pps": self.pps,
                          "t_end": self.v0 + self.visible_span()}
            return
        if kind in ("clip", "edge") and self.ed.tool == "razor":
            t, _ = self.snap(t_press, self._targets())
            self.ed.razor_at(t)
            return
        if kind == "edge":
            c, side = hit[1], hit[2]
            edge_t = c.start if side == "in" else c.end
            self._drag = {"kind": "trim", "cid": c.id, "side": side, "offset": edge_t - t_press,
                          "base": None, "started": False, "x": e.x}
            return
        if kind == "clip":
            c = hit[1]
            sel = set(self.ed.sel)
            additive = e.state & (SHIFT | CTRL) and not e.state & ALT
            if additive:
                sel ^= {c.id}
                self.ed.set_selection(sel)
                if c.id not in sel:
                    return
            elif c.id not in sel:
                self.ed.set_selection({c.id})
            self._drag = {"kind": "move", "cid": c.id, "t": t_press, "x": e.x, "base": None,
                          "started": False, "additive": bool(additive)}
            return
        self._drag = {"kind": "marquee", "x": e.x, "y": e.y, "gap": hit[1] if kind == "gap" else None,
                      "started": False, "keep": set(self.ed.sel) if e.state & (SHIFT | CTRL) else set()}

    def x2t_scroll(self, x):
        total = max(self.seq.duration + self.visible_span() * 0.5, self.visible_span())
        return (x - self.x0) / max(1, self.x1 - self.x0) * total

    def _autoscroll(self, x):
        vis = self.visible_span()
        if x > self.x1 - S(12):
            self.set_view(self.v0 + vis * 0.03)
        elif x < self.x0 + S(12) and self.v0 > 0:
            self.set_view(self.v0 - vis * 0.03)

    def _b1_motion(self, e):
        d = self._drag
        if d is None:
            return
        kind = d["kind"]
        if kind == "scrub":
            self._scrub(e.x, final=False)
        elif kind == "scroll":
            self._scroll_drag(d, e.x)
        elif kind == "move":
            if not d["started"]:
                if abs(e.x - d["x"]) < S(4):
                    return
                d["started"] = True
                d["ids"] = set(self.ed.sel)
                d["base"] = self.ed.begin_edit()
            self._autoscroll(e.x)
            self._move_drag(d, e)
        elif kind == "trim":
            if not d["started"]:
                if abs(e.x - d["x"]) < S(2):
                    return
                d["started"] = True
                d["base"] = self.ed.begin_edit()
            self._autoscroll(e.x)
            self._trim_drag(d, e)
        elif kind == "marquee":
            if not d["started"] and abs(e.x - d["x"]) + abs(e.y - d["y"]) < S(4):
                return
            d["started"] = True
            self._autoscroll(e.x)
            self._marquee = (d["x"], d["y"], e.x, e.y)
            xa, xb = sorted((d["x"], e.x))
            ya, yb = sorted((d["y"], e.y))
            hit = set(d["keep"])
            for c in self.seq.clips:
                cx1, cx2 = self.t2x(c.start), self.t2x(c.end)
                lanes = []
                if c.src.has_video:
                    lanes.append((self.v_top, self.v_bot))
                if c.src.has_audio:
                    lanes.append((self.a_top, self.a_bot))
                if cx2 >= xa and cx1 <= xb and any(b >= ya and a <= yb for a, b in lanes):
                    hit.add(c.id)
            self.ed.set_selection(hit, redraw=False)
            self.redraw()

    def _release(self, e):
        d, self._drag = self._drag, None
        if d is None:
            return
        kind = d["kind"]
        if kind == "scrub":
            self._scrub(e.x, final=True)
        elif kind in ("move", "trim"):
            if d["started"]:
                self.ed.end_edit()
                if kind == "trim":
                    self.ed.show_frame()
            elif kind == "move" and not d["additive"]:
                self.ed.set_selection({d["cid"]})  # clic simple dentro de una selección múltiple
        elif kind == "marquee":
            if not d["started"]:
                if d["gap"]:
                    self.ed.set_selection(set(), gap=d["gap"])
                else:
                    self.ed.set_selection(d["keep"])
            self._marquee = None
        self._info = None
        self._snap_t = None
        self.redraw()
        self._motion(e)

    def _scrub(self, x, final):
        t = max(0.0, self.x2t(x))
        pts = [p for p in self._targets(include_playhead=False)]
        t, hit = self.snap(t, pts)
        t = min(t, max(self.seq.duration, 0.0))
        self._snap_t = hit if not final else None
        self.ed.seek(self.seq.q(t) if not hit else t, final=final, from_timeline=True)
        self._draw_overlay()

    def _scroll_drag(self, d, x):
        w = max(1, self.x1 - self.x0)
        total = max(self.seq.duration + (d["t_end"] - d["v0"]) * 0.5, d["t_end"] - d["v0"])
        dt = (x - d["x"]) / w * total
        if d["part"] == "thumb":
            self.set_view(d["v0"] + dt)
        elif d["part"] == "left":
            v0 = min(d["v0"] + dt, d["t_end"] - 0.2)
            self.set_view(v0, pps=(self.x1 - self.x0) / (d["t_end"] - v0))
        else:
            t_end = max(d["t_end"] + dt, d["v0"] + 0.2)
            self.set_view(d["v0"], pps=(self.x1 - self.x0) / (t_end - d["v0"]))

    def _mode(self, state):
        if state & CTRL and state & ALT:
            return "rearrange"
        if state & CTRL:
            return "insert"
        if state & ALT:
            return "duplicate"
        return "overwrite"

    def _move_drag(self, d, e):
        seq = self.seq
        seq.restore(d["base"])
        ids = d["ids"]
        moving = [c for c in seq.clips if c.id in ids]
        if not moving:
            return
        delta = self.x2t(e.x) - d["t"]
        first = min(c.start for c in moving)
        last = max(c.end for c in moving)
        delta = max(-first, delta)
        pts = self._targets(exclude=ids)
        s1, h1 = self.snap(first + delta, pts)
        s2, h2 = self.snap(last + delta, pts)
        if h1 is not None and (h2 is None or abs(s1 - first - delta) <= abs(s2 - last - delta)):
            delta, self._snap_t = s1 - first, h1
        elif h2 is not None:
            delta, self._snap_t = s2 - last, h2
        else:
            self._snap_t = None
        mode = self._mode(e.state)
        new_ids = seq.move(ids, delta, mode)
        self.ed.set_selection(set(new_ids), redraw=False)
        sign = "+" if delta >= 0 else "−"
        label = {"overwrite": "Sobrescribir", "insert": "Insertar", "rearrange": "Reordenar",
                 "duplicate": "Duplicar"}[mode]
        hint = "Ctrl: insertar · Ctrl+Alt: reordenar · Alt: duplicar" if mode == "overwrite" else \
            "Soltá para aplicar"
        self._info = (e.x, f"{sign}{tc(abs(delta), seq.fps)}  ·  {label}", hint)
        self.ed.changed(live=True)
        self.redraw()

    def _trim_drag(self, d, e):
        seq = self.seq
        seq.restore(d["base"])
        c = seq.get(d["cid"])
        if c is None:
            return
        t = self.x2t(e.x) + d["offset"]
        pts = self._targets(exclude={c.id})
        t, self._snap_t = self.snap(t, pts)
        ripple = bool(e.state & CTRL)
        d["ripple"] = ripple
        before_dur = c.dur
        seq.trim(c.id, d["side"], t, ripple=ripple)
        c = seq.get(d["cid"])
        diff = c.dur - before_dur
        sign = "+" if diff >= 0 else "−"
        label = "Recorte en cadena" if ripple else "Recorte"
        self._info = (e.x, f"{label}  ·  {sign}{tc(abs(diff), seq.fps)}  ·  dura {tc(c.dur, seq.fps)}",
                      "Ctrl: corre lo que sigue" if not ripple else "Los clips siguientes se corren")
        self.ed.trim_preview(c, d["side"])
        self.ed.changed(live=True)
        self.redraw()

    def _context(self, e):
        hit = self._hit(e.x, e.y)
        if hit[0] in ("clip", "edge"):
            c = hit[1]
            if c.id not in self.ed.sel:
                self.ed.set_selection({c.id})
        elif hit[0] == "gap":
            self.ed.set_selection(set(), gap=hit[1])
        self.ed.context_menu(e, hit, self.x2t(e.x))

    # ── Dibujo ─────────────────────────────────────────────────────────────
    def redraw(self):
        if self._fast:  # en medio de un gesto: se agrupan los dibujos (uno cada ~16 ms)
            if self._redraw_job is None:
                self._redraw_job = self.after(16, self._redraw_now)
            return
        self._redraw_now()

    def _redraw_now(self):
        self._redraw_job = None
        if self.winfo_width() < 120:
            return
        self.delete("all")
        self._live = []
        self._draw_tracks()
        self._draw_ruler()
        self._draw_clips()
        self._draw_marks()
        self._draw_scroll()
        self._draw_overlay()
        self._draw_playhead()

    def _draw_tracks(self):
        x0, x1 = self.x0, self.x1
        for top, bot, name, icon in ((self.v_top, self.v_bot, "V1", G.VIDEO), (self.a_top, self.a_bot, "A1", G.AUDIO)):
            round_rect(self, S(4), top, self.LW, bot, S(6), T.SURFACE_2, None, T.BG)
            self.create_text(S(16), (top + bot) / 2 + S(1), text=icon, font=F.icon_sm, fill=T.TEXT_3)
            self.create_text(S(30), (top + bot) / 2, text=name, font=F.small_b, fill=T.TEXT_2, anchor="w")
            self.create_rectangle(x0 - S(4), top, x1 + S(4), bot, fill=T.SURFACE, width=0)
        if self.seq.duration > 0:
            end = self.t2x(self.seq.duration)
            if x0 <= end <= x1:
                self.create_line(end, self.v_top, end, self.a_bot, fill=T.BORDER, dash=(2, 3))

    def _ticks(self):
        fps = self.seq.fps
        steps = [1 / fps, 2 / fps, 5 / fps, 10 / fps, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900,
                 1800, 3600, 7200]
        step = next((s for s in steps if s * self.pps >= S(86)), 7200)
        i = steps.index(step) if step in steps else len(steps) - 1
        minor = steps[i - 1] if i > 0 and steps[i - 1] * self.pps >= S(10) else step / 2
        return step, minor

    def _draw_ruler(self):
        x0, x1, rh = self.x0, self.x1, self.RH
        self.create_rectangle(x0 - S(4), 0, x1 + S(4), rh, fill=T.BG_2, width=0)
        seq = self.seq
        # entrada / salida
        if seq.mark_in is not None or seq.mark_out is not None:
            a = seq.mark_in if seq.mark_in is not None else 0.0
            b = seq.mark_out if seq.mark_out is not None else max(seq.duration, a)
            xa, xb = max(x0, self.t2x(a)), min(x1, self.t2x(b))
            if xb > xa:
                self.create_rectangle(xa, 0, xb, rh, fill=T.ACCENT_SOFT, width=0)
        step, minor = self._ticks()
        ratio = max(1, int(round(step / minor)))
        end = self.v0 + self.visible_span()
        k = int(self.v0 / minor)
        while k * minor <= end + minor:
            t = k * minor
            x = self.t2x(t)
            if x0 <= x <= x1:
                major = k % ratio == 0
                self.create_line(x, rh - (S(9) if major else S(5)), x, rh, fill=T.TEXT_3 if major else T.TEXT_4)
                if major and x + S(4) < x1 - S(30):
                    self.create_text(x + S(4), S(10), text=_ruler_label(t, seq.fps, step), font=F.mono_sm,
                                     fill=T.TEXT_3, anchor="w")
            k += 1
        # marcas I / O
        for mark, side in ((seq.mark_in, "in"), (seq.mark_out, "out")):
            if mark is None:
                continue
            x = self.t2x(mark)
            if x0 - S(2) <= x <= x1 + S(2):
                k = S(5) if side == "in" else -S(5)
                self.create_line(x, 0, x, rh, fill=T.ACCENT, width=S(2))
                self.create_line(x, S(1), x + k, S(1), fill=T.ACCENT, width=S(2))
                self.create_line(x, rh - S(1), x + k, rh - S(1), fill=T.ACCENT, width=S(2))
        # loop
        if self.ed.loop:
            a, b = self.ed.loop_range()
            xa, xb = max(x0, self.t2x(a)), min(x1, self.t2x(b))
            if xb > xa:
                self.create_rectangle(xa, rh - S(3), xb, rh, fill=T.OK, width=0)

    def _draw_clips(self):
        seq = self.seq
        x0, x1 = self.x0, self.x1
        if len(self._photos) > 400:
            self._photos.clear()
        gap = self.ed.gap_sel
        if gap:
            ga, gb = max(x0, self.t2x(gap[0])), min(x1, self.t2x(gap[1]))
            if gb > ga:
                for top, bot in ((self.v_top, self.v_bot), (self.a_top, self.a_bot)):
                    self.create_rectangle(ga, top, gb, bot - 1, fill=GAP_FILL, outline=T.ACCENT, dash=(3, 3))
        hover = self._hover
        for c in seq.clips:
            cx1, cx2 = self.t2x(c.start), self.t2x(c.end)
            if cx2 < x0 or cx1 > x1:
                continue
            va, vb = max(cx1, x0), min(cx2, x1)
            if vb - va < 1:
                continue
            sel = c.id in self.ed.sel
            lanes = []
            if c.src.has_video:
                lanes.append(("v", self.v_top, self.v_bot))
            if c.src.has_audio:
                lanes.append(("a", self.a_top, self.a_bot))
            for lane, top, bot in lanes:
                photo = self._clip_photo(lane, c, cx1, va, vb, bot - top)
                if photo is not None:
                    self.create_image(int(va), top, image=photo, anchor="nw")
                else:
                    self.create_rectangle(va, top, vb, bot, fill="#2A2027" if lane == "v" else "#14231A", width=0)
                self.create_rectangle(cx1, top, cx2 - 1, bot - 1, outline=T.ACCENT if sel else CLIP_EDGE,
                                      width=S(2) if sel else 1)
            # nombre
            if vb - va > S(34):
                ly = self.v_top + S(8) if c.src.has_video else self.a_top + S(8)
                name = T.ellipsize(F.tiny_b, c.src.name, int(vb - va - S(12)))
                self.create_text(va + S(6), ly, text=name, font=F.tiny_b,
                                 fill=T.TEXT if sel else T.TEXT_2, anchor="w")
            # borde resaltado (recorte)
            d = self._drag
            active_edge = None
            if d and d["kind"] == "trim" and d["cid"] == c.id:
                active_edge = d["side"]
            elif hover and hover[0] == "edge" and hover[1] == c.id and self.ed.tool == "select":
                active_edge = hover[2]
            if active_edge:
                ex = cx1 if active_edge == "in" else cx2
                col = T.WARN if (d and d.get("ripple")) else T.ACCENT_HI
                bx1, bx2 = (ex, ex + S(4)) if active_edge == "in" else (ex - S(4), ex)
                for _lane, top, bot in lanes:
                    self.create_rectangle(bx1, top, bx2, bot - 1, fill=col, width=0)
                    k = S(5) if active_edge == "in" else -S(5)
                    self.create_line(ex, top + S(2), ex + k, top + S(2), fill=col, width=S(2))
                    self.create_line(ex, bot - S(3), ex + k, bot - S(3), fill=col, width=S(2))

    def _clip_photo(self, lane, c, cx1, va, vb, h):
        w = int(round(vb - va))
        off = int(round(va - cx1))
        if w < 1:
            return None
        key = (lane, c.src.id, round(c.t_in, 4), round(c.t_out, 4), round(self.pps, 4), off, w, h,
               self.ed.media.version.get(c.src.id, 0))
        photo = self._photos.get(key)
        if photo is None and self._fast:
            return None  # se completa al terminar el gesto
        if photo is None:
            img = self._render_video(c, off, w, h) if lane == "v" else self._render_audio(c, off, w, h)
            photo = ImageTk.PhotoImage(img)
            self._photos[key] = photo
        return photo

    def _render_video(self, c, off, w, h):
        img = Image.new("RGB", (w, h), V_FILL)
        data = self.ed.media.thumbs.get(c.src.id)
        if data:
            interval, imgs = data
            tw, th = imgs[0].size
            y = max(0, (h - th) // 2)
            k = int(off // tw)
            while k * tw < off + w:
                tx = k * tw
                t = c.t_in + (tx + tw / 2) / self.pps
                idx = max(0, min(len(imgs) - 1, int(round(t / interval - 0.5))))
                img.paste(imgs[idx], (int(tx - off), y))
                k += 1
        strip_h = S(15)
        strip = img.crop((0, 0, w, strip_h))
        img.paste(ImageEnhance.Brightness(strip).enhance(0.32), (0, 0))
        return img

    def _render_audio(self, c, off, w, h):
        img = Image.new("RGB", (w, h), A_FILL)
        d = ImageDraw.Draw(img)
        cy = h / 2
        d.line((0, cy, w, cy), fill=A_MID)
        data = self.ed.media.peaks.get(c.src.id)
        if data:
            rate, arr = data
            n = len(arr)
            hh = h / 2 - S(3)
            pps = self.pps
            top_pad = S(14) if not c.src.has_video else 0
            if top_pad:
                hh = (h - top_pad) / 2 - S(2)
                cy = top_pad + (h - top_pad) / 2
            for x in range(w):
                t0 = c.t_in + (off + x) / pps
                i0 = int(t0 * rate)
                if i0 >= n:
                    break
                i1 = max(i0 + 1, int((t0 + 1 / pps) * rate))
                v = max(arr[i0:min(i1, n)])
                a = min(1.0, v * 1.15) * hh
                if a >= 0.5:
                    d.line((x, cy - a, x, cy + a), fill=A_WAVE)
        if not c.src.has_video:
            strip = img.crop((0, 0, w, S(15)))
            img.paste(ImageEnhance.Brightness(strip).enhance(0.45), (0, 0))
        return img

    def _draw_marks(self):
        seq = self.seq
        for mark in (seq.mark_in, seq.mark_out):
            if mark is None:
                continue
            x = self.t2x(mark)
            if self.x0 <= x <= self.x1:
                self.create_line(x, self.v_top, x, self.a_bot, fill=T.ACCENT_LO, dash=(4, 3))

    def _draw_scroll(self):
        x0, x1 = self.x0, self.x1
        y1, y2 = self.sb_top, self.sb_bot
        round_rect(self, x0, y1, x1, y2, S(4), T.SURFACE, None, T.BG)
        a, b = self._thumb_px()
        b = min(b, x1)
        d = self._drag
        active = d is not None and d["kind"] == "scroll"
        hov = self._hover and self._hover[0] == "scroll"
        round_rect(self, a, y1, b, y2, S(4), T.BORDER if (active or hov) else T.SURFACE_HI, None, T.SURFACE)
        for x in (a + S(4), b - S(4)):
            self.create_oval(x - S(2), (y1 + y2) / 2 - S(2), x + S(2), (y1 + y2) / 2 + S(2), fill=T.TEXT_3, width=0)

    def _draw_overlay(self):
        self.delete("ov")
        if self.winfo_width() < 120:
            return
        top, bot = self.v_top, self.a_bot
        if self._guide is not None and self._drag is None:
            x = self._guide
            self.create_line(x, top - S(2), x, bot + S(2), fill=T.ACCENT_HI, width=S(2), dash=(4, 2), tags=("ov",))
            self.create_text(x + S(6), top + S(8), text=G.CUT, font=F.icon_sm, fill=T.ACCENT_HI, anchor="w",
                             tags=("ov",))
        if self._snap_t is not None:
            x = self.t2x(self._snap_t)
            if self.x0 - S(2) <= x <= self.x1 + S(2):
                self.create_line(x, self.RH - S(4), x, bot + S(4), fill=T.WARN, width=S(2), tags=("ov",))
                k = S(5)
                self.create_polygon(x - k, self.RH - S(6), x + k, self.RH - S(6), x, self.RH, fill=T.WARN,
                                    outline="", tags=("ov",))
                self.create_polygon(x - k, bot + S(6), x + k, bot + S(6), x, bot, fill=T.WARN, outline="",
                                    tags=("ov",))
        if self._marquee:
            xa, ya, xb, yb = self._marquee
            self.create_rectangle(xa, ya, xb, yb, outline=T.TEXT_2, dash=(3, 3), tags=("ov",))
        if self._info:  # sobre la regla, para no tapar el clip que se arrastra
            x, text, hint = self._info
            gap = S(14)
            tw = F.small_b.measure(text) + gap + F.small.measure(hint) + S(20)
            bx = max(self.x0, min(self.x1 - tw, x - tw / 2))
            by1, by2 = S(1), self.RH - S(1)
            round_rect(self, bx, by1, bx + tw, by2, S(6), "#0D0A0A", T.BORDER, T.BG_2, tags=("ov",))
            cy = (by1 + by2) / 2
            self.create_text(bx + S(10), cy, text=text, font=F.small_b, fill=T.TEXT, anchor="w", tags=("ov",))
            self.create_text(bx + S(10) + F.small_b.measure(text) + gap, cy, text=hint, font=F.small,
                             fill=T.TEXT_3, anchor="w", tags=("ov",))

    def _draw_playhead(self):
        self.delete("ph")
        if self.winfo_width() < 120:
            return
        x = self.t2x(self.ed.pos)
        if not (self.x0 - S(1) <= x <= self.x1 + S(1)):
            return
        k = S(6)
        self.create_line(x, S(12), x, self.a_bot + S(3), fill=T.TEXT, width=1, tags=("ph",))
        self.create_polygon(x - k, S(2), x + k, S(2), x + k, S(10), x, S(16), x - k, S(10), fill=T.TEXT,
                            outline="", tags=("ph",))
        self.tag_raise("ov")  # el cartel del arrastre queda encima del cabezal

    def set_pos(self, t, follow=True):
        if follow and self._drag is None and self.follow(t):
            return
        self._draw_playhead()
