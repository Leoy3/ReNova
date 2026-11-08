"""Editor de la pestaña Recortar: visor + transporte + herramientas + línea de tiempo (ui/timeline.py)
sobre el modelo de renova/sequence.py. Atajos y comportamiento de Premiere Pro."""

from __future__ import annotations

import logging
import time
import tkinter as tk

from .. import playback
from ..sequence import Sequence
from . import theme as T
from .theme import F, G, S
from .timeline import MediaCache, SeqTimeline, tc
from .trim import G_LEFT, G_NEXT, G_PAUSE, G_PLAY, G_PREV, G_REDO, G_REPEAT, G_RIGHT, G_UNDO, G_ZOOM_IN, \
    G_ZOOM_OUT, VideoView
from .widgets import Button, Modal, PopupMenu, Segmented, Tooltip, attach_tooltip, reveal_in_explorer, round_rect

log = logging.getLogger("renova.editor")

G_POINTER = ""
G_FIT = ""
G_KEYBOARD = ""


def draw_magnet(cv, cx, cy, color, tip):
    """Ícono de imán (no hay uno en Segoe MDL2)."""
    r, w = S(5), S(3)
    cv.create_arc(cx - r, cy - r + S(1), cx + r, cy + r + S(1), start=180, extent=180, style="arc",
                  outline=color, width=w)
    for x in (cx - r, cx + r):
        cv.create_line(x, cy - S(6), x, cy + S(1), fill=color, width=w)
        cv.create_line(x, cy - S(6), x, cy - S(3), fill=tip, width=w)


class ToolToggle(tk.Canvas):
    """Botón que queda encendido o apagado (imán, loop)."""

    def __init__(self, master, text, icon=None, draw_icon=None, value=False, command=None, tooltip=None,
                 height=32, bg=None):
        self._bg = bg or master.cget("bg")
        self.text, self.icon, self.draw_icon = text, icon, draw_icon
        iw = S(16) if draw_icon else (F.icon_sm.measure(icon) if icon else 0)
        width = S(12) + iw + (S(8) + F.small_b.measure(text) if text else 0) + S(12)
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0, height=S(height), width=width,
                         cursor="hand2", takefocus=0)
        self.value, self.command = value, command
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        if tooltip:
            attach_tooltip(self, tooltip)

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set(self, v):
        self.value = bool(v)
        self._draw()

    def _click(self, e):
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.value = not self.value
            self._draw()
            if self.command:
                self.command(self.value)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(self["width"]), int(self["height"])
        on = self.value
        fill = T.ACCENT_SOFT if on else (T.SURFACE_HI if self._hover else None)
        outline = T.ACCENT_LO if on else (T.BORDER if self._hover else T.BORDER_SOFT)
        round_rect(self, 0, 0, w, h, S(8), fill or self._bg, outline, self._bg)
        fg = T.ACCENT if on else (T.TEXT if self._hover else T.TEXT_2)
        x = S(12)
        if self.draw_icon:
            self.draw_icon(self, x + S(8), h / 2, fg, T.TEXT if on else T.TEXT_3)
            x += S(16)
        elif self.icon:
            self.create_text(x, h / 2 + S(1), text=self.icon, font=F.icon_sm, fill=fg, anchor="w")
            x += F.icon_sm.measure(self.icon)
        if self.text:
            self.create_text(x + S(8), h / 2, text=self.text, font=F.small_b,
                             fill=T.TEXT if on else fg, anchor="w")


G_VOL = ("", "", "", "")
G_VOL_MUTE = ""


class VolumeControl(tk.Canvas):
    """Volumen de escucha mientras se edita (no cambia el archivo). Clic en el parlante: silenciar.
    Se guarda en las preferencias y lo comparten los dos editores."""

    def __init__(self, master, settings, on_change, bg=None):
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg, highlightthickness=0, bd=0, width=S(124), height=S(32),
                         cursor="hand2", takefocus=0)
        self.settings, self.on_change = settings, on_change
        self.value = float(settings.get("preview_volume"))
        self.muted = bool(settings.get("preview_muted"))
        self._hover = False
        self._drag = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", lambda e: self._drag and self._set_from_x(e.x))
        self.bind("<ButtonRelease-1>", self._release)
        self._wheel_target = self._wheel
        attach_tooltip(self, lambda: ("Volumen de escucha: " + ("silenciado" if self.muted else f"{self.value * 100:.0f}%")
                                      + "\nSólo cambia lo que oís mientras editás, no el archivo."
                                      "\nClic en el parlante: silenciar · rueda: subir / bajar"))

    @property
    def effective(self) -> float:
        return 0.0 if self.muted else self.value

    def sync(self):
        self.value = float(self.settings.get("preview_volume"))
        self.muted = bool(self.settings.get("preview_muted"))
        self._draw()

    def _track(self):
        return S(34), self.winfo_width() - S(10)

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def _press(self, e):
        if e.x < S(30):
            self.muted = not self.muted
            if not self.muted and self.value < 0.02:
                self.value = 0.5
            self._commit()
            return
        self._drag = True
        self._set_from_x(e.x)

    def _release(self, _e):
        if self._drag:
            self._drag = False
            self._commit(save=True)

    def _set_from_x(self, x):
        a, b = self._track()
        self.value = max(0.0, min(1.0, (x - a) / max(1, b - a)))
        self.muted = self.value < 0.01
        self._commit(save=False)

    def _wheel(self, e):
        self.value = max(0.0, min(1.0, self.value + (0.05 if e.delta > 0 else -0.05)))
        self.muted = self.value < 0.01
        self._commit()

    def _commit(self, save=True):
        self._draw()
        if save:
            self.settings.set("preview_volume", float(round(self.value, 3)))
            self.settings.set("preview_muted", bool(self.muted))
        if self.on_change:
            self.on_change(self.effective)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w, h = int(self["width"]), int(self["height"])
        if self._hover or self._drag:
            round_rect(self, 0, 0, w, h, S(8), T.SURFACE_HI, None, self._bg)
        v = self.effective
        glyph = G_VOL_MUTE if self.muted else G_VOL[min(3, int(v * 3.999))]
        self.create_text(S(15), h / 2 + S(1), text=glyph, font=F.icon_sm,
                         fill=T.TEXT_3 if self.muted else (T.TEXT if self._hover else T.TEXT_2))
        a, b = S(34), w - S(10)
        cy = h / 2
        round_rect(self, a, cy - S(2), b, cy + S(2), S(2), T.BORDER, None, T.SURFACE_HI if self._hover or self._drag else self._bg)
        x = a + (b - a) * v
        if x - a >= S(2):
            self.create_rectangle(a, cy - S(2), x, cy + S(2), fill=T.ACCENT if not self.muted else T.TEXT_4, width=0)
        r = S(6) if (self._hover or self._drag) else S(5)
        self.create_oval(x - r, cy - r, x + r, cy + r, fill=T.TEXT, outline="")


SHORTCUTS = [
    ("Reproducir / pausa", "Espacio"), ("Selección / cuchilla", "V / C"), ("Imán (ajustar)", "S"),
    ("Cortar en el cabezal", "Ctrl+K"), ("Borrar (deja el hueco)", "Supr"),
    ("Borrar y cerrar el hueco", "Shift+Supr"), ("Mover sobrescribiendo", "Arrastrar"),
    ("Mover insertando", "Ctrl+arrastrar"), ("Reordenar", "Ctrl+Alt+arrastrar"), ("Duplicar", "Alt+arrastrar"),
    ("Recorte en cadena", "Ctrl+arrastrar borde"), ("Recortar hasta el cabezal", "Q / W"),
    ("Corte anterior / siguiente", "↑ / ↓"),
    ("Cuadro anterior / siguiente", "← / →"), ("5 cuadros", "Shift+← / →"),
    ("Zoom / ver todo", "+ / − / \\"), ("Desplazar la línea de tiempo", "Ctrl + rueda"), ("Copiar / cortar / pegar", "Ctrl+C / X / V"),
    ("Pegar insertando", "Ctrl+Shift+V"), ("Seleccionar todo / nada", "Ctrl+A / Ctrl+Shift+A"),
    ("Deshacer / rehacer", "Ctrl+Z / Ctrl+Y"),
]


class SequenceEditor(tk.Frame):
    def __init__(self, master, app, on_change=None, on_add=None, padx=24):
        super().__init__(master, bg=T.BG)
        self.app, self.on_change, self.on_add = app, on_change, on_add
        self.seq = Sequence()
        self.sel: set = set()
        self.gap_sel = None
        self.pos = 0.0
        self.tool = "select"
        self.snap = bool(app.settings.get("snap"))
        self.loop = bool(app.settings.get("loop"))
        self.clipboard: list = []
        self._undo: list = []
        self._redo: list = []
        self._edit_base = None
        self.playing = False
        self.video = self.audio = None
        self.until = 0.0
        self._t0 = self._wall0 = 0.0
        self._waiting = False
        self._tick_job = None
        self._last_frame_at = self._last_clock_at = 0.0
        self._last_clock = None
        self._frame_token = 0
        self._audio_card = None
        self.media = MediaCache(app.bus, self._media_ready)
        self.grabber = playback.FrameGrabber(None, lambda tag, w, h, d: app.bus.call(self._grabbed, tag, w, h, d))
        self._padx = S(padx)
        self._build()
        top = self.winfo_toplevel()
        self._bound = []
        for seq, fn in self._shortcuts():
            top.bind(seq, lambda e, fn=fn: self._key(e, fn), add="+")
            self._bound.append(seq)

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build(self):
        px = self._padx
        bg = self["bg"]
        self.timeline = SeqTimeline(self, self)
        self.timeline.pack(side="bottom", fill="x", padx=(max(0, px - S(14)), max(0, px - S(14))),
                           pady=(S(2), S(8)))

        tools = tk.Frame(self, bg=bg, padx=px)
        tools.pack(side="bottom", fill="x", pady=(S(6), S(2)))
        self.tool_seg = Segmented(tools, [("select", "Selección", G_POINTER), ("razor", "Cuchilla", G.CUT)],
                                  "select", command=self.set_tool, height=32)
        self.tool_seg.pack(side="left")
        attach_tooltip(self.tool_seg, "V: selección (mover, recortar bordes) · C: cuchilla (clic para cortar)")
        self.snap_btn = ToolToggle(tools, "Imán", draw_icon=draw_magnet, value=self.snap, command=self.set_snap,
                                   tooltip="Ajustar al cabezal, a los cortes y a las marcas (S)")
        self.snap_btn.pack(side="left", padx=(S(8), 0))
        self._sep(tools)
        self.undo_btn = Button(tools, icon=G_UNDO, variant="ghost", height=32, icon_font=F.icon_sm,
                               command=self.undo, tooltip="Deshacer (Ctrl+Z)")
        self.undo_btn.pack(side="left")
        self.redo_btn = Button(tools, icon=G_REDO, variant="ghost", height=32, icon_font=F.icon_sm,
                               command=self.redo, tooltip="Rehacer (Ctrl+Y)")
        self.redo_btn.pack(side="left")
        self._sep(tools)
        Button(tools, text="Cortar", icon=G.CUT, variant="secondary", height=32, padx=10, font=F.small_b,
               icon_font=F.icon_sm, command=self.add_edit,
               tooltip="Corta el clip en el cabezal (Ctrl+K)").pack(side="left")
        self.del_btn = Button(tools, text="Borrar", icon=G.DELETE, variant="secondary", height=32, padx=10,
                              font=F.small_b, icon_font=F.icon_sm, command=lambda: self.delete(False),
                              tooltip="Borra la selección y deja el hueco (Supr)")
        self.del_btn.pack(side="left", padx=(S(6), 0))
        self.ripple_btn = Button(tools, text="Borrar y cerrar hueco", variant="secondary", height=32, padx=10,
                                 font=F.small_b, command=lambda: self.delete(True),
                                 tooltip="Borra la selección y corre lo que sigue (Shift+Supr)")
        self.ripple_btn.pack(side="left", padx=(S(6), 0))
        Button(tools, icon=G_KEYBOARD, variant="ghost", height=32, icon_font=F.icon_sm,
               command=self._shortcuts_menu, tooltip="Atajos de teclado").pack(side="right")
        Button(tools, icon=G_FIT, variant="ghost", height=32, icon_font=F.icon_sm, command=self.timeline.fit,
               tooltip="Ver toda la secuencia (\\)").pack(side="right")
        Button(tools, icon=G_ZOOM_IN, variant="ghost", height=32, icon_font=F.icon_sm,
               command=lambda: self.timeline.zoom(1.5), tooltip="Acercar (+)").pack(side="right")
        Button(tools, icon=G_ZOOM_OUT, variant="ghost", height=32, icon_font=F.icon_sm,
               command=lambda: self.timeline.zoom(1 / 1.5), tooltip="Alejar (−)").pack(side="right")
        self.sel_lbl = tk.Label(tools, text="", font=F.small, fg=T.TEXT_3, bg=bg, anchor="e")
        self.sel_lbl.pack(side="right", padx=(S(8), S(8)))

        tr = tk.Frame(self, bg=bg, padx=px)
        tr.pack(side="bottom", fill="x", pady=(S(6), 0))
        tr.columnconfigure(0, weight=1, uniform="tr")
        tr.columnconfigure(2, weight=1, uniform="tr")
        left = tk.Frame(tr, bg=bg)
        left.grid(row=0, column=0, sticky="w")
        self.time_lbl = tk.Label(left, text="", font=F.tc, fg=T.ACCENT, bg=bg, width=11, anchor="w")
        self.time_lbl.pack(side="left")
        self.dur_lbl = tk.Label(left, text="", font=F.mono, fg=T.TEXT_3, bg=bg, width=13, anchor="w")
        self.dur_lbl.pack(side="left", padx=(S(8), 0), pady=(S(3), 0))
        self.vol = VolumeControl(left, self.app.settings, self._volume_changed)
        self.vol.pack(side="left", padx=(S(14), 0))
        mid = tk.Frame(tr, bg=bg)
        mid.grid(row=0, column=1)
        Button(mid, icon=G_PREV, variant="ghost", height=36, command=lambda: self.go_edit(-1),
               tooltip="Corte anterior (↑)").pack(side="left")
        Button(mid, icon=G_LEFT, variant="ghost", height=36, command=lambda: self.step(-1),
               tooltip="Un cuadro atrás (←)").pack(side="left", padx=(S(4), 0))
        self.play_btn = Button(mid, icon=G_PLAY, variant="primary", height=42, width=54, radius=21,
                               icon_font=F.icon_md, command=self.toggle_play, tooltip="Reproducir / pausa (Espacio)")
        self.play_btn.pack(side="left", padx=S(10))
        Button(mid, icon=G_RIGHT, variant="ghost", height=36, command=lambda: self.step(1),
               tooltip="Un cuadro adelante (→)").pack(side="left", padx=(0, S(4)))
        Button(mid, icon=G_NEXT, variant="ghost", height=36, command=lambda: self.go_edit(1),
               tooltip="Corte siguiente (↓)").pack(side="left")
        right = tk.Frame(tr, bg=bg)
        right.grid(row=0, column=2, sticky="e")
        self.loop_btn = ToolToggle(right, "Loop", icon=G_REPEAT, value=self.loop, command=self.set_loop,
                                   tooltip="Repetir sólo los clips elegidos (si no hay ninguno, "
                                           "toda la línea de tiempo)")
        self.loop_btn.pack(side="right")

        self.view = VideoView(self, on_click=self.toggle_play, on_resize=self._view_resized)
        self.view.pack(fill="both", expand=True, padx=px, pady=(S(4), 0))
        self.view.bind("<ButtonPress-1>", lambda e: self.focus_editor(), add="+")
        self._refresh_ui()

    def _sep(self, parent):
        tk.Frame(parent, bg=T.BORDER_SOFT, width=1, height=S(22)).pack(side="left", padx=S(10))

    def destroy(self):
        self.stop()
        try:
            top = self.winfo_toplevel()
            for seq in self._bound:
                top.unbind(seq)
        except tk.TclError:
            pass
        super().destroy()

    def focus_editor(self):
        try:
            self.focus_set()
        except tk.TclError:
            pass

    # ── Atajos ─────────────────────────────────────────────────────────────
    def _shortcuts(self):
        k = []

        def both(key, fn):
            k.append((f"<Key-{key.lower()}>", fn))
            k.append((f"<Key-{key.upper()}>", fn))
        k.append(("<space>", self.toggle_play))
        both("k", self.pause)
        both("l", self.play)
        both("v", lambda: self.set_tool("select"))
        both("c", lambda: self.set_tool("razor"))
        both("s", lambda: self.set_snap(not self.snap))
        both("q", lambda: self.ripple_trim("prev"))
        both("w", lambda: self.ripple_trim("next"))
        k += [("<Control-k>", self.add_edit), ("<Control-K>", self.add_edit),
              ("<Delete>", lambda: self.delete(False)), ("<BackSpace>", lambda: self.delete(False)),
              ("<Shift-Delete>", lambda: self.delete(True)), ("<Shift-BackSpace>", lambda: self.delete(True)),
              ("<Up>", lambda: self.go_edit(-1)), ("<Down>", lambda: self.go_edit(1)),
              ("<Left>", lambda: self.step(-1)), ("<Right>", lambda: self.step(1)),
              ("<Shift-Left>", lambda: self.step(-5)), ("<Shift-Right>", lambda: self.step(5)),
              ("<Home>", lambda: self.seek(0.0)), ("<End>", lambda: self.seek(self.seq.duration)),
              ("<plus>", lambda: self.timeline.zoom(1.5)), ("<equal>", lambda: self.timeline.zoom(1.5)),
              ("<KP_Add>", lambda: self.timeline.zoom(1.5)), ("<minus>", lambda: self.timeline.zoom(1 / 1.5)),
              ("<KP_Subtract>", lambda: self.timeline.zoom(1 / 1.5)), ("<backslash>", self.timeline.fit),
              ("<Control-z>", self.undo), ("<Control-Z>", self.undo), ("<Control-Shift-Z>", self.redo),
              ("<Control-y>", self.redo), ("<Control-Y>", self.redo),
              ("<Control-c>", self.copy), ("<Control-C>", self.copy),
              ("<Control-x>", self.cut), ("<Control-X>", self.cut),
              ("<Control-v>", lambda: self.paste(False)), ("<Control-V>", lambda: self.paste(False)),
              ("<Control-Shift-V>", lambda: self.paste(True)),
              ("<Control-a>", self.select_all), ("<Control-A>", self.select_all),
              ("<Control-Shift-A>", lambda: self.set_selection(set())), ("<Escape>", lambda: self.set_selection(set()))]
        return k

    def _key(self, e, fn):
        try:
            if not self.winfo_ismapped() or (not self.seq.clips and not self._undo and not self._redo):
                return None
            f = self.focus_get()
            if isinstance(f, tk.Entry) and f.winfo_ismapped():
                return None
            if e.widget.winfo_toplevel() is not self.winfo_toplevel():
                return None
        except (KeyError, tk.TclError):
            return None
        fn()
        return "break"

    def _shortcuts_menu(self):
        dlg = Modal(self, "Atajos de teclado", 720, 520)
        self._shortcuts_dlg = dlg
        body = tk.Frame(dlg, bg=T.BG, padx=S(24), pady=S(18))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Atajos de teclado", font=F.h2, fg=T.TEXT, bg=T.BG, anchor="w").pack(fill="x")
        tk.Label(body, text="Los mismos que en Premiere Pro. Con el mouse: rueda = zoom, Ctrl + rueda = desplazar, "
                            "clic derecho = más opciones.", font=F.small, fg=T.TEXT_3, bg=T.BG, anchor="w",
                 justify="left", wraplength=S(660)).pack(fill="x", pady=(S(4), S(12)))
        grid = tk.Frame(body, bg=T.BG)
        grid.pack(fill="both", expand=True)
        half = (len(SHORTCUTS) + 1) // 2
        for i, (label, key) in enumerate(SHORTCUTS):
            col, row = (0, i) if i < half else (2, i - half)
            tk.Label(grid, text=key, font=F.mono_sm, fg=T.TEXT, bg=T.SURFACE_2, padx=S(6), pady=S(2)).grid(
                row=row, column=col, sticky="e", padx=(0, S(10)), pady=S(3))
            tk.Label(grid, text=label, font=F.small, fg=T.TEXT_2, bg=T.BG, anchor="w").grid(
                row=row, column=col + 1, sticky="w", padx=(0, S(24)))
        foot = tk.Frame(dlg, bg=T.BG_2, padx=S(24), pady=S(10))
        foot.pack(side="bottom", fill="x")
        Button(foot, text="Listo", variant="primary", command=dlg.close).pack(side="right")
        dlg.show()

    # ── Estado general ─────────────────────────────────────────────────────
    def _volume_changed(self, v):
        if self.audio is not None:
            self.audio.set_volume(v)

    def set_tool(self, tool):
        self.tool = tool
        self.tool_seg.set(tool)
        self.timeline._guide = None
        self.timeline.redraw()

    def set_snap(self, v):
        self.snap = bool(v)
        self.snap_btn.set(self.snap)
        self.app.settings.set("snap", self.snap)

    def set_loop(self, v):
        self.loop = bool(v)
        self.loop_btn.set(self.loop)
        self.app.settings.set("loop", self.loop)
        self.timeline.redraw()
        if self.playing:
            self.play(self.pos)

    def loop_range(self):
        seq = self.seq
        clips = [c for c in seq.clips if c.id in self.sel]
        if clips:
            return min(c.start for c in clips), max(c.end for c in clips)
        if self.gap_sel:
            return self.gap_sel
        return 0.0, seq.duration

    def set_selection(self, ids, gap=None, redraw=True):
        self.sel = set(ids)
        self.gap_sel = gap
        self._refresh_ui()
        if redraw:
            self.timeline.redraw()

    def select_all(self):
        self.set_selection({c.id for c in self.seq.clips})

    def _refresh_ui(self):
        seq = self.seq
        fps = seq.fps
        self.time_lbl.configure(text=tc(self.pos, fps))
        self.dur_lbl.configure(text=f"/ {tc(seq.duration, fps)}")
        clips = [c for c in seq.clips if c.id in self.sel]
        if clips:
            span = sum(c.dur for c in clips)
            self.sel_lbl.configure(text=f"{len(clips)} {'clip' if len(clips) == 1 else 'clips'} · {tc(span, fps)}")
        elif self.gap_sel:
            self.sel_lbl.configure(text=f"Hueco · {tc(self.gap_sel[1] - self.gap_sel[0], fps)}")
        else:
            self.sel_lbl.configure(text="")
        has = bool(clips or self.gap_sel)
        self.del_btn.configure_button(enabled=has)
        self.ripple_btn.configure_button(enabled=has)
        self.undo_btn.configure_button(enabled=bool(self._undo))
        self.redo_btn.configure_button(enabled=bool(self._redo))

    # ── Edición (con deshacer) ─────────────────────────────────────────────
    def _state(self):
        return (self.seq.snapshot(), set(self.sel), self.gap_sel)

    def begin_edit(self):
        """Antes de un arrastre: guarda para deshacer y devuelve el estado base."""
        if self.playing:
            self.pause()
        self._edit_base = self._state()
        return self.seq.snapshot()

    def end_edit(self):
        base, self._edit_base = self._edit_base, None
        if base is None:
            return
        before = Sequence()
        before.restore(base[0])
        if before.signature() != self.seq.signature():
            self._push(base)
            self.changed()
        else:
            self.changed(live=True)

    def _push(self, state):
        self._undo.append(state)
        del self._undo[:-100]
        self._redo.clear()

    def _apply(self, fn, keep_sel=False):
        """Ejecuta una edición; si no cambió nada, no queda en el historial."""
        if self.playing:
            self.pause()
        st = self._state()
        sig = (self.seq.signature(), self.seq.mark_in, self.seq.mark_out)
        result = fn()
        if (self.seq.signature(), self.seq.mark_in, self.seq.mark_out) != sig:
            self._push(st)
            if not keep_sel:
                self.sel = {i for i in self.sel if self.seq.get(i)}
                if self.gap_sel and not self._gap_valid(self.gap_sel):
                    self.gap_sel = None
            self.changed()
        return result

    def _gap_valid(self, gap):
        g = self.seq.gap_at((gap[0] + gap[1]) / 2)
        return g is not None and abs(g[0] - gap[0]) < 1e-6 and abs(g[1] - gap[1]) < 1e-6

    def changed(self, live=False):
        seq = self.seq
        self.pos = min(self.pos, seq.duration)
        if seq.has_video:
            self.view.aspect = seq.aspect
        self._refresh_ui()
        self.timeline.redraw()
        if not live:
            self.show_frame()
            if self.on_change:
                self.on_change()

    def _restore(self, st):
        snap, sel, gap = st
        self.seq.restore(snap)
        self.sel, self.gap_sel = set(sel), gap
        self.changed()

    def undo(self):
        if self._undo:
            if self.playing:
                self.pause()
            self._redo.append(self._state())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            if self.playing:
                self.pause()
            self._undo.append(self._state())
            self._restore(self._redo.pop())

    # ── Operaciones ────────────────────────────────────────────────────────
    def add_sources(self, sources, at=None, insert=False):
        """Agrega archivos (ya analizados): al final, o en `at`."""
        first = not self.seq.clips
        added = []

        def run():
            t = at
            for src in sources:
                c = self.seq.add(src, at=t, insert=insert and t is not None)
                added.append(c.id)
                if t is not None:
                    t = c.end
        self._apply(run)
        for src in sources:
            self.media.ensure(src, self.timeline.thumb_height())
        self.sel = set(added)
        if first:
            self._undo.clear()
            self.view.aspect = self.seq.aspect
            self.pos = 0.0
            self.timeline._fitted = False
            self.after(30, self.timeline.fit)
        self.changed()

    def reset(self):
        self.pause()
        self.seq = Sequence()
        self.sel, self.gap_sel = set(), None
        self._undo.clear()
        self._redo.clear()
        self.pos = 0.0
        self.media.reset()
        self.view.set_audio_mode(None)
        self.timeline._fitted = False
        self.changed()

    def razor_at(self, t):
        self._apply(lambda: self.seq.split_at(t), keep_sel=True)

    def add_edit(self):
        """Ctrl+K: corta en el cabezal (los clips elegidos o el que esté debajo)."""
        if self.seq.clip_at(self.pos) is None:
            self.app.toast.show("Poné el cabezal sobre un clip para cortarlo", "info", ms=2500)
            return
        self._apply(lambda: self.seq.split_at(self.pos), keep_sel=True)

    def delete(self, ripple):
        if self.gap_sel:
            gap = self.gap_sel
            self.gap_sel = None
            self._apply(lambda: self.seq.close_gap(*gap))
            return
        ids = {i for i in self.sel if self.seq.get(i)}
        if not ids:
            return
        self._apply(lambda: self.seq.ripple_delete(ids) if ripple else self.seq.lift(ids))
        self.sel = set()
        self._refresh_ui()

    def ripple_trim(self, side):
        res = self._apply(lambda: self.seq.ripple_trim_to(self.pos, side))
        if res is not None:
            self.seek(res)

    def copy(self):
        clips = sorted((c for c in self.seq.clips if c.id in self.sel), key=lambda c: c.start)
        if clips:
            self.clipboard = [c.clone() for c in clips]
            self.app.toast.show(f"{len(clips)} {'clip copiado' if len(clips) == 1 else 'clips copiados'}"
                                " · Ctrl+V para pegar en el cabezal", "info", ms=2200)

    def cut(self):
        if self.sel:
            self.copy()
            self.delete(False)

    def paste(self, insert):
        if not self.clipboard:
            return
        ids = self._apply(lambda: self.seq.paste(self.clipboard, self.pos, insert=insert))
        if ids:
            self.set_selection(set(ids))
            end = max(self.seq.get(i).end for i in ids if self.seq.get(i))
            self.seek(end)

    # ── Menú contextual ────────────────────────────────────────────────────
    def context_menu(self, e, hit, t):
        items = []
        kind = hit[0]
        if kind in ("clip", "edge"):
            c = hit[1]
            items += [
                {"label": "Cortar acá", "icon": G.CUT, "command": lambda: self.razor_at(t)},
                {"label": "Cortar en el cabezal", "hint": "Ctrl+K", "command": self.add_edit,
                 "enabled": self.seq.clip_at(self.pos) is not None},
                {"separator": True},
                {"label": "Borrar", "icon": G.DELETE, "hint": "Supr", "command": lambda: self.delete(False)},
                {"label": "Borrar y cerrar hueco", "hint": "Shift+Supr", "command": lambda: self.delete(True)},
                {"separator": True},
                {"label": "Copiar", "icon": G.COPY, "hint": "Ctrl+C", "command": self.copy},
                {"label": "Mostrar el archivo en la carpeta", "icon": G.FOLDER_OPEN,
                 "command": lambda: reveal_in_explorer(c.src.path)},
            ]
        elif kind == "gap":
            items += [{"label": "Cerrar hueco", "icon": G.DELETE, "hint": "Supr", "command": lambda: self.delete(False)}]
        if self.clipboard:
            items += [{"separator": True}] if items else []
            items += [{"label": "Pegar en el cabezal", "icon": G.PASTE, "hint": "Ctrl+V",
                       "command": lambda: self.paste(False)},
                      {"label": "Pegar insertando", "hint": "Ctrl+Shift+V", "command": lambda: self.paste(True)}]
        if self.on_add:
            items += [{"separator": True}] if items else []
            items.append({"label": "Agregar archivo al final…", "icon": G.ADD, "command": self.on_add})
        if items:
            PopupMenu(self, items, e.x_root, e.y_root, min_width=240)

    # ── Navegación ─────────────────────────────────────────────────────────
    def seek(self, t, final=True, from_timeline=False):
        if self.playing:
            self.pause()
        self.pos = max(0.0, min(self.seq.duration, t))
        self._refresh_ui()
        self.timeline.set_pos(self.pos, follow=not from_timeline)
        self.show_frame()

    def step(self, frames):
        f = self.seq.frame
        self.seek(round(self.pos / f) * f + frames * f)

    def go_edit(self, d):
        pts = self.seq.edit_points()
        if d < 0:
            t = max((p for p in pts if p < self.pos - 1e-6), default=0.0)
        else:
            t = min((p for p in pts if p > self.pos + 1e-6), default=self.seq.duration)
        self.seek(t)

    # ── Visor ──────────────────────────────────────────────────────────────
    def _media_ready(self):
        if self.winfo_exists():
            self.timeline.redraw()

    def _set_audio_card(self, title):
        if title != self._audio_card:
            self._audio_card = title
            self.view.set_audio_mode(title)

    def _black(self):
        w, h = self.view.frame_size()
        self.view.show_rgb(w, h, bytes(w * h * 3))

    def show_frame(self, t=None):
        if self.playing:
            return
        t = self.pos if t is None else t
        c = self.seq.clip_at(min(t, max(0.0, self.seq.duration - 1e-3)))
        self._frame_token += 1
        if c is None or not c.src.has_video:
            self._black()
            self._set_audio_card(c.src.name if c is not None else None)
            return
        self._set_audio_card(None)
        if self.grabber is None:
            return
        w, h = self.view.frame_size()
        src_t = min(c.t_in + (t - c.start), c.t_out - 0.5 / (c.src.fps or 30))
        self.grabber.request(src_t, w, h, path=c.src.path, fit=True, tag=self._frame_token)

    def trim_preview(self, c, side):
        """Durante un recorte muestra el cuadro del borde que se está moviendo."""
        if not c.src.has_video or self.grabber is None:
            return
        self._frame_token += 1
        w, h = self.view.frame_size()
        t = c.t_in if side == "in" else max(c.t_in, c.t_out - 1.0 / (c.src.fps or 30))
        self.grabber.request(t, w, h, path=c.src.path, fit=True, tag=self._frame_token)

    def _grabbed(self, tag, w, h, data):
        if self.winfo_exists() and not self.playing and tag == self._frame_token:
            self.view.show_rgb(w, h, data)

    def _view_resized(self):
        if self.playing:
            self.play(self._clock())
        else:
            self.show_frame()

    # ── Reproducción ───────────────────────────────────────────────────────
    def _clock(self):
        if self.audio is not None and self.audio.ok:
            c = self.audio.clock()
            if c is not None:
                return c
        return self._t0 + (time.monotonic() - self._wall0)

    def toggle_play(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def _pieces(self, a, b, lane):
        out = []
        for s, e, c in self.seq.pieces(a, b):
            ok = c is not None and (c.src.has_video if lane == "v" else c.src.has_audio)
            out.append((s, e, c.src.path if ok else None, (c.t_in + (s - c.start)) if c else 0.0))
        return out

    def play(self, from_t=None):
        seq = self.seq
        if not seq.clips:
            return
        self._stop_streams()
        a, b = self.loop_range() if self.loop else (0.0, seq.duration)
        t = self.pos if from_t is None else from_t
        if self.loop:
            if not (a - 1e-6 <= t < b - 0.04):
                t = a
        elif t >= seq.duration - 0.04:
            t = 0.0
        self.until = b
        if seq.has_video:
            w, h = self.view.frame_size()
            self.video = playback.SeqVideo(self._pieces(t, b, "v"), t, w, h, min(seq.fps, 30.0))
        self.audio = None
        a_out = playback.SeqAudio(self._pieces(t, b, "a"), t, volume=self.vol.effective)
        self.audio = a_out if a_out.ok else None
        self._t0 = t
        self.pos = t
        self.playing = True
        self._waiting = True
        self._wait_since = time.monotonic()
        self.play_btn.configure_button(icon=G_PAUSE)
        self._tick()

    def _tick(self):
        self._tick_job = None
        if not self.playing or not self.winfo_exists():
            return
        try:
            self._tick_body()
        except Exception:  # un error suelto no debe congelar la reproducción
            log.exception("Error en la reproducción")
            if self.playing and self._tick_job is None:
                self._tick_job = self.after(30, self._tick)

    def _restart_video(self, t):
        """Vuelve a arrancar sólo el video en t (el audio sigue sonando)."""
        old = self.video
        if old is not None:
            old.stop()
        w, h = self.view.frame_size()
        self.video = playback.SeqVideo(self._pieces(t, self.until, "v"), t, w, h, min(self.seq.fps, 30.0))
        self._last_frame_at = time.monotonic()

    def _tick_body(self):
        v = self.video
        now = time.monotonic()
        if self._waiting:
            ready_v = v is None or bool(v.frames) or v.eof
            ready_a = self.audio is None or self.audio._primed.is_set()
            if (ready_v and ready_a) or time.monotonic() - self._wait_since > 1.5:
                self._waiting = False
                if self.audio is not None:
                    self.audio.resume()
                self._wall0 = time.monotonic()
                self._last_frame_at = self._last_clock_at = time.monotonic()
                self._last_clock = None
            else:
                self._tick_job = self.after(8, self._tick)
                return
        clock = self._clock()
        # reloj trabado (el audio dejó de avanzar): se reinicia todo en ese punto
        if self._last_clock is None or abs(clock - self._last_clock) > 1e-4:
            self._last_clock, self._last_clock_at = clock, now
        elif now - self._last_clock_at > 1.0 and clock < self.until - 0.2:
            log.info("La reproducción se trabó en %.2f s: se reinicia", clock)
            self.play(clock)
            return
        if v is not None:
            frame = None
            while v.frames and v.frames[0][0] <= clock + 0.5 / v.fps:
                frame = v.frames.popleft()
            if frame is not None:
                self.view.show_rgb(v.w, v.h, frame[1])
                self._last_frame_at = now
            elif now - self._last_frame_at > 0.8 and clock < self.until - 0.3 and \
                    (v.eof or not v.frames or v.frames[0][0] > clock + 1.0):
                # el video dejó de llegar mientras el tiempo sigue: se reanuda en el punto actual
                log.info("El video de la vista previa se trabó en %.2f s: se reanuda", clock)
                self._restart_video(clock)
        c = self.seq.clip_at(clock)
        self._set_audio_card(c.src.name if c is not None and not c.src.has_video else None)
        self.pos = min(clock, self.until)
        self._refresh_time()
        if clock >= self.until - 1e-3:
            if self.loop:
                a, _b = self.loop_range()
                self.play(a)
            else:
                self.pause(at=self.until)
            return
        self._tick_job = self.after(8, self._tick)

    def _refresh_time(self):
        self.time_lbl.configure(text=tc(self.pos, self.seq.fps))
        self.timeline.set_pos(self.pos)

    def pause(self, at=None):
        if self.playing:
            pos = self._clock() if at is None else at
            self._stop_streams()
            self.playing = False
            self.play_btn.configure_button(icon=G_PLAY)
            self.pos = max(0.0, min(self.seq.duration, pos))
            self._refresh_ui()
            self.timeline.set_pos(self.pos)
            self.show_frame()

    def _stop_streams(self):
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        for s in (self.video, self.audio):
            if s is not None:
                s.stop()
        self.video = self.audio = None

    def stop(self):
        self.playing = False
        self._stop_streams()
        self.media.cancel.set()
        if self.grabber:
            self.grabber.close()
            self.grabber = None
        Tooltip.hide()
